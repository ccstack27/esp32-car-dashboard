# OTA safety net. Never replaced by over-the-air updates (USB deploy only).
#
# install() swaps staged files in, keeping the old ones in /ota/old and writing
# /ota/state. The new release gets exactly one boot to prove itself: app.py
# calls confirm() after it has been running for a while. If the board resets
# before that, or main.py catches a crash, rollback() restores the old files.

import json
import os

STATE = "/ota/state"
OLD = "/ota/old"


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def makedirs(path):
    """Create the parent directories of a file path."""
    cur = ""
    for part in path.strip("/").split("/")[:-1]:
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


def rmtree(path):
    try:
        entries = list(os.ilistdir(path))
    except OSError:
        return
    for name, kind, *_ in entries:
        child = path + "/" + name
        if kind == 0x4000:
            rmtree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_state(s):
    makedirs(STATE)
    with open(STATE, "w") as f:
        json.dump(s, f)


def install(paths, staged):
    """Move staged/<path> over each path, keeping backups for rollback."""
    rmtree(OLD)
    had = [p for p in paths if exists(p)]
    write_state({"phase": "swap", "files": paths, "had": had, "boots": 0})
    for p in paths:
        if exists(p):
            makedirs(OLD + p)
            os.rename(p, OLD + p)
        makedirs(p)
        os.rename(staged + p, p)
    write_state({"phase": "trial", "files": paths, "had": had, "boots": 0})
    rmtree(staged)


def rollback():
    s = read_state()
    if not s:
        return False
    for p in s["files"]:
        if exists(OLD + p):
            if exists(p):
                os.remove(p)
            os.rename(OLD + p, p)
        elif p not in s["had"] and exists(p):
            os.remove(p)  # file only existed in the failed release
    os.remove(STATE)
    rmtree(OLD)
    print("OTA: update failed, previous version restored")
    return True


def confirm():
    if read_state():
        os.remove(STATE)
        rmtree(OLD)
        print("OTA: new version confirmed")


def on_boot():
    s = read_state()
    if not s:
        return
    if s["phase"] != "trial" or s["boots"] >= 1:
        rollback()  # interrupted swap, or the trial boot never confirmed
    else:
        s["boots"] = 1
        write_state(s)
