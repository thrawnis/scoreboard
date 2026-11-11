# Keeps one stalled web client from freezing the whole board.
#
# adafruit_httpserver sends each reply in a loop that waits for the client to take the data. If a phone
# stops reading in the middle of a reply (it joined a Wi-Fi network with no internet and switched to
# cellular, the screen locked, Safari was backgrounded...) that loop can wait forever. Everything here runs
# in one loop, so the display, the serial console and the web server all freeze until the board is reset.
# This replaces the library's send routine with one that gives up after a few seconds without progress.

import time
from errno import EAGAIN, ECONNRESET, ENOTCONN, ETIMEDOUT

from adafruit_httpserver import response as _response

STALL_SECONDS = 4  # no bytes accepted for this long: drop the reply


def _send_bytes(self, conn, buffer):
    view = memoryview(buffer)
    sent = 0
    last = time.monotonic()
    while sent < len(view):
        try:
            n = conn.send(view[sent:sent + 2048])
        except OSError as exc:
            if exc.errno in (ECONNRESET, ENOTCONN):
                return  # the client went away
            if exc.errno not in (EAGAIN, ETIMEDOUT):
                raise
            n = 0
        if n:
            sent += n
            last = time.monotonic()
        elif time.monotonic() - last > STALL_SECONDS:
            return  # the client stopped reading: give up on this reply
        else:
            time.sleep(0.01)


_response.Response._send_bytes = _send_bytes
