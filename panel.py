# LED panel rendering for the scoreboard, for every sport, on one or two panels.
# Layouts are driven by Match.view() so they don't need to know the sport.
#
# Two panels (128x32):
#  y 0-9    TEAM A NAME     idle timer / TB     TEAM B NAME
#  y 10-11  games-won dots                      games-won dots
#  y 12-31  big score  [MIKE]2              2[ANA ]  big score
#                      [JO  ]                [BEN ]
#  Tennis: games in the current set replace the player rows; the server's
#  name sits below them.
#
# One panel (64x32):
#  y 0-9    [MIKE]2   4-3     0:42     (server box over the serving team;
#  y 12-31   big score  ::  big score    games in the middle for tennis;
#                                        timer on the far side)
#
# "Switch ends", scoring animations and the start-up screen take over the display.

import time

import displayio
import vectorio
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import label

import icons
from anim import Animator

H = 32

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


def make_panel(display, width):
    return CompactPanel(display) if width <= 64 else WidePanel(display)


class _Panel:
    """Shared plumbing: fonts, brightness, change detection, messages, animations."""

    W = 128

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

    def _outline(self, x, y, w, h):
        pal = displayio.Palette(2)
        pal[1] = 0x000000
        return _OutlineBox(self.score_group, pal, x, y, w, h), pal

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
                cx = (2 * t + 1) * self.W // (2 * n) if n > 2 else None
                self.anim.start(c["sport"], t, c["text"], c["level"], TEAM_COLORS[t], other, self.pct, cx)
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
        if self.display.root_group is self.splash_group:
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
            self.draw(v, app.clock(), second, blink_on, key)
            self._show(self.score_group)

    # ---------------------------------------------------------------- timed games (both widths)

    def _build_timed(self):
        g = displayio.Group()
        W = self.W
        if W > 64:
            self.t_names = [self._label(self.small, TEAM_COLORS[0], (0.0, 0.0), (1, 0), g),
                            self._label(self.small, TEAM_COLORS[1], (1.0, 0.0), (W - 1, 0), g)]
            self.t_period = self._label(self.small, TIMER, (0.0, 0.0), (61, 0), g)
            self.t_clock = self._label(self.mid, 0xFFFFFF, (0.5, 0.0), (W // 2, 11), g)
            self.t_status = self._label(self.tiny, ALERT, (0.5, 1.0), (W // 2, H), g)
            self.t_big = [self._label(self.big, SCORE, (0.5, 1.0), (x, H), g) for x in (22, W - 22)]
            self.t_mid = [self._label(self.mid, SCORE, (0.5, 0.5), (x, 21), g) for x in (22, W - 22)]
            self.t_icon_xy = (51, 1)
        else:
            self.t_names = None
            self.t_period = self._label(self.small, TIMER, (0.0, 0.0), (0, 0), g)
            self.t_clock = self._label(self.small, 0xFFFFFF, (1.0, 0.0), (W, 0), g)
            self.t_status = None
            self.t_big = [self._label(self.big, TEAM_COLORS[t], (0.5, 1.0), (x, H), g) for t, x in ((0, 16), (1, 48))]
            self.t_mid = [self._label(self.mid, TEAM_COLORS[t], (0.5, 0.5), (x, 21), g) for t, x in ((0, 16), (1, 48))]
            self.t_icon_xy = (21, 1)
        self.timed_group = g

    def draw_timed(self, v, key, blink_on):
        if self.timed_group is None:
            self._build_timed()
        clock = v["clock"]
        wide = self.W > 64
        if wide:
            for t in (0, 1):
                self._text(self.t_names[t], v["names"][t][:6].upper())
        if v["over"]:
            ctext, ccolor = "FINAL", 0xFFFFFF
        elif clock["running"]:
            ctext, ccolor = clock["text"], (0xFF2000 if clock["low"] else 0xFFFFFF)
        else:
            ctext, ccolor = clock["text"], ALERT  # stopped
        status = "TIED" if clock["tied"] else ""
        if wide:
            self._text(self.t_clock, ctext)
            self._color(self.t_clock, ccolor)
            self._text(self.t_period, clock["period_text"])
            self._text(self.t_status, status)
            self._place_icon(self.timed_group, key, *self.t_icon_xy)
        else:
            # one panel: period (or FINAL/TIED) on the left, the clock on the right
            if v["over"]:
                self._text(self.t_period, "FINAL")
                self._text(self.t_clock, " ")
            else:
                self._text(self.t_period, status or clock["period_text"])
                self._text(self.t_clock, ctext)
                self._color(self.t_clock, ccolor)
            self._place_icon(self.timed_group, key, *self.t_icon_xy, show=not v["over"])
        big_ok = max(int(b) for b in v["big"]) < (1000 if wide else 100)
        for t in (0, 1):
            text = v["big"][t]
            off = v["over"] and v["winner"] == t and not blink_on
            self._text(self.t_big[t], text if big_ok else " ")
            self._text(self.t_mid[t], " " if big_ok else text)
            base = SCORE if wide else TEAM_COLORS[t]
            self._color(self.t_big[t], 0x000000 if off else base)
            self._color(self.t_mid[t], 0x000000 if off else base)

    # ---------------------------------------------------------------- misc: 2-6 teams (both widths)

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


class WidePanel(_Panel):
    W = 128
    SCORE_X = (18, 110)
    BOX_X = (37, 69)
    NUM_X = (61, 66)
    ROWS_Y = (12, 22)
    SINGLE_Y = 17
    DOT_X = ((1, 4, 7), (124, 121, 118))
    DOT_Y = 10

    def __init__(self, display):
        super().__init__(display)
        W = self.W
        self.dots = [[self._rect(x, self.DOT_Y, 2, 2) for x in self.DOT_X[t]] for t in (0, 1)]
        self.boxes = [[self._outline(self.BOX_X[t], self.ROWS_Y[r], BOX_W, BOX_H) for r in (0, 1)] for t in (0, 1)]
        # serve marker when there's no player to highlight (foosball): beside the score
        self.balls = [self._rect(x, 13, 3, 3) for x in (34, 91)]
        self.names = [
            self._label(self.small, TEAM_COLORS[0], (0.0, 0.0), (1, 0)),
            self._label(self.small, TEAM_COLORS[1], (1.0, 0.0), (W - 1, 0)),
        ]
        self.timer = self._label(self.small, TIMER, (0.0, 0.0), (56, 0))  # sport icon sits to its left
        self.scores = [self._label(self.big, SCORE, (0.5, 1.0), (self.SCORE_X[t], H)) for t in (0, 1)]
        self.mids = [self._label(self.mid, GAMES, (0.5, 0.0), (self.BOX_X[t] + BOX_W // 2, 10)) for t in (0, 1)]
        self.inits = [
            [self._label(self.tiny, PLAYER, (0.5, 0.5), (self.BOX_X[t] + BOX_W // 2, self.ROWS_Y[r] + BOX_H // 2 + 1))
             for r in (0, 1)]
            for t in (0, 1)
        ]
        self.nums = [self._label(self.tiny, SERVER_NUM, (0.5, 0.5), (self.NUM_X[t], 0)) for t in (0, 1)]

    def start_screen(self, net, step):
        if net.get("mode") == "ap":
            lines = ["WIFI %s" % net["ap_ssid"].upper()[:12], "PASS %s" % net["ap_password"].upper()[:12], net["ip"]]
        elif net.get("mode") == "station":
            lines = [net["ip"], (net.get("host") or "").upper() or net["ip"]]
        else:
            lines = ["NO WI-FI"]
        self.message("SCOREBOARD", small=lines[step % len(lines)], top_color=0xFFC800)

    def _box(self, t, r, y, text, highlight):
        box, pal = self.boxes[t][r]
        lbl = self.inits[t][r]
        box.y = y
        box.hidden = not highlight
        self._fill(pal, SERVER_BOX)
        lbl.anchored_position = (self.BOX_X[t] + BOX_W // 2, y + BOX_H // 2 + 1)
        self._text(lbl, text)
        self._color(lbl, SERVER_TEXT if highlight else PLAYER)

    def draw(self, v, now, second, blink_on, key):
        server = v["server"]
        tennis_like = v["mid"][0] is not None
        self._place_icon(self.score_group, key, 45, 1)
        for t in (0, 1):
            self._text(self.names[t], v["names"][t][:6].upper())
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
                self._box(t, 0, self.ROWS_Y[0], " ", False)
                if is_serving:
                    self._box(t, 1, self.ROWS_Y[1], players[server[1]], True)
                else:
                    self._box(t, 1, self.ROWS_Y[1], " ", False)
            else:
                for r in (0, 1):
                    if r >= len(players):
                        self._box(t, r, self.ROWS_Y[r], " ", False)
                        continue
                    y = self.ROWS_Y[r] if v["doubles"] else self.SINGLE_Y
                    self._box(t, r, y, players[r], is_serving and server[1] == r)

            num = self.nums[t]
            if is_serving and server[2]:
                row_y = self.ROWS_Y[server[1]] if v["doubles"] else self.SINGLE_Y
                num.anchored_position = (self.NUM_X[t], row_y + BOX_H // 2 + 1)
                self._text(num, str(server[2]))
            else:
                self._text(num, " ")
        self._dots(self.dots, v)
        text, color = self._center_text(v, now, second)
        self._text(self.timer, text)
        self._color(self.timer, color)


class CompactPanel(_Panel):
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
