# Scoreboard

A Wi-Fi scoreboard for pickleball and other sports, running on a Raspberry Pi Pico 2 W with
CircuitPython. It drives a 64x64 LED board (two 64x32 HUB75 panels stacked) and serves a phone-friendly
web app for scoring and settings. It makes its own Wi-Fi network, or joins yours.

> This repository is public. Never commit Wi-Fi passwords or other secrets: `settings.toml` and
> `wifi.json` are git-ignored on purpose.

## Hardware

- Raspberry Pi Pico 2 W (CircuitPython 9.2.4)
- Seengreat RGB matrix adapter (revision V3.9 and later pinout; V3.8 is in `code.py` too)
- Two Adafruit 64x32 RGB LED matrix panels, 4 mm pitch (product 2278), stacked into 64x64 and chained
- Pins (V3.9 adapter): RGB GP2-GP7, address GP8-GP11, clock GP13, latch GP14, output enable GP15

The Pico's ribbon cable goes to the **bottom** screen, so the frame buffer's first 32 rows (team A) show
on the bottom screen and the next 32 (team B) on the top. The layouts are drawn for that. If you rewire or
swap the panels, the layouts need to change with them. The second panel in the chain is mounted upside-down
(the "serpentine" setting; toggle it in Settings or with `flip` if the lower half looks flipped).

Single-screen (64x32) mode still exists in the code but is switched off for now.

## What is on the board

Top to bottom as you see it: the score call, team B's court (blue), team A's court (red), then the idle timer.

- **Court view (pickleball, badminton):** a bird's-eye view with the net where the two courts meet (white
  line). Each team's court has a line down the middle in the team's colour. The server and the receiver
  (the player on the diagonal court) stand at the outer edge of their court, a ball marks the server, and
  the other players stand toward the net. The call is `serving score - receiving score - server number`,
  with the digits in the team colours. Left/right can be set in Settings.
- Other sports use the standard stacked layout, the timed-game layout, or the multi-team layout.
- Scoring animations play on the scorer's half. Player names are 1-5 characters.
- **Start screen** (no match running): the Wi-Fi name, the password masked after its first character, and
  the IP address, in the smallest font (4x6), so you can join without opening anything.

Sports: pickleball, tennis, badminton, ping pong, volleyball, racquetball, foosball, air hockey, shuffleboard,
timed games (basketball, soccer, hockey, football, custom) and a generic 2-6 team scorer.

## Files

| File | What it does |
| --- | --- |
| `code.py` | Starts the display, Wi-Fi and web server; main loop; serial console |
| `panel.py` | All LED layouts (court view, standard, timed, multi-team) |
| `anim.py`, `icons.py` | Scoring animations and the 8x8 sport icons |
| `sports.py` | The rules and scoring for every sport |
| `scoreapp.py` | Match state, saving, and the HTTP API |
| `www/index.html` | The web app |
| `boot.py` | Chooses whether the drive is writable over USB (see below) |
| `fonts/` | 4x6 (public domain X11 font, ASCII only), 5x7, 6x10, 7x14 bold and the large score digits |

Needed CircuitPython libraries (in `lib/`): `adafruit_bitmap_font`, `adafruit_display_text`,
`adafruit_httpserver`, `adafruit_ticks`.

## Wi-Fi

It joins the network saved from the web app, or `SCOREBOARD_WIFI_SSID` / `SCOREBOARD_WIFI_PASSWORD` in
`settings.toml`. Otherwise it starts its own network (`SCOREBOARD_AP_SSID` / `SCOREBOARD_AP_PASSWORD`,
default name "Scoreboard"; set your own password in `settings.toml`). The board is at `192.168.4.1` on its
own network, or `scoreboard.local`.

## Deploying changes

In normal mode CircuitPython owns the flash so games can be saved, and the drive is read-only from the
computer. To copy files over:

1. Type `devmode` in the serial console (the board restarts with the drive writable; games are not saved).
2. Copy the files to the CIRCUITPY drive. The board reloads on every save.
3. Delete `/devmode` from the drive and reset the board (button or replug) to go back to normal mode.

## Serial console (`/dev/ttyACM0`, 115200)

`help`, `status`, `a` / `b` (team A / B won the rally), `point` / `loss` (the remote's team / the other
team), `u` (undo), `api METHOD PATH [JSON]`, `snap` (dump the frame buffer), `panels 2`, `flip`,
`fill COLOR [secs]` (solid colour on both screens, for checking brightness), `devmode`, `reboot`.
On Linux you need to be in the `uucp` group to open the port.

## HTTP API

The web app uses `/api/state`, `/api/match`, `/api/rally` and friends. For remotes, shortcuts and buttons
there are plain-text endpoints:

- `POST /api/w/a`, `/api/w/b`: team A / B won the rally
- `/api/w/point`, `/api/w/loss` (POST or GET): a rally for the remote's team / for the other team. Pick the
  remote's team in Settings.
- `POST /api/w/undo`, `/api/w/clock`

## Remote control

The Pico 2 W's own Bluetooth is not available in CircuitPython, so a Bluetooth remote cannot connect
directly. A Wi-Fi button or a small second board that calls `/api/w/point` and `/api/w/loss` works.

## Troubleshooting

- **Safe mode / blank display:** a soft reboot does not leave safe mode; use a hard reset or replug.
- **Text on the wrong screen or upside-down:** see the Hardware section (screen order, "flip").
- **Brightness looks different between screens:** run `fill white` to compare the screens at one colour.
