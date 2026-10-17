# Over-the-air updater. Joins a Wi-Fi network on request (credentials are kept
# in RAM only), downloads the signed release published by tools/release.py,
# verifies every file, then hands the swap to recovery.py and reboots.
#
# Trust comes from the HMAC signature on manifest.json (key in config.py, never
# on GitHub), so TLS certificate checking is not needed for authenticity.

import asyncio
import gc
import hashlib
import json
import machine
import network
import ssl
import time
from binascii import hexlify

import config
import recovery

STAGED = "/ota/new"
VERSION_FILE = "/version.txt"
PROTECTED = ("/boot.py", "/main.py", "/recovery.py", "/config.py")  # USB only

status = {"stage": "idle", "msg": "", "ssid": "", "latest": "", "files": 0, "total": 0, "done": 0}
_task = None
_plan = None  # (manifest, [paths that differ from what is installed])


def version():
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def busy():
    return status["stage"] in ("downloading", "installing", "rebooting")


def _set(stage, msg="", **kw):
    status["stage"] = stage
    status["msg"] = msg
    status.update(kw)


def _sta():
    return network.WLAN(network.STA_IF)


def _disconnect():
    sta = _sta()
    try:
        sta.disconnect()
    except OSError:
        pass
    sta.active(False)


def _fail(msg):
    _set("error", msg)
    _disconnect()


def _err(e):
    return str(e) or type(e).__name__


def _hmac(key, msg):
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key += b"\x00" * (64 - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in key) + inner).digest()


async def _sha_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                h.update(chunk)
                await asyncio.sleep_ms(0)
    except OSError:
        return None
    return hexlify(h.digest()).decode()


async def _get(url, sink=None):
    """HTTPS GET. Streams the body to sink(chunk), or returns it if no sink."""
    host, _, path = url[8:].partition("/")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_NONE
    gc.collect()
    r, w = await asyncio.wait_for(asyncio.open_connection(host, 443, ssl=ctx), 20)
    try:
        w.write("GET /{} HTTP/1.0\r\nHost: {}\r\nUser-Agent: esp32-dashboard\r\n\r\n".format(path, host))
        await asyncio.wait_for(w.drain(), 20)  # TLS handshake happens here
        line = await asyncio.wait_for(r.readline(), 20)
        code = int(line.split()[1])
        while (await asyncio.wait_for(r.readline(), 20)) not in (b"\r\n", b"\n", b""):
            pass
        if code != 200:
            raise OSError("GitHub returned HTTP {} for {}".format(code, path.split("?")[0]))
        body = b""
        while True:
            chunk = await asyncio.wait_for(r.read(2048), 20)
            if not chunk:
                return body
            if sink:
                sink(chunk)
            else:
                body += chunk
                if len(body) > 16384:
                    raise ValueError("manifest too large")
    finally:
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass


# --- Steps driven by the dashboard -------------------------------------------

def scan():
    """Blocking (~3 s). Returns nearby networks, strongest first."""
    sta = _sta()
    sta.active(True)
    found = {}
    for ssid, _bssid, _ch, rssi, auth, _hidden in sta.scan():
        try:
            name = ssid.decode()
        except UnicodeError:
            continue
        if name and (name not in found or rssi > found[name]["rssi"]):
            found[name] = {"ssid": name, "rssi": rssi, "lock": auth != 0}
    return sorted(found.values(), key=lambda n: -n["rssi"])


def start(ssid, password):
    global _task
    cancel()
    _set("connecting", "Connecting to " + ssid, ssid=ssid, latest="", files=0, total=0, done=0)
    _task = asyncio.create_task(_connect_and_check(ssid, password))


def install(all_off):
    global _task
    if status["stage"] != "available" or not _plan:
        raise ValueError("No update ready to install")
    all_off()
    _task = asyncio.create_task(_install())


def cancel():
    global _task, _plan
    if busy():
        return False
    if _task:
        _task.cancel()
        _task = None
    _plan = None
    _disconnect()
    _set("idle")
    return True


async def _connect_and_check(ssid, password):
    global _plan
    sta = _sta()
    sta.active(True)
    sta.connect(ssid, password)
    password = None
    deadline = time.ticks_add(time.ticks_ms(), 20000)
    while not sta.isconnected():
        st = sta.status()
        if st == network.STAT_WRONG_PASSWORD:
            return _fail("Wrong Wi-Fi password")
        if time.ticks_diff(deadline, time.ticks_ms()) < 0:
            if st == network.STAT_NO_AP_FOUND:
                return _fail("Couldn't find " + ssid + " - is it in range?")
            return _fail("Couldn't connect to " + ssid)
        await asyncio.sleep_ms(250)

    _set("checking", "Checking GitHub for updates")
    base = config.UPDATE_URL
    bust = "?t={}".format(time.ticks_ms())  # dodge GitHub's raw-file cache where possible
    try:
        raw = await _get(base + "manifest.json" + bust)
        sig = (await _get(base + "manifest.sig" + bust)).strip().decode()
    except Exception as e:
        return _fail("Couldn't reach the update server: " + _err(e))
    if hexlify(_hmac(config.UPDATE_KEY.encode(), raw)).decode() != sig:
        return _fail("Release signature didn't match. If you just pushed, GitHub may take "
                     "up to 5 minutes to serve the new files - try again shortly.")

    man = json.loads(raw)
    todo, total = [], 0
    for path, info in man["files"].items():
        if path in PROTECTED or not path.startswith("/") or ".." in path:
            return _fail("Release tries to replace a protected file: " + path)
        if await _sha_file(path) != info["sha256"]:
            todo.append(path)
            total += info["size"]
    if not todo and man["version"] == version():
        return _set("uptodate", "Already on the latest version", latest=man["version"])  # leaves Wi-Fi to cancel()
    _plan = (man, todo)
    _set("available", "", latest=man["version"], files=len(todo), total=total, done=0)


async def _install():
    man, todo = _plan
    base = config.UPDATE_URL
    _set("downloading", "Starting download", done=0)
    recovery.rmtree(STAGED)
    try:
        for path in todo:
            info = man["files"][path]
            status["msg"] = "Downloading " + path.split("/")[-1]
            dest = STAGED + path
            recovery.makedirs(dest)
            h = hashlib.sha256()
            with open(dest, "wb") as f:
                def sink(chunk):
                    f.write(chunk)
                    h.update(chunk)
                    status["done"] += len(chunk)
                await _get(base + info["src"], sink)
            if hexlify(h.digest()).decode() != info["sha256"]:
                raise ValueError(path + " doesn't match the release. GitHub may still be "
                                 "updating its cache - try again in a few minutes.")
        recovery.makedirs(STAGED + VERSION_FILE)
        with open(STAGED + VERSION_FILE, "w") as f:
            f.write(man["version"])
    except Exception as e:
        recovery.rmtree(STAGED)
        return _fail("Download failed: " + _err(e))

    _set("installing", "Installing")
    _disconnect()
    try:
        recovery.install(todo + [VERSION_FILE], STAGED)
    except Exception as e:
        recovery.rollback()
        return _fail("Install failed, kept the current version: " + _err(e))
    _set("rebooting", "Restarting with v" + man["version"])
    await asyncio.sleep_ms(1500)  # let the phone see the final status
    machine.reset()
