# The scoring animation: short and the same for every sport, played on the scoring team's half of
# the board (64x32, under a black backdrop). A frame in the team's colour grows out from the middle,
# then the caption ("+1", "GOAL!", "GAME", "MATCH"...) appears in the middle. For a game or match the
# caption flashes and the animation lasts longer.
#
# Every shape is made once and only shown or hidden while it plays (nothing is resized or rebuilt),
# which keeps it cheap in RAM and free of flicker.

import time

import displayio
import vectorio
from adafruit_display_text import label

H = 32  # one team's half of the board
FRAMES = 6  # sizes of the growing frame
CLEAR, TEAM, BLACK = range(3)  # palette slots


def _scale(color, pct):
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return (r * pct // 100) << 16 | (g * pct // 100) << 8 | (b * pct // 100)


class Animator:
    def __init__(self, width, font):
        self.W = width
        self.group = displayio.Group()
        self.pal = displayio.Palette(3)
        self.pal.make_transparent(CLEAR)
        self.pal[BLACK] = 0x000000
        self.active = False
        self.backdrop = self._rect(0, 0, width, H, BLACK)
        # FRAMES concentric outlines (4 thin rectangles each), smallest first, 1 pixel inside the half
        self.frames = []
        for k in range(1, FRAMES + 1):
            hw = (width // 2 - 1) * k // FRAMES
            hh = (H // 2 - 1) * k // FRAMES
            x, y, w, h = width // 2 - hw, H // 2 - hh, 2 * hw, 2 * hh
            edges = [self._rect(x, y, w, 1, TEAM), self._rect(x, y + h - 1, w, 1, TEAM),
                     self._rect(x, y, 1, h, TEAM), self._rect(x + w - 1, y, 1, h, TEAM)]
            for e in edges:
                e.hidden = True
            self.frames.append(edges)
        self.caption = label.Label(font, text=" ", color=0xFFFFFF, anchor_point=(0.5, 0.5))
        self.caption.anchored_position = (width // 2, H // 2)
        self.caption.hidden = True
        self.group.append(self.caption)
        self.shown = None  # index of the frame on screen

    def _rect(self, x, y, w, h, idx):
        r = vectorio.Rectangle(pixel_shader=self.pal, width=w, height=h, x=x, y=y, color_index=idx)
        self.group.append(r)
        return r

    def _show_frame(self, k):
        if k == self.shown:
            return
        if self.shown is not None:
            for e in self.frames[self.shown]:
                e.hidden = True
        for e in self.frames[k]:
            e.hidden = False
        self.shown = k

    def start(self, team, text, level, team_color, pct, oy=0, cover=True):
        """Play on the half that starts at row `oy`; `level` is "point", "game", "unit" or "match"."""
        self.pal[TEAM] = _scale(team_color, pct)
        self.team_color = _scale(team_color, pct)
        self.white = _scale(0xFFFFFF, pct)
        self.big = level in ("unit", "match")
        self.duration = 2.4 if self.big else 1.2
        self.group.y = oy
        self.backdrop.hidden = not cover
        self.caption.text = (text or " ").upper()[:9]
        self.caption.color = self.team_color
        self.caption.hidden = True
        self._show_frame(0)
        self.t0 = time.monotonic()
        self.active = True

    def step(self):
        """Advance the animation; returns False once it has finished."""
        if not self.active:
            return False
        tn = (time.monotonic() - self.t0) / self.duration
        if tn >= 1:
            self.active = False
            return False
        grow = min(1.0, tn / 0.4)  # the frame reaches the edges at 40% of the time
        self._show_frame(min(FRAMES - 1, int(grow * FRAMES)))
        if tn >= 0.3:
            if self.caption.hidden:
                self.caption.hidden = False
            if self.big:  # a game or match: the caption flashes
                color = self.team_color if int(tn * 12) % 2 == 0 else self.white
                if self.caption.color != color:
                    self.caption.color = color
        return True
