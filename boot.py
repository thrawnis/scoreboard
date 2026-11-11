# Runs once at power-up, before USB is set up.
#
# Normal use: CircuitPython owns the flash so the scoreboard can save games.
# The CIRCUITPY drive is then read-only on the computer.
#
# Dev mode: if /devmode exists, the drive stays writable over USB (for
# deploying code) and the scoreboard can't save. Serial command `devmode`
# creates the file; delete it from the computer and restart to go back.

import os

import storage

try:
    os.stat("/devmode")
except OSError:
    storage.remount("/", readonly=False)
