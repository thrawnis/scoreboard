# Pickleball scoreboard: Raspberry Pi Pico 2 W + Seengreat adapter + 2x 64x32 HUB75.
#
# Phone/watch control over Wi-Fi (web app in /www, API in scoreapp.py).
# Serial console on /dev/ttyACM0: type `help`.
#
# Wi-Fi: joins the network saved from the web app (/data/wifi.json) or
# SCOREBOARD_WIFI_SSID/PASSWORD in settings.toml; otherwise it starts its own
# network, SCOREBOARD_AP_SSID / SCOREBOARD_AP_PASSWORD (default Scoreboard / pickleball).

import json
import os
import sys
import time

import board
import displayio
import framebufferio
import microcontroller
import rgbmatrix
import rtc
import supervisor

from panel import make_panel

# ---------------------------------------------------------------- display

PINOUTS = {
    # Seengreat RGB Matrix Adapter Board, revision V3.9 and later
    "seengreat_v39": dict(
        rgb_pins=[board.GP2, board.GP3, board.GP4, board.GP5, board.GP6, board.GP7],
        addr_pins=[board.GP8, board.GP9, board.GP10, board.GP11],
        clock_pin=board.GP13,
        latch_pin=board.GP14,
        output_enable_pin=board.GP15,
    ),
    # Seengreat V3.8 and earlier; also Adafruit's "RGB Matrix on Pico" wiring
    "seengreat_v38": dict(
        rgb_pins=[board.GP2, board.GP3, board.GP4, board.GP5, board.GP8, board.GP9],
        addr_pins=[board.GP10, board.GP16, board.GP18, board.GP20],
        clock_pin=board.GP11,
        latch_pin=board.GP12,
        output_enable_pin=board.GP13,
    ),
}
PINOUT = "seengreat_v39"


def _meta():
    try:
        with open("/data/meta.json") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return {}


# 1 or 2 chained 64x32 panels; chosen in the web app's Settings (or serial `panels N`).
# Two panels are stacked into a 64x64 board (team A on the top panel, team B below).
_m = _meta()
STACK = _m.get("panels") != 1
# Serpentine: the bottom panel is mounted upside-down (short cable from the top panel's
# output to its input). Turn off if both panels are upright (Settings, or serial `flip`).
SERPENTINE = _m.get("serpentine", True)

WIDTH = 64  # layout width
HEIGHT = 64 if STACK else 32
# One-panel mode always drives the full 128-pixel chain: the 64-wide layout is drawn on
# the left half and copied to the right half, so a second panel left connected
# shows the same picture (sending only 64 pixels made it show stale colors).
CHAIN = 128
MIRROR = not STACK

displayio.release_displays()
# Don't pass framebuffer= here: on CircuitPython 9.2.4 / RP2350 it hard-faults
# into safe mode. The matrix's own buffer is readable via memoryview instead.
if STACK:
    matrix = rgbmatrix.RGBMatrix(width=64, height=64, tile=2, serpentine=SERPENTINE, bit_depth=5,
                                 doublebuffer=True, **PINOUTS[PINOUT])
else:
    matrix = rgbmatrix.RGBMatrix(width=CHAIN, height=32, bit_depth=5, doublebuffer=True, **PINOUTS[PINOUT])
framebuffer = memoryview(matrix)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=not MIRROR)
mirror_due = 0.0


def mirror_refresh():
    """One-panel mode: draw, copy the left 64 columns onto the right 64, show."""
    global mirror_due
    now = time.monotonic()
    if now < mirror_due:
        return
    mirror_due = now + 0.025  # ~40 frames a second
    display.refresh(minimum_frames_per_second=0)
    for y in range(32):
        row = y * CHAIN
        framebuffer[row + WIDTH : row + CHAIN] = framebuffer[row : row + WIDTH]
    matrix.refresh()
try:
    supervisor.status_bar.console = False
    supervisor.status_bar.display = False
except AttributeError:
    pass

panel = make_panel(display, STACK)
panel.message("STARTING" if STACK else "START", small="WI-FI...", top_color=0xFFFFFF)

# ---------------------------------------------------------------- storage

DEV_MODE = False
try:
    os.stat("/devmode")
    DEV_MODE = True  # boot.py left the drive writable over USB; we can't save
except OSError:
    pass

# ---------------------------------------------------------------- network

net = {"mode": "off", "ip": "", "host": "", "ssid": "", "ap_ssid": "", "ap_password": ""}
server = None


def _wifi_config():
    try:
        with open("/data/wifi.json") as f:
            cfg = json.loads(f.read())
            if cfg.get("ssid"):
                return cfg["ssid"], cfg.get("password", "")
    except (OSError, ValueError):
        pass
    ssid = os.getenv("SCOREBOARD_WIFI_SSID")
    return (ssid, os.getenv("SCOREBOARD_WIFI_PASSWORD") or "") if ssid else (None, None)


def start_network():
    import wifi

    ssid, password = _wifi_config()
    if ssid:
        try:
            print("joining Wi-Fi %r" % ssid)
            wifi.radio.connect(ssid, password or None, timeout=15)
            net.update(mode="station", ssid=ssid, ip=str(wifi.radio.ipv4_address))
        except (ConnectionError, OSError) as err:
            print("couldn't join %r: %s" % (ssid, err))
    if net["mode"] == "off":
        ap_ssid = os.getenv("SCOREBOARD_AP_SSID") or "Scoreboard"
        ap_password = os.getenv("SCOREBOARD_AP_PASSWORD") or "pickleball"
        try:
            wifi.radio.stop_station()
        except (AttributeError, OSError):
            pass
        wifi.radio.start_ap(ap_ssid, ap_password)
        net.update(mode="ap", ap_ssid=ap_ssid, ap_password=ap_password, ip=str(wifi.radio.ipv4_address_ap))
    try:
        import mdns

        mdns_server = mdns.Server(wifi.radio)
        mdns_server.hostname = "scoreboard"
        mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)
        net["host"] = "scoreboard.local"
        net["_mdns"] = mdns_server  # keep a reference
    except (ImportError, RuntimeError, OSError) as err:
        print("mDNS unavailable:", err)
    print("network:", net["mode"], net["ip"])
    return wifi


def net_info():
    return {k: v for k, v in net.items() if not k.startswith("_")}


# ---------------------------------------------------------------- app

from scoreapp import App  # noqa: E402  (after the start-up screen is up)


def set_clock(t):
    rtc.RTC().datetime = time.localtime(int(t))


app = App("/data", clock=time.time, set_clock=set_clock, net_info=net_info, reboot=microcontroller.reset)
if DEV_MODE:
    app.writable = False

try:
    wifi = start_network()
    import socketpool
    from adafruit_httpserver import DELETE, GET, POST, Response, Server

    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool, "/www", debug=False)
    server.request_buffer_size = 4096

    @server.route("/api/....", [GET, POST, DELETE])
    def api(request):
        code, ctype, body = app.handle(request.method, request.path, request.query_params, request.body.decode())
        return Response(request, body, status=(code, "OK" if code == 200 else "Error"),
                        content_type=ctype, headers={"Cache-Control": "no-store"})

    server.start("0.0.0.0", 80)
except Exception as err:  # keep the scoreboard usable over serial without Wi-Fi
    print("network failed:", err)
    net["mode"] = "off"
    server = None

# ---------------------------------------------------------------- serial console

HELP = """Commands:
  status               score and network
  a | b                team A / B won the rally
  u                    undo
  api METHOD PATH [JSON]   call the web API, e.g. api GET /api/state
  snap                 dump the frame buffer (RGB565 hex rows)
  panels 1|2           one 64x32 panel, or two stacked into 64x64 (restarts the board)
  flip                 toggle the bottom panel's mounting: upside-down / upright (restarts)
  devmode             restart with CIRCUITPY writable over USB (history not saved)
  reboot               restart"""


def serial_command(line):
    parts = line.strip().split(None, 1)
    if not parts:
        return None
    cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if cmd in ("a", "b", "u"):
        path = "/api/w/undo" if cmd == "u" else "/api/w/" + cmd
        return app.handle("POST", path, {}, "")[2]
    if cmd == "status":
        return "%s\nnet %s %s %s  storage=%s" % (app.watch_text(), net["mode"], net["ip"], net["host"],
                                                "ok" if app.writable else "read-only")
    if cmd == "api":
        bits = arg.split(None, 2)
        if len(bits) < 2:
            return "usage: api METHOD PATH [JSON]"
        code, _, body = app.handle(bits[0].upper(), bits[1], {}, bits[2] if len(bits) > 2 else "")
        return "%d %s" % (code, body)
    if cmd == "snap":
        w = WIDTH if STACK else CHAIN
        print("SNAP %d %d" % (w, HEIGHT))
        for y in range(HEIGHT):
            print(bytes(framebuffer[y * w : (y + 1) * w]).hex())
        return "END"
    if cmd == "flip":
        code, _, body = app.handle("POST", "/api/settings", {}, '{"serpentine": %s}' % ("false" if SERPENTINE else "true"))
        return "OK bottom panel %s, restarting" % ("upright" if SERPENTINE else "upside-down") if code == 200 else body
    if cmd == "panels":
        if arg.strip() not in ("1", "2"):
            return "usage: panels 1|2  (now %d)" % (2 if STACK else 1)
        code, _, body = app.handle("POST", "/api/settings", {}, '{"panels": %s}' % arg.strip())
        return "OK panels=%s, restarting" % arg.strip() if code == 200 else body
    if cmd == "devmode":
        app.flush()
        with open("/devmode", "w") as f:
            f.write("CIRCUITPY is writable over USB while this file exists. Delete it and restart for normal use.\n")
        microcontroller.reset()
    if cmd == "reboot":
        app.flush()
        microcontroller.reset()
    if cmd in ("help", "?"):
        return HELP
    return "ERR unknown command (try help)"


print("\r\nPickleball scoreboard ready. Type 'help'.")
print("network: %s %s %s" % (net["mode"], net["ip"], net["host"]))
if DEV_MODE:
    print("DEV MODE: CIRCUITPY is writable over USB; games are not saved.")

line = ""
while True:
    if server is not None:
        try:
            server.poll()
        except (OSError, RuntimeError, ValueError) as err:
            print("http error:", err)

    n = supervisor.runtime.serial_bytes_available
    if n:
        for ch in sys.stdin.read(n):
            if ch in "\r\n":
                if line:
                    sys.stdout.write("\r\n")
                    try:
                        reply = serial_command(line)
                    except Exception as err:  # never let a typo stop the scoreboard
                        reply = "ERR %s" % err
                    if reply:
                        print(reply)
                    line = ""
            elif ch in "\x08\x7f":
                if line:
                    line = line[:-1]
                    sys.stdout.write("\x08 \x08")
            elif " " <= ch <= "~" and len(line) < 200:
                line += ch
                sys.stdout.write(ch)

    app.tick()
    panel.update(app, net)
    if MIRROR:
        mirror_refresh()
