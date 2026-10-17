"""Build and sign an over-the-air release into release/.

    py tools/release.py 1.1.0

Then commit and push; the dashboard's "Update Firmware" button pulls it from
GitHub. The board only accepts releases signed with UPDATE_KEY from
device/config.py, which stays on this PC and the board.
"""
import gzip
import hashlib
import hmac
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEVICE = REPO / "device"
RELEASE = REPO / "release"

# Board path -> file in the repo. Only these travel over the air; boot.py,
# main.py, recovery.py and config.py are USB-only on purpose.
OTA_FILES = {
    "/app.py": "device/app.py",
    "/updater.py": "device/updater.py",
    "/www/index.html.gz": "release/index.html.gz",
    "/www/intro.mp4": "device/www/intro.mp4",
    "/www/bg.jpg": "device/www/bg.jpg",
}


def gzip_html():
    """Deterministic gzip of the web page (same bytes every build)."""
    return gzip.compress((DEVICE / "www" / "index.html").read_bytes(), 9, mtime=0)


def load_config():
    cfg = {}
    exec((DEVICE / "config.py").read_text(), cfg)
    return cfg


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: py tools/release.py <version>   e.g. 1.1.0")
    version = sys.argv[1].lstrip("v")
    key = load_config()["UPDATE_KEY"].encode()

    RELEASE.mkdir(exist_ok=True)
    (RELEASE / "index.html.gz").write_bytes(gzip_html())

    files = {}
    for target, src in OTA_FILES.items():
        data = (REPO / src).read_bytes()
        files[target] = {"src": src, "sha256": sha256(data), "size": len(data)}

    manifest = json.dumps({"version": version, "files": files}, indent=1).encode()
    (RELEASE / "manifest.json").write_bytes(manifest)
    (RELEASE / "manifest.sig").write_text(hmac.new(key, manifest, hashlib.sha256).hexdigest())

    print("Release v{} built and signed:".format(version))
    for target, info in files.items():
        print("  {:<20} {:>8} bytes  {}".format(target, info["size"], info["sha256"][:12]))
    print("\nPublish it:\n  git add -A\n  git commit -m \"Release v{0}\"\n  git push".format(version))


if __name__ == "__main__":
    main()
