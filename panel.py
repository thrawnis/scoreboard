# LED panel rendering for the scoreboard: two 64x32 panels stacked into one 64x64 board.
# Layouts are driven by Match.view() so they don't need to know the sport.
#
# Pickleball and badminton use the court view (see "court diagram" below). Other rally sports use the
# standard layout, where each team owns one 64x32 half and both halves use the same layout:
#  y 0-9    TEAM NAME           (first half: idle timer / TB; second half: sport icon)
#  y 10-11  games-won dots
#  y 12-31  [MIKE]  big score   (server box + serve number on the left)
#           [JO  ]
#  Tennis: games in the current set replace the player rows; the server's
#  name sits below them.
#
# "Switch ends", scoring animations and the start-up screen take over the display
# (a scoring animation plays on the scorer's own half).

import gc
import time

import displayio
import vectorio
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import label

import colors
import icons
from anim import Animator

H = 32  # height of one 64x32 panel

# The six team colours (misc games use up to 6). Chosen in Settings (colors.py); red and blue by default.
# Updated in place whenever the setting changes, so everything that reads it picks the change up.
TEAM_COLORS = colors.as_ints(None)
SCORE = 0xFFC800
TIMER = 0xC8E8FF  # idle timer: a pale cyan-white, easy to tell from the team colours
NET = 0xFFFFFF  # the net: a white line where the two teams' courts meet
SERVER_BOX = 0x30FF30
SERVER_TEXT = 0xFFFFFF  # bright name inside a green outline reads better than inverse text
PLAYER = 0x707070
SERVER_NUM = 0xFFFFFF
GAMES = 0xFFFFFF
DOT_OFF = 0x282828
ALERT = 0xFFC800
BOX_W, BOX_H = 22, 9  # fits a 4-character player name in the 5x7 font (the court view shows all 5)


def _scale(color, pct):
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return (r * pct // 100) << 16 | (g * pct // 100) << 8 | (b * pct // 100)


def short_name(name):
    """Drop a leading "Team " (the default names are "Team A" / "Team B")."""
    return name[5:] if name[:5].lower() == "team " and len(name) > 5 else name


def fmt_secs(s):
    s = int(max(0, s))
    if s >= 20 * 60:  # no point counting on: after 20 minutes the timer just shows dashes
        return "--:--"
    return "%d:%02d" % (s // 60, s % 60)


class _OutlineBox:
    """A 1-pixel outline rectangle: the border colour, with black inset over it."""

    def __init__(self, group, pal, x, y, w, h):
        self._outer = vectorio.Rectangle(pixel_shader=pal, width=w, height=h, x=x, y=y, color_index=0)
        self._inner = vectorio.Rectangle(pixel_shader=pal, width=w - 2, height=h - 2, x=x + 1, y=y + 1, color_index=1)
        group.append(self._outer)
        group.append(self._inner)
        self.hidden = True

    @property
    def hidden(self):
        return self._outer.hidden

    @hidden.setter
    def hidden(self, value):
        self._outer.hidden = value
        self._inner.hidden = value

    @property
    def x(self):
        return self._outer.x

    @x.setter
    def x(self, value):
        self._outer.x = value
        self._inner.x = value + 1

    @property
    def y(self):
        return self._outer.y

    @y.setter
    def y(self, value):
        self._outer.y = value
        self._inner.y = value + 1


def make_panel(display):
    return StackPanel(display)


class _Panel:
    """Shared plumbing: fonts, brightness, change detection, messages, animations."""

    W = 64

    def __init__(self, display):
        self.display = display
        self.small = bitmap_font.load_font("/fonts/6x10.bdf")
        self.tiny = bitmap_font.load_font("/fonts/5x7.bdf")  # player names (2-4 characters)
        self.tiny.load_glyphs("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-+/ ")
        self.mid = bitmap_font.load_font("/fonts/7x14B.bdf")
        self.big = bitmap_font.load_font("/fonts/score-20.bdf")
        self.small.load_glyphs(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-+/")
        self.mid.load_glyphs(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-!+")
        self.big.load_glyphs("0123456789AD")
        self.pct = 100
        self._labels = []  # every label, so brightness can be reapplied (labels are unhashable)
        self.version = None
        self.last_second = None
        self.celebrated = None
        self._colors_seen = False  # the "colors" setting last applied (False: none yet)
        self.test_until = 0  # a test pattern (see test_fill) holds the display until then
        self.test_group = None
        self.score_group = displayio.Group()
        self.timed_group = None  # built on first use
        self.multi_group = None
        self.multi_n = None
        self.anim = Animator(self.W, self.mid)
        self.icon = None  # (key, TileGrid) - one icon, moved between layouts
        self.icon_parent = None
        self.splash_match = None
        self.splash_until = 0
        self.splash_group = displayio.Group()
        self.splash_big = displayio.Group(scale=2, x=self.W // 2 - 8, y=1)
        self.splash_group.append(self.splash_big)
        self.splash_name = self._label(self.small, 0xFFFFFF, (0.5, 1.0), (self.W // 2, H - 1), self.splash_group)

        W = self.W
        self.msg_group = displayio.Group()
        self.msg_top = self._label(self.mid, ALERT, (0.5, 0.0), (W // 2, 0), self.msg_group)
        self.msg_bottom = self._label(self.mid, 0xFFFFFF, (0.5, 1.0), (W // 2, H), self.msg_group)
        self.msg_small_top = self._label(self.small, 0xFFFFFF, (0.5, 0.0), (W // 2, 2), self.msg_group)
        self.msg_small = self._label(self.small, 0xFFFFFF, (0.5, 1.0), (W // 2, H - 1), self.msg_group)
        display.root_group = self.msg_group

    def _label(self, font, color, anchor, pos, group=None):
        lbl = label.Label(font, text=" ", color=_scale(color, self.pct), anchor_point=anchor)
        lbl.anchored_position = pos
        lbl.base_color = color
        self._labels.append(lbl)
        (group if group is not None else self.score_group).append(lbl)
        return lbl

    def _drop_labels(self, labels):
        """Stop tracking the labels of a layout that's being replaced (they'd stay in memory otherwise).
        Labels are unhashable, so compare by identity."""
        gone = [lbl for lbl in labels if lbl is not None]
        self._labels = [lbl for lbl in self._labels if not any(lbl is g for g in gone)]

    def _rect(self, x, y, w, h, group=None):
        pal = displayio.Palette(1)
        rect = vectorio.Rectangle(pixel_shader=pal, width=w, height=h, x=x, y=y)
        rect.hidden = True
        (group if group is not None else self.score_group).append(rect)
        return rect, pal

    def _place_icon(self, group, key, x, y, show=True):
        """Put the sport icon in `group` at (x, y); there is one icon, shared by the layouts."""
        if self.icon is None or self.icon[0] != key:
            if self.icon is not None and self.icon_parent is not None:
                try:
                    self.icon_parent.remove(self.icon[1])
                except ValueError:
                    pass
            self.icon = (key, icons.make(key, self.pct))
            self.icon_parent = None
        tile = self.icon[1]
        if self.icon_parent is not group:
            if self.icon_parent is not None:
                self.icon_parent.remove(tile)
            group.append(tile)
            self.icon_parent = group
        tile.x, tile.y = x, y
        tile.hidden = not show

    def _outline(self, x, y, w, h, group=None):
        pal = displayio.Palette(2)
        pal[1] = 0x000000
        return _OutlineBox(group if group is not None else self.score_group, pal, x, y, w, h), pal

    def _text(self, lbl, text):
        text = text or " "
        if lbl.text != text:
            lbl.text = text

    def _color(self, lbl, color):
        lbl.base_color = color
        scaled = _scale(color, self.pct)
        if lbl.color != scaled:
            lbl.color = scaled

    def _fill(self, pal, color):
        scaled = _scale(color, self.pct)
        if pal[0] != scaled:
            pal[0] = scaled

    def _show(self, group):
        if self.display.root_group is not group:
            self.display.root_group = group

    def _is_shown(self, group):
        return self.display.root_group is group

    def message(self, top=None, bottom=None, small_top=None, small=None, top_color=ALERT, bottom_color=0xFFFFFF):
        self._text(self.msg_top, top)
        self._color(self.msg_top, top_color)
        self._text(self.msg_bottom, bottom)
        self._color(self.msg_bottom, bottom_color)
        self._text(self.msg_small_top, small_top)
        self._color(self.msg_small_top, top_color)
        self._text(self.msg_small, small)
        self._color(self.msg_small, bottom_color)
        self._show(self.msg_group)

    def _dots(self, dots, v):
        for t in (0, 1):
            for i, (rect, pal) in enumerate(dots[t]):
                rect.hidden = i >= v["need"]
                self._fill(pal, TEAM_COLORS[t] if i < v["won"][t] else DOT_OFF)

    def _center_text(self, v, now, second):
        """Idle timer, alternating with a short status (e.g. TB) when there is one."""
        if v["over"]:
            return ("MATCH" if v["match_over"] else v["unit"]), 0xFFFFFF
        if v["status"] and (second // 4) % 2 == 1:
            return "TB" if v["status"] == "TIEBREAK" else v["status"][:5], ALERT
        return fmt_secs(now - v["last_t"]), TIMER

    def test_fill(self, color, seconds):
        """Troubleshooting: fill every pixel with one colour (at the current brightness) for a while,
        e.g. to compare the brightness of the two screens."""
        if self.test_group is None:
            self.test_pal = displayio.Palette(1)
            self.test_group = displayio.Group()
            self.test_group.append(vectorio.Rectangle(pixel_shader=self.test_pal, width=self.display.width,
                                                      height=self.display.height, x=0, y=0))
        self.test_pal[0] = _scale(color, self.pct)
        self.test_until = time.monotonic() + seconds
        self._show(self.test_group)

    def update(self, app, net=None):
        """Redraw from the app. Cheap when nothing changed; call every loop."""
        if time.monotonic() < self.test_until:
            return
        if self.test_until:  # test pattern over: redraw the scoreboard
            self.test_until = 0
            self.version = None
        self.court_mode = app.meta.get("court", "over")
        picked = app.meta.get("colors")
        if picked != self._colors_seen:  # team colours changed in Settings: redraw everything
            self._colors_seen = picked
            TEAM_COLORS[:] = colors.as_ints(picked)
            self.version = None
        pct = app.meta.get("brightness", 100)
        if pct != self.pct:
            self.pct = pct
            self.version = None
            for lbl in self._labels:
                lbl.color = _scale(lbl.base_color, pct)
            if self.icon is not None:
                self.icon = (None, self.icon[1])  # rebuild at the new brightness

        # Scoring animation (runs every loop while active).
        c = app.celebrate
        if c and c["n"] != self.celebrated:
            self.celebrated = c["n"]
            if app.meta.get("animations", True) and app.match:
                t = c["tm"]
                n = c.get("teams", 2)
                other = TEAM_COLORS[1 - t] if n == 2 else 0xFFFFFF
                cx, cy, oy, cover = self._anim_area(t, n)
                self.anim.start(c["sport"], t, c["text"], c["level"], TEAM_COLORS[t], other, self.pct, cx, cy, oy, cover)
        if self.anim.active:
            if self.anim.step():
                self._show(self.anim.group)
                return
            self.version = None  # animation over: redraw the score

        m = app.match
        # Start-of-match splash: the sport's icon (double size) and name.
        if m is not None and m.d["id"] != self.splash_match:
            self.splash_match = m.d["id"]
            self.splash_until = time.monotonic() + 2.5
            key = icons.icon_key(m.d["cfg"])
            while len(self.splash_big):
                self.splash_big.pop()
            self.splash_big.append(icons.make(key, self.pct))
            self._text(self.splash_name, icons.NAMES.get(key, "SCOREBOARD"))
            self._show(self.splash_group)
        if time.monotonic() < self.splash_until:
            return
        if self._is_shown(self.splash_group):
            self.version = None

        fast = m is not None and m.game.layout == "timed" and m.game.running
        second = int(time.monotonic() * 2)  # half-second ticks for blinking and timers
        tick = int(time.monotonic() * 10) if fast else second  # tenths for a running game clock
        if app.version == self.version and tick == self.last_second:
            return
        self.version = app.version
        self.last_second = tick
        blink_on = second % 2 == 0

        if m is None:
            self.start_screen(net or {}, second // 6)  # rotate every 3 seconds
            return
        if app.alert and app.alert["k"] == "switch":
            self.message("SWITCH", "ENDS", top_color=ALERT, bottom_color=ALERT if blink_on else 0x806000)
            return
        v = m.view(app.clock())
        key = icons.icon_key(m.d["cfg"])
        if v["layout"] == "timed":
            self.draw_timed(v, key, blink_on)
            self._show(self.timed_group)
        elif v["layout"] == "multi":
            self.draw_multi(v, blink_on)
            self._show(self.multi_group)
        else:
            group = self.draw(v, app.clock(), second, blink_on, key)  # a layout may pick its own group
            self._show(group or self.score_group)


class StackPanel(_Panel):
    """Two 64x32 panels stacked into a 64x64 board. Team A is drawn in the first half of the frame
    buffer, which (as wired) shows on the bottom screen; team B in the second half, on the top screen."""

    W = 64
    OY = (0, 32)  # top of each team's panel
    SCORE_X = 18
    BOX_X = 37
    NUM_X = 61
    ROWS_Y = (12, 22)
    SINGLE_Y = 17
    DOT_X = (1, 4, 7)
    DOT_Y = 10
    NAME_CHARS = 5  # leaves room on the right of the name row for the timer / clock

    def __init__(self, display):
        super().__init__(display)
        # Everything is shown through one stage group, so a scoring animation can sit
        # on top of the scoreboard and cover only the panel of the team that scored.
        self.stage = displayio.Group()
        self._shown = None
        self._base = None  # last scoreboard layout shown (what an animation plays over)
        self.court_group = None  # court diagram (pickleball, badminton), built on first use
        self.start_group = None  # the Wi-Fi details shown before a match, built on first use
        # A full-screen black layer under everything. Changing it (see _show) makes the display redraw the
        # whole screen, so nothing of the previous layout is left behind when the next one is sparse.
        bg_pal = displayio.Palette(1)
        bg_pal[0] = 0x000000
        self._bg = vectorio.Rectangle(pixel_shader=bg_pal, width=self.W, height=64, x=0, y=0)
        display.root_group = self.stage
        # centre the full-screen layouts on the taller board
        self.msg_top.anchored_position = (32, 14)
        self.msg_bottom.anchored_position = (32, 50)
        self.msg_small_top.anchored_position = (32, 20)
        self.msg_small.anchored_position = (32, 44)
        self.splash_big.y = 12
        self.splash_name.anchored_position = (32, 50)
        self._show(self.msg_group)

        self.dots = [[self._rect(x, oy + self.DOT_Y, 2, 2) for x in self.DOT_X] for oy in self.OY]
        self.boxes = [[self._outline(self.BOX_X, oy + self.ROWS_Y[r], BOX_W, BOX_H) for r in (0, 1)] for oy in self.OY]
        # serve marker when there's no player to highlight (foosball): beside the score
        self.balls = [self._rect(34, oy + 13, 3, 3) for oy in self.OY]
        self.names = [self._label(self.small, TEAM_COLORS[t], (0.0, 0.0), (1, self.OY[t])) for t in (0, 1)]
        self.timer = self._label(self.small, TIMER, (1.0, 0.0), (self.W, 0))  # top panel, right
        self.scores = [self._label(self.big, SCORE, (0.5, 1.0), (self.SCORE_X, oy + H)) for oy in self.OY]
        self.mids = [self._label(self.mid, GAMES, (0.5, 0.0), (self.BOX_X + BOX_W // 2, oy + 10)) for oy in self.OY]
        self.inits = [
            [self._label(self.tiny, PLAYER, (0.5, 0.5), (self.BOX_X + BOX_W // 2, oy + self.ROWS_Y[r] + BOX_H // 2 + 1))
             for r in (0, 1)]
            for oy in self.OY
        ]
        self.nums = [self._label(self.tiny, SERVER_NUM, (0.5, 0.5), (self.NUM_X, oy)) for oy in self.OY]

    # ---------------------------------------------------------------- display plumbing

    def _show(self, group):
        if group is self._shown:
            return
        self._shown = group
        if group in (self.score_group, self.timed_group, self.multi_group, self.court_group):
            self._base = group
        while len(self.stage):
            self.stage.pop()
        self.stage.append(self._bg)
        if group is self.anim.group and self._base is not None:
            self.stage.append(self._base)
        self.stage.append(group)
        self._bg.hidden = True  # hide-then-show marks the whole screen for redrawing
        self._bg.hidden = False

    def _is_shown(self, group):
        return self._shown is group

    def _anim_area(self, t, n):
        if n > 2:  # misc with 3+ teams: a burst over that team's cell
            x, y, w, h = self._cells(n)[t]
            return x + w // 2, y + h // 2, 0, False
        return None, None, self.OY[t], True  # the scorer's panel

    # ---------------------------------------------------------------- start-up

    def start_screen(self, net, step):
        """Shown until a match starts: how to join the scoreboard, all at once in the small font. The
        password is masked after its first character. Every line sits wholly on one screen: nothing may
        cross the middle of the canvas, where the two screens meet."""
        if self.start_group is None:
            # The smallest font (4x6, public domain), with lower-case letters: the Wi-Fi name and the first
            # character of the password are shown exactly as written (the rest of the password is dashes).
            # Loaded only now, because RAM is tight at start-up.
            gc.collect()
            micro = bitmap_font.load_font("/fonts/4x6.bdf")
            micro.load_glyphs("".join(chr(c) for c in range(32, 127)))
            g = displayio.Group()
            # No headings: the title and the network name on the screen that shows them (canvas rows
            # 32-63), the password and the address on the other, each pair centred on its screen.
            self.s_title = self._label(micro, ALERT, (0.5, 0.0), (32, 40), g)
            self.s_lines = [self._label(micro, 0xFFFFFF, (0.5, 0.0), (32, y), g) for y in (51, 7, 18)]
            self.start_group = g
        mode = net.get("mode")
        if mode == "ap":
            password = net.get("ap_password") or ""
            lines = [net.get("ap_ssid") or "", password[:1] + "-" * (len(password) - 1), net.get("ip") or ""]
        elif mode == "station":
            lines = [net.get("ssid") or "", "", net.get("ip") or ""]
        else:
            lines = ["NO WI-FI", "", ""]
        self._text(self.s_title, "SCOREBOARD")
        for lbl, text in zip(self.s_lines, lines):
            self._text(lbl, text[:16])  # 16 characters fit across the screen
        self._show(self.start_group)

    # ---------------------------------------------------------------- standard layout

    def _box(self, t, r, y, text, highlight):
        box, pal = self.boxes[t][r]
        lbl = self.inits[t][r]
        box.y = y
        box.hidden = not highlight
        self._fill(pal, SERVER_BOX)
        lbl.anchored_position = (self.BOX_X + BOX_W // 2, y + BOX_H // 2 + 1)
        self._text(lbl, text)
        self._color(lbl, SERVER_TEXT if highlight else PLAYER)

    # ---------------------------------------------------------------- court diagram
    # One 64x64 canvas (the two panels act as a single screen), top to bottom:
    #   A's court (0-18) | games-won dots and idle timer (22-28) | the call X-Y-Z (33-43) | B's court (45-63)
    # Teams are told apart by colour, so there are no team-name rows. Inside the courts every element
    # keeps a 1-row margin; the call and the timer are centred in the rows left above and below.
    # As displayed the screens are swapped, so the call is at the very top of the board and the
    # timer at the very bottom; the courts meet in the middle at the white net line.
    COURT_Y = (0, 45)
    COURT_HS = (19, 19)
    INFO_Y = 21
    CALL_Y = 31
    DOT_XS = ((1, 4, 7), (61, 58, 55))  # A's games-won dots on the left, B's on the right

    def _build_court(self):
        g = displayio.Group()
        W = self.W
        # 6x10, the next size down from the score's 7x14 bold; its 7-row digits start 1 row below the
        # label's top, so INFO_Y puts them in the middle of the free rows 19-31
        self.c_timer = self._label(self.small, TIMER, (0.5, 0.0), (W // 2, self.INFO_Y), g)
        self.c_dots = [[self._rect(x, self.INFO_Y + 3, 2, 2, g) for x in xs] for xs in self.DOT_XS]
        # the call is four labels so the scores can wear their team's colour: X, "-", Y, "-Z"
        self.c_call = [self._label(self.mid, 0xFFFFFF, (0.0, 0.0), (0, self.CALL_Y), g) for _ in range(4)]
        self.c_ball = []  # the sport's ball, in the server's court (one tile per team)
        self.c_ball_key = None
        self.c_frames = []
        self.c_slots = []  # [team][column] player name; column 0 = left on the board
        for t, cy in enumerate(self.COURT_Y):
            frame, pal = self._outline(0, cy, W, self.COURT_HS[t], g)  # the team's court, in the team's colour
            frame.hidden = False
            self.c_frames.append((frame, pal))
            self.c_slots.append([self._label(self.tiny, PLAYER, (0.5, 0.5), (16 + 32 * c, cy + 10), g) for c in (0, 1)])
        # a line down the middle of each court, in the team's colour, between its two service courts
        self.c_div = []
        for t, cy in enumerate(self.COURT_Y):
            rect, pal = self._rect(W // 2, cy, 1, self.COURT_HS[t], g)
            rect.hidden = False
            self.c_div.append(pal)
        # The net: the two courts' touching edges (the last row of the top screen's half of the
        # frame buffer and the first row of the other) drawn as one white line between the teams.
        # Added last so it runs unbroken over the ends of the dividers.
        self.c_net = []
        for y in (0, 63):
            rect, pal = self._rect(0, y, W, 1, g)
            rect.hidden = False
            self.c_net.append(pal)
        self.court_group = g

    def draw_court(self, v, now, second, blink_on, key):
        """Birds-eye view with the net in the middle: each team's court with its players where
        they stand (server and receiver at the baselines, partners at the net), the sport's ball
        in the server's court, and the call X-Y-Z (serving score, receiving score, server number).
        As the screens are wired, team B is the top court (facing down) and team A the bottom court
        (facing up); see Settings for the left/right choices."""
        if self.court_group is None:
            self._build_court()
        if self.c_ball_key != (key, self.pct):  # one ball tile per team, rebuilt for a new sport or brightness
            for tile in self.c_ball:
                self.court_group.remove(tile)
            self.c_ball = []
            for t in (0, 1):
                tile = icons.make(key, self.pct)
                tile.hidden = True
                self.court_group.append(tile)
                self.c_ball.append(tile)
            self.c_ball_key = (key, self.pct)
        server = v["server"]
        srv_side = None  # which court the server is standing in (R or L)
        if server is not None:
            for sd, ix in v["courts"][server[0]].items():
                if ix == server[1]:
                    srv_side = sd

        # the call, centred at the net
        big = v["big"]

        def shade(t):
            return 0x000000 if (v["over"] and v["winner"] == t and not blink_on) else TEAM_COLORS[t]

        if server is not None:
            s = server[0]
            segs = [(big[s], TEAM_COLORS[s]), ("-", TIMER), (big[1 - s], TEAM_COLORS[1 - s]),
                    ("-%d" % server[2] if server[2] else "", 0xFFFFFF)]
        else:  # game over: the final score, A first
            segs = [(big[0], shade(0)), ("-", TIMER), (big[1], shade(1)), ("", 0xFFFFFF)]
        x = (self.W - 7 * sum(len(text) for text, _ in segs)) // 2
        for lbl, (text, color) in zip(self.c_call, segs):
            self._text(lbl, text)
            self._color(lbl, color)
            pos = (x, self.CALL_Y)
            if lbl.anchored_position != pos:
                lbl.anchored_position = pos
            x += 7 * len(text)

        for pal in self.c_net:
            self._fill(pal, NET)
        for t, cy in enumerate(self.COURT_Y):
            self._fill(self.c_frames[t][1], TEAM_COLORS[t])
            self._fill(self.c_div[t], TEAM_COLORS[t])
            ball = self.c_ball[t]
            serve_col = None  # set once, below: never hide-then-show (the display refreshes in between)
            # The two screens are swapped (the Pico's cable plugs into the bottom screen), so the first
            # half of the frame buffer (team A) shows on the bottom screen and B's on the top. The net is
            # where the two courts meet: A's court has it at its top edge, B's at its bottom edge.
            # The baseline is the end of the court away from the net: the bottom for A, the top for B.
            # Text is 6 rows tall, drawn from 4 above its anchor row to 1 below it; both ends of both
            # courts leave 1 clear row inside the line. The 8-row ball is centred on the same row.
            base_y, net_y = (cy + 15, cy + 6) if t == 0 else (cy + 6, cy + 15)
            ball_y = cy + 2 if t == 0 else cy + 9
            for side in ("R", "L"):
                # over:   birds-eye. As displayed, B (top screen) faces down and A (bottom screen) faces up,
                #         so B's right-hand court is on the viewer's left and A's on the viewer's right
                # mirror: the same, flipped left-right
                # same:   every team's right court on the board's right (as if both stood at one end)
                right_col = 1 if (self.court_mode == "same" or (self.court_mode == "over") != bool(t)) else 0
                col = right_col if side == "R" else 1 - right_col
                idx = v["courts"][t].get(side)
                players = v["players"][t]
                lbl = self.c_slots[t][col]
                # the server and the player receiving the serve (the diagonal court) stand at the
                # baselines; everyone else is up at the net
                active = idx is not None and server is not None and side == srv_side
                self._text(lbl, players[idx] if idx is not None and idx < len(players) else " ")
                self._color(lbl, SERVER_TEXT if active else PLAYER)
                pos = (16 + 32 * col, base_y if active else (net_y if server is not None else cy + 10))
                if lbl.anchored_position != pos:
                    lbl.anchored_position = pos
                if active and t == server[0]:
                    serve_col = col
            if serve_col is None:
                if not ball.hidden:
                    ball.hidden = True
            else:
                # the ball, centred in the server's half at the net end of the court (the server
                # number is part of the call, so there is none beside the ball)
                bx = 32 * serve_col + 12
                if ball.x != bx or ball.y != ball_y:
                    ball.x, ball.y = bx, ball_y
                if ball.hidden:
                    ball.hidden = False
            for i, (rect, pal) in enumerate(self.c_dots[t]):
                rect.hidden = i >= v["need"]
                self._fill(pal, TEAM_COLORS[t] if i < v["won"][t] else DOT_OFF)
        text, color = self._center_text(v, now, second)
        self._text(self.c_timer, text)
        self._color(self.c_timer, color)
        return self.court_group

    # ---------------------------------------------------------------- standard layout

    def draw(self, v, now, second, blink_on, key):
        if v.get("courts"):
            return self.draw_court(v, now, second, blink_on, key)
        server = v["server"]
        tennis_like = v["mid"][0] is not None
        self._place_icon(self.score_group, key, self.W - 9, self.OY[1] + 1)  # bottom panel, top right
        for t in (0, 1):
            oy = self.OY[t]
            rows = (oy + self.ROWS_Y[0], oy + self.ROWS_Y[1])
            single = oy + self.SINGLE_Y
            self._text(self.names[t], short_name(v["names"][t])[:self.NAME_CHARS].upper())
            self._color(self.names[t], 0x000000 if (v["over"] and v["winner"] == t and not blink_on) else TEAM_COLORS[t])
            self._text(self.scores[t], v["big"][t])
            self._text(self.mids[t], v["mid"][t] if tennis_like else " ")
            players = v["players"][t]
            is_serving = server is not None and server[0] == t
            ball, ball_pal = self.balls[t]
            ball.hidden = not (v["serving"] == t and server is None and not v["over"])
            self._fill(ball_pal, 0xFFFFFF)

            if tennis_like:
                # games above; only the server's initials below
                self._box(t, 0, rows[0], " ", False)
                if is_serving:
                    self._box(t, 1, rows[1], players[server[1]][:4], True)  # the box fits 4 characters
                else:
                    self._box(t, 1, rows[1], " ", False)
            else:
                for r in (0, 1):
                    if r >= len(players):
                        self._box(t, r, rows[r], " ", False)
                        continue
                    y = rows[r] if v["doubles"] else single
                    self._box(t, r, y, players[r][:4], is_serving and server[1] == r)

            num = self.nums[t]
            if is_serving and server[2]:
                row_y = rows[server[1]] if v["doubles"] else single
                num.anchored_position = (self.NUM_X, row_y + BOX_H // 2 + 1)
                self._text(num, str(server[2]))
            else:
                self._text(num, " ")
        self._dots(self.dots, v)
        text, color = self._center_text(v, now, second)
        self._text(self.timer, text)
        self._color(self.timer, color)

    # ---------------------------------------------------------------- timed games

    def _build_timed(self):
        g = displayio.Group()
        W = self.W
        self.t_names = [self._label(self.small, TEAM_COLORS[t], (0.0, 0.0), (1, self.OY[t]), g) for t in (0, 1)]
        self.t_clock = self._label(self.small, 0xFFFFFF, (1.0, 0.0), (W, 0), g)  # top panel, right
        self.t_period = self._label(self.small, TIMER, (1.0, 0.0), (W, self.OY[1]), g)  # bottom panel, right
        self.t_big = [self._label(self.big, SCORE, (0.5, 1.0), (W // 2, oy + H), g) for oy in self.OY]
        self.t_mid = [self._label(self.mid, SCORE, (0.5, 0.5), (W // 2, oy + 21), g) for oy in self.OY]
        self.timed_group = g

    def draw_timed(self, v, key, blink_on):
        if self.timed_group is None:
            self._build_timed()
        clock = v["clock"]
        for t in (0, 1):
            self._text(self.t_names[t], short_name(v["names"][t])[:self.NAME_CHARS].upper())
            self._color(self.t_names[t], TEAM_COLORS[t])
        if v["over"]:
            self._text(self.t_clock, "FINAL")
            self._color(self.t_clock, 0xFFFFFF)
            self._text(self.t_period, " ")
        else:
            if clock["running"]:
                ccolor = 0xFF2000 if clock["low"] else 0xFFFFFF
            else:
                ccolor = ALERT  # stopped
            self._text(self.t_clock, clock["text"])
            self._color(self.t_clock, ccolor)
            self._text(self.t_period, "TIED" if clock["tied"] else clock["period_text"])
        big_ok = max(int(b) for b in v["big"]) < 1000
        for t in (0, 1):
            text = v["big"][t]
            off = v["over"] and v["winner"] == t and not blink_on
            self._text(self.t_big[t], text if big_ok else " ")
            self._text(self.t_mid[t], " " if big_ok else text)
            self._color(self.t_big[t], 0x000000 if off else SCORE)
            self._color(self.t_mid[t], 0x000000 if off else SCORE)

    # ---------------------------------------------------------------- misc: 2-6 teams

    @staticmethod
    def _cells(n):
        """(x, y, w, h) of each team's cell: 1 per row for 2, then a 2-wide grid."""
        if n <= 2:
            cols, cw, ch = 1, 64, 32
        elif n <= 4:
            cols, cw, ch = 2, 32, 32
        else:
            cols, cw, ch = 2, 32, 21
        return [((i % cols) * cw, (i // cols) * ch, cw, ch) for i in range(n)]

    def _build_multi(self, n):
        if self.multi_group is not None:  # rebuilt for a new team count: let the old layout be freed
            self._drop_labels(self.m_names + self.m_big + self.m_mid)
        g = displayio.Group()
        cells = self._cells(n)
        self.m_names, self.m_big, self.m_mid = [], [], []
        for i, (x, y, w, h) in enumerate(cells):
            cx = x + w // 2
            self.m_names.append(self._label(self.tiny, TEAM_COLORS[i], (0.5, 0.0), (cx, y + 1), g))
            if h == 32:
                self.m_big.append(self._label(self.big, TEAM_COLORS[i], (0.5, 1.0), (cx, y + h), g))
                self.m_mid.append(self._label(self.mid, TEAM_COLORS[i], (0.5, 0.5), (cx, y + 20), g))
            else:  # three rows: the 14-pixel font only
                self.m_big.append(None)
                self.m_mid.append(self._label(self.mid, TEAM_COLORS[i], (0.5, 1.0), (cx, y + h), g))
        # thin dividers between the cells
        lines = set()
        for x, y, w, h in cells:
            if x:
                lines.add((x - 1, 0, 1, 64))
            if y:
                lines.add((0, y - 1, 64, 1))
        for x, y, w, h in lines:
            rect, pal = self._rect(x, y, w, h, g)
            rect.hidden = False
            self._fill(pal, 0x282828)
        self.multi_group = g
        self.multi_n = n
        self.multi_cells = cells

    def draw_multi(self, v, blink_on):
        n = len(v["big"])
        if self.multi_group is None or self.multi_n != n:
            self._build_multi(n)
        for i, (x, y, w, h) in enumerate(self.multi_cells):
            self._text(self.m_names[i], short_name(v["names"][i]).upper()[:max(1, (w - 2) // 5)])
            self._color(self.m_names[i], TEAM_COLORS[i])
            text = v["big"][i]
            fits_big = self.m_big[i] is not None and len(text) * 15 <= w - 2
            off = v["over"] and v["winner"] == i and not blink_on
            color = 0x000000 if off else TEAM_COLORS[i]
            if self.m_big[i] is not None:
                self._text(self.m_big[i], text if fits_big else " ")
                self._color(self.m_big[i], color)
            self._text(self.m_mid[i], " " if fits_big else text)
            self._color(self.m_mid[i], color)
