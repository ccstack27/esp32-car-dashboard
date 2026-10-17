"""Upload the dashboard to the ESP32-S3 over the serial REPL.

    py tools/deploy.py            # default port COM18
    py tools/deploy.py COM5
    py tools/deploy.py COM18 --no-media   # skip intro.mp4 / bg.jpg (fast)

mpremote toggles DTR/RTS when it opens the port, which resets this board and
races the running server, so this script talks to the raw REPL directly with
DTR/RTS held low. It gzips index.html, uploads everything (including the USB-only
boot.py / main.py / recovery.py / config.py), then soft-reboots.
"""
import binascii
import json
import pathlib
import sys
import time

import serial

ROOT = pathlib.Path(__file__).resolve().parent.parent / "device"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = ARGS[0] if ARGS else "COM18"
NO_MEDIA = "--no-media" in sys.argv


class Board:
    def __init__(self, port):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, 115200, 0.2
        self.s.dtr = self.s.rts = False  # don't pulse EN/IO0
        self.s.open()

    def read_until(self, token, timeout=10):
        buf, end = b"", time.time() + timeout
        while not buf.endswith(token):
            if time.time() > end:
                raise TimeoutError("waiting for {!r}, got {!r}".format(token, buf[-200:]))
            buf += self.s.read(1)
        return buf

    def enter_raw(self):
        for _ in range(4):  # interrupt the running server
            self.s.write(b"\r\x03")
            time.sleep(0.15)
        self.s.reset_input_buffer()
        self.s.write(b"\r\x01")
        self.read_until(b"raw REPL; CTRL-B to exit\r\n>")

    def exec(self, code, timeout=15):
        data = code.encode()
        for i in range(0, len(data), 256):
            self.s.write(data[i:i + 256])
            time.sleep(0.01)
        self.s.write(b"\x04")
        if self.s.read(2) != b"OK":
            raise RuntimeError("raw REPL did not accept code")
        out = self.read_until(b"\x04", timeout)[:-1]
        err = self.read_until(b"\x04", timeout)[:-1]
        self.read_until(b">")
        if err:
            raise RuntimeError(err.decode(errors="replace"))
        return out.decode(errors="replace")

    def put(self, data, remote):
        self.exec("f=open({!r},'wb');w=f.write;from binascii import a2b_base64 as d".format(remote))
        step = 2048
        for i in range(0, len(data), step):
            b64 = binascii.b2a_base64(data[i:i + step], newline=False).decode()
            self.exec("w(d({!r}))".format(b64))
            done = min(i + step, len(data))
            print("\r  {:<20} {:>4}%".format(remote, done * 100 // len(data)), end="", flush=True)
        self.exec("f.close()")
        print()

    def soft_reboot(self):
        self.s.write(b"\x02\x04")  # leave raw REPL, soft reset -> main.py runs
        time.sleep(3)
        print(self.s.read(4000).decode(errors="replace"))


def main():
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from release import OTA_FILES, REPO, gzip_html, sha256

    ota = {t: gzip_html() if t == "/www/index.html.gz" else (REPO / src).read_bytes()
           for t, src in OTA_FILES.items()}
    # Report the release version only if these exact files were released.
    version = "dev"
    manifest = REPO / "release" / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_bytes())
        if all(m["files"].get(t, {}).get("sha256") == sha256(d) for t, d in ota.items()):
            version = m["version"]

    files = list(ota.items()) + [
        ("/recovery.py", (ROOT / "recovery.py").read_bytes()),
        ("/boot.py", (ROOT / "boot.py").read_bytes()),
        ("/config.py", (ROOT / "config.py").read_bytes()),
        ("/version.txt", version.encode()),
        ("/main.py", (ROOT / "main.py").read_bytes()),  # last, so a half-done upload never boots
    ]
    if NO_MEDIA:
        files = [f for f in files if not f[0].endswith((".mp4", ".jpg"))]
    b = Board(PORT)
    b.enter_raw()
    print("Connected:", b.exec("import os,sys;print(sys.version)").strip())
    b.exec(  # drop the old launcher and any pending OTA state so it can't roll this deploy back
        "import os\n"
        "def rm(p):\n"
        "  try:\n"
        "    for e in os.ilistdir(p):\n"
        "      rm(p+'/'+e[0]) if e[1]==0x4000 else os.remove(p+'/'+e[0])\n"
        "    os.rmdir(p)\n"
        "  except OSError: pass\n"
        "rm('/ota')\n"
        "for n in ('old_main.py','main.py'):\n"
        "  try: os.remove(n)\n"
        "  except OSError: pass\n"
        "try: os.mkdir('/www')\n"
        "except OSError: pass\n"
    )
    for remote, data in files:
        b.put(data, remote)
    print(b.exec("import os;print(os.listdir('/'),os.listdir('/www'))"))
    print("Deployed version:", version)
    print("Rebooting...")
    b.soft_reboot()


if __name__ == "__main__":
    main()