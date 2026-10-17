# Copy to device/config.py and fill in. config.py is NOT on GitHub (.gitignore) and never
# replaced by over-the-air updates - change it over USB with tools/deploy.py.

# Dashboard Wi-Fi access point
AP_SSID = "Misyubibi Lancer"
AP_PASSWORD = "change-me-8-chars-min"

# PIN required in the dashboard before an update can start
UPDATE_PIN = "0000"

# Where releases are published (raw GitHub URL of the repo's release/ folder)
UPDATE_URL = "https://raw.githubusercontent.com/YOUR_GITHUB_USER/esp32-car-dashboard/main/release/"

# Signing key shared by this board and tools/release.py. Anyone with it can
# publish firmware this board will accept - keep it private.
# Generate one with:  py -c "import secrets;print(secrets.token_hex(32))"
UPDATE_KEY = "paste-64-hex-characters-here"