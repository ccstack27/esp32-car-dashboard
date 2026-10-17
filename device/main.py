# Stable launcher (USB deploy only). The dashboard itself lives in app.py so it
# can be updated over the air; if a freshly installed app.py crashes, roll back.
import sys

import recovery

try:
    import app

    app.run()
except Exception as e:
    sys.print_exception(e)
    if recovery.read_state():
        recovery.rollback()
        import machine

        machine.reset()
