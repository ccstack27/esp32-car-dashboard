# Project Log — ESP32-S3 Car Dashboard

## 2026-10-17 — Initial build and deploy

**Goal:** Web dashboard served by the ESP32-S3 to switch car loads through a relay module.

### Done
- Probed the board on COM18: MicroPython v1.27.0, 16 MB flash (~14.6 MB free), no PSRAM (boot log shows harmless PSRAM errors), ~150 KB free RAM.
- Found an old Thonny program (AP "Misyubibi Lancer", active-high relays). Wiped it as allowed; kept the same SSID and password.
- Wrote `device/main.py`: Wi-Fi access point + asyncio HTTP server + relay control.
  - IO4 ignition (toggle), IO5 crank (3 s pulse, needs ignition on, tap again to abort, ignition-off cancels it), IO6 horn/alarm (hold-to-sound, auto-off after 0.7 s without keep-alive), IO7 park light (toggle), IO15–IO18 AUX 1–4 (toggles).
  - All relays driven OFF at boot and on shutdown; server socket released on Ctrl-C.
  - Serves gzipped HTML and supports HTTP Range requests (needed by iOS video playback).
- Wrote `device/www/index.html`: glassmorphism UI in the video's cyan HUD style. Tap-to-start gate → intro video with sound → controls fade in over a blurred last frame. `/#dash` skips the intro. Multiple phones stay in sync (1.5 s polling).
- Compressed `dashboard_animation.mp4` (4.0 MB → 885 KB, 960×540) into `device/www/intro.mp4`; extracted the last frame as `device/www/bg.jpg`.
- Wrote `tools/deploy.py` (pyserial raw-REPL uploader). mpremote can't be used: opening COM18 toggles DTR/RTS and resets the board.
- Previewed the UI with a local mock server + headless Edge (desktop and phone widths); fixed a grid overflow and an idle progress-ring dot.
- On-device loopback self-test passed: API state/409/400/404, gzip page, images, video full + range requests; RAM steady.

### Not yet tested
- Opening the dashboard from a real phone over the AP.
- Actual relay switching (deliberately skipped in case the car was wired).
- Whether IO15–IO18 relays are active-high like IO4–IO7 (assumed yes).

### Next steps
- Connect a phone to "Misyubibi Lancer", open http://192.168.4.1, test the intro and every control.
- Bench-test IO5 with a test light to confirm the 3 s crank release before connecting the starter.

## 2026-10-17 (later) — Over-the-air firmware updates

### Done
- Added an "Update Firmware" button + glass update sheet: PIN → Wi-Fi scan/pick + password (asked every time, never stored) → check GitHub → install with progress → auto-reload.
- Split the firmware: USB-only core (`boot.py`, `main.py` launcher, `recovery.py`, `config.py`) and OTA-updatable files (`app.py` = former `main.py`, `updater.py`, web files).
- Releases are signed: `tools/release.py <version>` writes `release/manifest.json` + HMAC-SHA256 `manifest.sig` using `UPDATE_KEY` from the gitignored `device/config.py`. The board rejects unsigned/tampered releases and verifies every file's sha256.
- Safe install: downloads to `/ota/new`, swaps with backups in `/ota/old`, new version must run 20 s to be kept; crash or unconfirmed reboot → automatic rollback. Only changed files are downloaded.
- Update requires ignition off and the PIN; relays switched off and controls locked during install.
- Secrets moved out of code into `device/config.py` (`config.example.py` is the public template). Repo prepared for GitHub (`.gitignore`, `.gitattributes`).

### Tested
- UI states previewed with a mock server (network list, update available, downloading, error).
- On the board: HMAC matches the PC tool; install/rollback/confirm/power-loss-mid-swap all restore correctly; TLS streams don't block the server and time out properly; update API (status, wrong PIN, real scan, install refusal).

### Not yet tested
- A real update end to end from GitHub (needs the repo pushed and a Wi-Fi network with internet).
- Everything from the earlier session's list (phone over AP, real relay switching).

## 2026-10-17 (later) — GitHub connected

- Repo: https://github.com/ccstack27/esp32-car-dashboard (public). Pushed release v1.0.0.
- Set `UPDATE_URL` in `device/config.py` and USB-deployed; the board now reports firmware v1.0.0.
- Verified from the PC that GitHub serves a correctly signed manifest and byte-identical files (all sha256 match).
- Next: on a phone, Update Firmware → PIN → Wi-Fi should report "up to date (v1.0.0)"; then release v1.0.1 to test a real install.
