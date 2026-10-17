# ESP32-S3 car dashboard - Wi-Fi access point + web UI + relay control.
# Updatable over the air (see updater.py); started by main.py.
#
#   IO4  Ignition      toggle
#   IO5  Engine crank  momentary, 3 s pulse (only while ignition is ON)
#   IO6  Horn / alarm  hold-to-sound (auto-off if the phone stops pinging)
#   IO7  Park light    toggle
#   IO15-IO18          AUX 1-4 toggles (reserved for future use)

from machine import Pin
import asyncio
import gc
import json
import network
import time

import config
import recovery
import updater

# --- Relays: drive every output OFF before anything else so nothing clicks on boot.
ACTIVE_LOW = False  # relay board triggers on HIGH
OFF = 1 if ACTIVE_LOW else 0

PINS = {
    "ign": 4, "crank": 5, "horn": 6, "park": 7,
    "aux1": 15, "aux2": 16, "aux3": 17, "aux4": 18,
}
TOGGLES = ("ign", "park", "aux1", "aux2", "aux3", "aux4")

relay = {k: Pin(n, Pin.OUT, value=OFF) for k, n in PINS.items()}
state = {k: False for k in PINS}

WWW = "/www/"
CRANK_MS = 3000
HORN_TIMEOUT_MS = 700  # horn drops if no keep-alive within this window
CONFIRM_AFTER_S = 20   # a freshly updated app must run this long before it is kept

VERSION = updater.version()
crank_task = None
crank_end = 0
horn_ping = 0
boot = time.ticks_ms()
server = None


def set_relay(key, on):
    on = bool(on)
    relay[key].value((not on) if ACTIVE_LOW else on)
    if state[key] != on:
        state[key] = on
        print("{} -> {}".format(key, "ON" if on else "OFF"))


def all_off():
    stop_crank()
    for k in relay:
        set_relay(k, False)


def snapshot():
    s = {k: int(v) for k, v in state.items()}
    s["crank_left"] = max(0, time.ticks_diff(crank_end, time.ticks_ms())) if state["crank"] else 0
    s["crank_ms"] = CRANK_MS
    s["up"] = time.ticks_diff(time.ticks_ms(), boot) // 1000
    s["ver"] = VERSION
    return s


crank_gen = 0  # bumps on every start/stop so a stale pulse never touches a newer one


async def crank_pulse(gen):
    global crank_task
    try:
        await asyncio.sleep_ms(CRANK_MS)
    finally:
        if gen == crank_gen:
            set_relay("crank", False)
            crank_task = None


def start_crank():
    global crank_task, crank_end, crank_gen
    crank_gen += 1
    crank_end = time.ticks_add(time.ticks_ms(), CRANK_MS)
    set_relay("crank", True)
    crank_task = asyncio.create_task(crank_pulse(crank_gen))


def stop_crank():
    global crank_task, crank_gen
    crank_gen += 1
    if crank_task:
        crank_task.cancel()
        crank_task = None
    set_relay("crank", False)


async def horn_watchdog():
    while True:
        if state["horn"] and time.ticks_diff(time.ticks_ms(), horn_ping) > HORN_TIMEOUT_MS:
            set_relay("horn", False)
        await asyncio.sleep_ms(100)


async def confirm_update():
    await asyncio.sleep(CONFIRM_AFTER_S)
    recovery.confirm()


# --- API --------------------------------------------------------------------

pin_fails = 0
pin_lock = 0


def check_pin(pin):
    global pin_fails, pin_lock
    if time.ticks_diff(pin_lock, time.ticks_ms()) > 0:
        return "Too many wrong PINs - wait a minute"
    if pin == config.UPDATE_PIN:
        pin_fails = 0
        return None
    pin_fails += 1
    if pin_fails >= 5:
        pin_fails = 0
        pin_lock = time.ticks_add(time.ticks_ms(), 60000)
    return "Wrong PIN"


def update_status():
    s = dict(updater.status)
    s["version"] = VERSION
    return s


def api_update(cmd, body):
    if cmd == "status":
        return 200, update_status()
    if cmd == "cancel":
        if not updater.cancel():
            return 409, {"error": "Installing - can't cancel now"}
        return 200, update_status()

    err = check_pin(body.get("pin"))
    if err:
        return 403, {"error": err}
    if state["ign"] or state["crank"]:
        return 409, {"error": "Turn the ignition off before updating"}
    if updater.busy():
        return 409, {"error": "An update is already installing"}

    if cmd == "scan":
        set_relay("horn", False)  # scanning blocks the event loop for ~3 s
        return 200, {"nets": updater.scan()}
    if cmd == "connect":
        if not body.get("ssid"):
            return 400, {"error": "Choose a Wi-Fi network"}
        updater.start(body["ssid"], body.get("password", ""))
        return 200, update_status()
    if cmd == "install":
        if recovery.read_state():
            return 409, {"error": "The last update is still being verified - try again in a few seconds"}
        try:
            updater.install(all_off)
        except ValueError as e:
            return 409, {"error": str(e)}
        return 200, update_status()
    return 404, {"error": "unknown command"}


def api(cmd, q, body):
    """Returns (status, body)."""
    global horn_ping
    if cmd.startswith("update/"):
        return api_update(cmd[7:], body)
    if cmd != "state" and updater.busy():
        return 423, {"error": "Firmware update in progress"}
    if cmd == "state":
        pass
    elif cmd == "set":
        key = q.get("id")
        if key not in TOGGLES:
            return 400, {"error": "unknown switch"}
        on = q.get("on") == "1"
        set_relay(key, on)
        if key == "ign" and not on:
            stop_crank()
    elif cmd == "crank":
        if q.get("stop") == "1":
            stop_crank()
        elif not state["ign"]:
            return 409, {"error": "Turn the ignition on first"}
        elif not crank_task:
            start_crank()
    elif cmd == "horn":
        if q.get("on") == "1":
            horn_ping = time.ticks_ms()
            set_relay("horn", True)
        else:
            set_relay("horn", False)
    else:
        return 404, {"error": "unknown command"}
    return 200, snapshot()


# --- HTTP server ------------------------------------------------------------

STATIC = {
    "/": ("index.html.gz", "text/html; charset=utf-8", True, "no-cache"),
    "/index.html": ("index.html.gz", "text/html; charset=utf-8", True, "no-cache"),
    "/intro.mp4": ("intro.mp4", "video/mp4", False, "max-age=86400"),
    "/bg.jpg": ("bg.jpg", "image/jpeg", False, "max-age=86400"),
}
REASON = {200: "OK", 206: "Partial Content", 400: "Bad Request", 403: "Forbidden",
          404: "Not Found", 409: "Conflict", 416: "Range Not Satisfiable", 423: "Locked"}
CHUNK = 4096


def parse_qs(qs):
    out = {}
    for part in qs.split("&"):
        if part:
            k, _, v = part.partition("=")
            out[k] = v
    return out


async def send_head(w, status, ctype, length, extra=""):
    w.write("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n{}Connection: close\r\n\r\n"
            .format(status, REASON.get(status, ""), ctype, length, extra))
    await w.drain()


async def send_json(w, status, obj):
    body = json.dumps(obj)
    await send_head(w, status, "application/json", len(body), "Cache-Control: no-store\r\n")
    w.write(body)
    await w.drain()


async def send_file(w, path, headers):
    name, ctype, gz, cache = STATIC[path]
    try:
        f = open(WWW + name, "rb")
    except OSError:
        await send_json(w, 404, {"error": "missing " + name})
        return
    try:
        size = f.seek(0, 2)
        start, end, status = 0, size - 1, 200
        extra = "Accept-Ranges: bytes\r\nCache-Control: {}\r\n".format(cache)
        if gz:
            extra += "Content-Encoding: gzip\r\n"
        rng = headers.get("range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            else:  # suffix range: last N bytes
                start = max(0, size - int(b))
            if start > end:
                await send_head(w, 416, ctype, 0, "Content-Range: bytes */{}\r\n".format(size))
                return
            status = 206
            extra += "Content-Range: bytes {}-{}/{}\r\n".format(start, end, size)
        left = end - start + 1
        await send_head(w, status, ctype, left, extra)
        f.seek(start)
        while left > 0:
            data = f.read(min(CHUNK, left))
            if not data:
                break
            w.write(data)
            await w.drain()
            left -= len(data)
    finally:
        f.close()


async def handle(r, w):
    try:
        line = await asyncio.wait_for(r.readline(), 5)
        if not line:
            return
        method, target, _ = line.decode().split(" ", 2)
        headers = {}
        while True:
            h = await asyncio.wait_for(r.readline(), 5)
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode().partition(":")
            headers[k.strip().lower()] = v.strip()
        body = {}
        length = int(headers.get("content-length") or 0)
        if 0 < length <= 2048:
            try:
                body = json.loads(await asyncio.wait_for(r.readexactly(length), 5))
            except ValueError:
                pass
        path, _, qs = target.partition("?")
        if path.startswith("/api/"):
            status, resp = api(path[5:], parse_qs(qs), body)
            await send_json(w, status, resp)
        elif path in STATIC and method in ("GET", "HEAD"):
            await send_file(w, path, headers)
        else:
            await send_json(w, 404, {"error": "not found"})
    except Exception as e:  # client hung up mid-stream, timeouts, bad requests
        if not isinstance(e, (OSError, asyncio.TimeoutError)):
            print("request error:", repr(e))
    finally:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
        gc.collect()


def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=config.AP_SSID, password=config.AP_PASSWORD, authmode=network.AUTH_WPA2_PSK)
    while not ap.active():
        time.sleep_ms(100)
    print("Wi-Fi '{}' -> open http://{}  (firmware {})".format(config.AP_SSID, ap.ifconfig()[0], VERSION))


async def main():
    global server
    start_ap()
    asyncio.create_task(horn_watchdog())
    asyncio.create_task(confirm_update())
    server = await asyncio.start_server(handle, "0.0.0.0", 80, backlog=8)
    print("Dashboard server running on port 80")
    while True:
        await asyncio.sleep(3600)


def run():
    try:
        asyncio.run(main())
    finally:  # Ctrl-C / crash: release the port and drop every relay
        if server:
            server.close()
            asyncio.run(server.wait_closed())  # let the cancel run so the socket closes
        for k in relay:
            set_relay(k, False)
        asyncio.new_event_loop()
