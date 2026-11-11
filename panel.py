# LED panel rendering for the scoreboard, for every sport, on one or two panels.
# Layouts are driven by Match.view() so they don't need to know the sport.
#
# Two panels, stacked (64x64): each team owns one 64x32 panel - team A on top,
# team B underneath - and both use the same layout:
#  y 0-9    TEAM NAME           (top panel: idle timer / TB; bottom: sport icon)
#  y 10-11  games-won dots
#  y 12-31  [MIKE]  big score   (server box + serve number on the left)
#           [JO  ]
#  Tennis: games in the current set replace the player rows; the server's
#  name sits below them.
#
# One panel (64x32):
#  y 0-9    [MIKE]2   4-3     0:42     (server box over the serving team;
#  y 12-31   big score  ::  big score    games in the middle for tennis;
#                                        timer on the far side)
#
# "Switch ends", scoring animations and the start-up screen take over the display
# (a scoring animation plays on the scorer's own panel).

import time

import displayio
import vectorio
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import label

import icons
from anim import Animator

H = 32  # height of one 64x32 panel

TEAM_COLORS = (0xFF3000, 0x0090FF, 0x30FF30, 0xFFD000, 0xFF40FF, 0x00E0FF)  # misc uses up to 6
SCORE = 0xFFC800
TIMER = 0xB0B0B0
SERVER_BOX = 0x30FF30
SERVER_TEXT = 0xFFFFFF  # bright name inside a green outline reads better than inverse text
PLAYER = 0x707070
SERVER_NUM = 0xFFFFFF
GAMES = 0xFFFFFF
DOT_OFF = 0x282828
ALERT = 0xFFC800
BOX_W, BOX_H = 22, 9  # fits a 4-character player name in the 5x7 font


def _scale(color, pct):
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return (r * pct // 100) << 16 | (g * pct // 100) << 8 | (b * pct // 100)


def fmt_secs(s):
    s = int(max(0, s))
    if s >= 6000:
        return "99+"
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


def make_panel(display, stacked):
    return StackPanel(display) if stacked else CompactPanel(display)


class _Panel:
    """Shared plumbing: fonts, brightness, change detection, messages, animations."""

    W = 64

    def __init__(self, display):
        self.display = display
        self.small = bitmap_font.load_font("/fonts/6x10.bdf")
        self.tiny = bitmap_font.load_font("/fonts/5x7.bdf")  # player names (2-4 characters)
        self.tiny.load_glyphs("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
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

    def _anim_area(self, t, n):
        """Where a scoring animation plays: (centre x, centre y, y offset, black backdrop?)."""
        return ((2 * t + 1) * self.W // (2 * n) if n > 2 else None), None, 0, False

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

    def update(self, app, net=None):
        """Redraw from the app. Cheap when nothing changed; call every loop."""
        self.court_mode = app.meta.get("court", "over")
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


class CompactPanel(_Panel):
    """One 64x32 panel."""

    W = 64
    SCORE_X = (15, 49)
    BOX_X = (0, 42)
    NUM_X = (25, 38)
    BOX_Y = 1
    DOT_X = (30, 32)
    DOT_Y = (29, 26, 23)

    def __init__(self, display):
        super().__init__(display)
        self.dots = [[self._rect(self.DOT_X[t], y, 2, 2) for y in self.DOT_Y] for t in (0, 1)]
        self.box = self._outline(0, self.BOX_Y, BOX_W, BOX_H)
        self.ball = self._rect(0, 3, 3, 3)
        self.scores = [self._label(self.big, TEAM_COLORS[t], (0.5, 1.0), (self.SCORE_X[t], H)) for t in (0, 1)]
        self.server = self._label(self.tiny, SERVER_TEXT, (0.5, 0.5), (BOX_W // 2, self.BOX_Y + BOX_H // 2 + 1))
        self.num = self._label(self.tiny, SERVER_NUM, (0.5, 0.5), (self.NUM_X[0], self.BOX_Y + BOX_H // 2 + 1))
        self.games = self._label(self.small, GAMES, (0.5, 0.0), (self.W // 2, 0))
        self.timer = self._label(self.small, TIMER, (1.0, 0.0), (self.W, 0))

    def start_screen(self, net, step):
        if net.get("mode") == "ap":
            pairs = [("WI-FI", net["ap_ssid"]), ("PASSWORD", net["ap_password"]), ("OPEN", net["ip"])]
        elif net.get("mode") == "station":
            pairs = [("OPEN", net["ip"])]
        else:
            pairs = [("NO WI-FI", "")]
        top, bottom = pairs[step % len(pairs)]
        self.message(small_top=top, small=bottom.upper()[:10], top_color=0xFFC800)

    def _place_timer(self, anchor_x, x):
        self.timer.anchor_point = (anchor_x, 0.0)
        self.timer.anchored_position = (x, 0)

    def draw(self, v, now, second, blink_on, key):
        for t in (0, 1):
            self._text(self.scores[t], v["big"][t])
            winner_blink = v["over"] and v["winner"] == t and not blink_on
            self._color(self.scores[t], 0x000000 if winner_blink else TEAM_COLORS[t])
        self._dots(self.dots, v)

        box, pal = self.box
        ball, ball_pal = self.ball
        text, color = self._center_text(v, now, second)
        if v["over"]:
            box.hidden = True
            ball.hidden = True
            self._place_icon(self.score_group, key, 29, 1, show=False)
            self._text(self.server, " ")
            self._text(self.num, " ")
            self._text(self.games, " ")
            self._place_timer(0.5, self.W // 2)
            self._text(self.timer, text)
            self._color(self.timer, color)
            return

        # middle of the top row: the sport icon; in tennis it takes turns with the games (or TB)
        show_games = v["mid"][0] is not None and (second // 6) % 2 == 0
        if v["mid"][0] is not None:
            games = "TB" if v["status"] == "TIEBREAK" else "%s-%s" % (v["mid"][0], v["mid"][1])
            self._text(self.games, games if show_games else " ")
            if text == "TB":
                text, color = fmt_secs(now - v["last_t"]), TIMER
        else:
            self._text(self.games, " ")
        self._place_icon(self.score_group, key, 29, 1, show=not show_games)

        s = v["serving"]
        server = v["server"]
        if server is not None:
            box.x = self.BOX_X[s]
            box.hidden = False
            self._fill(pal, SERVER_BOX)
            ball.hidden = True
            self.server.anchored_position = (self.BOX_X[s] + BOX_W // 2, self.BOX_Y + BOX_H // 2 + 1)
            self._text(self.server, v["players"][s][server[1]])
            self._color(self.server, SERVER_TEXT)
            self.num.anchored_position = (self.NUM_X[s], self.BOX_Y + BOX_H // 2 + 1)
            self._text(self.num, str(server[2]) if server[2] else " ")
        else:
            # no player to highlight (foosball): a ball marks the side to serve
            box.hidden = True
            self._text(self.server, " ")
            self._text(self.num, " ")
            ball.hidden = s is None
            if s is not None:
                ball.x = 2 if s == 0 else self.W - 5
                self._fill(ball_pal, 0xFFFFFF)
        # Timer on the side away from the serving team.
        if s == 1:
            self._place_timer(0.0, 0)
        else:
            self._place_timer(1.0, self.W)
        self._text(self.timer, text)
        self._color(self.timer, color)

    # ---------------------------------------------------------------- timed games

    def _build_timed(self):
        g = displayio.Group()
        W = self.W
        self.t_period = self._label(self.small, TIMER, (0.0, 0.0), (0, 0), g)
        self.t_clock = self._label(self.small, 0xFFFFFF, (1.0, 0.0), (W, 0), g)
        self.t_big = [self._label(self.big, TEAM_COLORS[t], (0.5, 1.0), (x, H), g) for t, x in ((0, 16), (1, 48))]
        self.t_mid = [self._label(self.mid, TEAM_COLORS[t], (0.5, 0.5), (x, 21), g) for t, x in ((0, 16), (1, 48))]
        self.t_icon_xy = (21, 1)
        self.timed_group = g

    def draw_timed(self, v, key, blink_on):
        if self.timed_group is None:
            self._build_timed()
        clock = v["clock"]
        if v["over"]:
            ctext, ccolor = "FINAL", 0xFFFFFF
        elif clock["running"]:
            ctext, ccolor = clock["text"], (0xFF2000 if clock["low"] else 0xFFFFFF)
        else:
            ctext, ccolor = clock["text"], ALERT  # stopped
        status = "TIED" if clock["tied"] else ""
        # period (or FINAL/TIED) on the left, the clock on the right
        if v["over"]:
            self._text(self.t_period, "FINAL")
            self._text(self.t_clock, " ")
        else:
            self._text(self.t_period, status or clock["period_text"])
            self._text(self.t_clock, ctext)
            self._color(self.t_clock, ccolor)
        self._place_icon(self.timed_group, key, *self.t_icon_xy, show=not v["over"])
        big_ok = max(int(b) for b in v["big"]) < 100
        for t in (0, 1):
            text = v["big"][t]
            off = v["over"] and v["winner"] == t and not blink_on
            self._text(self.t_big[t], text if big_ok else " ")
            self._text(self.t_mid[t], " " if big_ok else text)
            self._color(self.t_big[t], 0x000000 if off else TEAM_COLORS[t])
            self._color(self.t_mid[t], 0x000000 if off else TEAM_COLORS[t])

    # ---------------------------------------------------------------- misc: 2-6 teams

    def _build_multi(self, n):
        g = displayio.Group()
        col = self.W // n
        self.m_names, self.m_big, self.m_mid = [], [], []
        for i in range(n):
            cx = col * i + col // 2
            if i:
                rect, pal = self._rect(col * i - 1, 0, 1, H, g)
                rect.hidden = False
                self._fill(pal, 0x282828)
            self.m_names.append(self._label(self.tiny, TEAM_COLORS[i], (0.5, 0.0), (cx, 1), g))
            self.m_big.append(self._label(self.big, TEAM_COLORS[i], (0.5, 1.0), (cx, H), g))
            self.m_mid.append(self._label(self.mid, TEAM_COLORS[i], (0.5, 0.5), (cx, 20), g))
        self.multi_group = g
        self.multi_n = n
        self.multi_col = col

    def draw_multi(self, v, blink_on):
        n = len(v["big"])
        if self.multi_group is None or self.multi_n != n:
            self._build_multi(n)
        col = self.multi_col
        for i in range(n):
            self._text(self.m_names[i], v["names"][i].upper()[:max(1, (col - 2) // 5)])
            text = v["big"][i]
            fits_big = len(text) * 15 <= col - 2
            off = v["over"] and v["winner"] == i and not blink_on
            self._text(self.m_big[i], text if fits_big else " ")
            self._text(self.m_mid[i], " " if fits_big else text)
            self._color(self.m_big[i], 0x000000 if off else TEAM_COLORS[i])
            self._color(self.m_mid[i], 0x000000 if off else TEAM_COLORS[i])


class StackPanel(_Panel):
    """Two 64x32 panels stacked into a 64x64 board: team A on the top panel, team B below."""

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
        if group is self.anim.group and self._base is not None:
            self.stage.append(self._base)
        self.stage.append(group)

    def _is_shown(self, group):
        return self._shown is group

    def _anim_area(self, t, n):
        if n > 2:  # misc with 3+ teams: a burst over that team's cell
            x, y, w, h = self._cells(n)[t]
            return x + w // 2, y + h // 2, 0, False
        return None, None, self.OY[t], True  # the scorer's panel

    # ---------------------------------------------------------------- start-up

    def start_screen(self, net, step):
        if net.get("mode") == "ap":
            pairs = [("WI-FI", net["ap_ssid"]), ("PASSWORD", net["ap_password"]), ("OPEN", net["ip"])]
        elif net.get("mode") == "station":
            pairs = [("OPEN", net["ip"])]
        else:
            pairs = [("NO WI-FI", "")]
        top, bottom = pairs[step % len(pairs)]
        self.message(small_top=top, small=bottom.upper()[:10], top_color=0xFFC800)

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

    COURT_Y = 12  # court top within a panel; each court is two 22x20 service courts
    COURT_COLOR = 45  # percent of the team colour

    def _build_court(self):
        g = displayio.Group()
        W = self.W
        self.c_names = [self._label(self.small, TEAM_COLORS[t], (0.0, 0.0), (1, self.OY[t]), g) for t in (0, 1)]
        self.c_timer = self._label(self.small, TIMER, (1.0, 0.0), (W, 0), g)  # top panel, right
        self.c_dots = [[self._rect(x, oy + self.DOT_Y, 2, 2, g) for x in self.DOT_X] for oy in self.OY]
        self.c_big = [self._label(self.big, SCORE, (0.5, 1.0), (54, oy + H), g) for oy in self.OY]
        self.c_mid = [self._label(self.mid, SCORE, (0.5, 0.5), (54, oy + 21), g) for oy in self.OY]
        self.c_hl = []  # green frame around the serving player's court
        self.c_slots = []  # [team][column] player name; column 0 = left on the board
        self.c_num = []  # server number (1 or 2), above the serving player's name
        for t, oy in enumerate(self.OY):
            y = oy + self.COURT_Y
            frame, pal = self._outline(0, y, 44, 20, g)  # court outline and centre line, a dim team colour
            frame.hidden = False
            self._fill(pal, _scale(TEAM_COLORS[t], self.COURT_COLOR))
            self.c_hl.append(self._rect(0, y, 22, 20, g))  # solid green behind the server's name
            self.c_slots.append([self._label(self.tiny, PLAYER, (0.5, 0.5), (11 + 22 * c, y + 10), g) for c in (0, 1)])
            self.c_num.append(self._label(self.tiny, SERVER_NUM, (0.5, 0.5), (11, y + 4), g))
        self.court_group = g

    def draw_court(self, v, now, second, blink_on, key):
        """Birds-eye view with the net at the seam between the panels: each team's half of the
        court with its players where they stand, the server framed in green, the score beside it.
        Team A faces down and team B faces up, so a team's right-hand court is on the board's
        left for A and on its right for B."""
        if self.court_group is None:
            self._build_court()
        self._place_icon(self.court_group, key, self.W - 9, self.OY[1] + 1)  # bottom panel, top right
        server = v["server"]
        for t, oy in enumerate(self.OY):
            self._text(self.c_names[t], v["names"][t][:self.NAME_CHARS].upper())
            self._color(self.c_names[t], 0x000000 if (v["over"] and v["winner"] == t and not blink_on) else TEAM_COLORS[t])
            text = v["big"][t]
            fits = len(text) * 15 <= 19
            self._text(self.c_big[t], text if fits else " ")
            self._text(self.c_mid[t], " " if fits else text)
            hl, hl_pal = self.c_hl[t]
            num = self.c_num[t]
            serve_col = None  # set once, below: never hide-then-show (the display refreshes in between)
            for side in ("R", "L"):
                # over:   birds-eye, A faces down and B faces up, so A's right court is on the board's left
                # mirror: the same, flipped left-right
                # same:   every team's right court on the board's right (as if both stood at one end)
                right_col = 1 if (self.court_mode == "same" or (self.court_mode == "over") == bool(t)) else 0
                col = right_col if side == "R" else 1 - right_col
                idx = v["courts"][t].get(side)
                players = v["players"][t]
                lbl = self.c_slots[t][col]
                y = oy + self.COURT_Y
                serving = idx is not None and server is not None and server[0] == t and server[1] == idx
                self._text(lbl, players[idx] if idx is not None and idx < len(players) else " ")
                self._color(lbl, 0x000000 if serving else PLAYER)  # black on the green block
                lbl.anchored_position = (11 + 22 * col, y + (14 if serving and server[2] else 10))
                if serving:
                    hl.x = 22 * col
                    hl.hidden = False
                    self._fill(hl_pal, SERVER_BOX)
                    num.base_color = 0x000000
                    num.color = 0
                    if server[2]:
                        num.anchored_position = (11 + 22 * col, y + 5)
                        self._text(num, str(server[2]))
        for t in (0, 1):
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
            self._text(self.names[t], v["names"][t][:self.NAME_CHARS].upper())
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
                    self._box(t, 1, rows[1], players[server[1]], True)
                else:
                    self._box(t, 1, rows[1], " ", False)
            else:
                for r in (0, 1):
                    if r >= len(players):
                        self._box(t, r, rows[r], " ", False)
                        continue
                    y = rows[r] if v["doubles"] else single
                    self._box(t, r, y, players[r], is_serving and server[1] == r)

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
            self._text(self.t_names[t], v["names"][t][:self.NAME_CHARS].upper())
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
            self._text(self.m_names[i], v["names"][i].upper()[:max(1, (w - 2) // 5)])
            text = v["big"][i]
            fits_big = self.m_big[i] is not None and len(text) * 15 <= w - 2
            off = v["over"] and v["winner"] == i and not blink_on
            color = 0x000000 if off else TEAM_COLORS[i]
            if self.m_big[i] is not None:
                self._text(self.m_big[i], text if fits_big else " ")
                self._color(self.m_big[i], color)
            self._text(self.m_mid[i], " " if fits_big else text)
            self._color(self.m_mid[i], color)
