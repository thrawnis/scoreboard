# Short scoring animations, one per sport, drawn with vectorio shapes so they
# cost almost no flash or RAM. They scale to the display width (64 or 128).
#
#   pickleball  a holey yellow ball bouncing over the net
#   tennis      a fast ball with a motion trail skimming the net
#   badminton   a shuttlecock smashed steeply into the floor
#   pingpong    a quick rally on a table, then the winning shot
#   foosball    a shot between the rods into the goal, then "GOAL!"
#   volleyball  a high set, then a spike down over the net
#   racquetball the ball ricochets off the front wall and back
#   airhockey   a puck banks off the rails into the goal slot
#   shuffleboard a puck slides down the table into the scoring zones
#   basketball  a shot arcs into the hoop (timed games)
#   goal        soccer/hockey: a ball or puck into the net (timed games)
#   burst       football, custom timed games and misc: a starburst behind the caption
#
# The ball travels from the scorer's side (team 0 = left) toward the opponent.

import math
import time

import displayio
import vectorio
from adafruit_display_text import label

H = 32

# palette slots
CLEAR, WHITE, DIM, BALL, TEAM, OTHER, TRAIL1, TRAIL2, TABLE, BLACK = range(10)


def _scale(color, pct):
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return (r * pct // 100) << 16 | (g * pct // 100) << 8 | (b * pct // 100)


class Animator:
    # sport (or timed preset) -> animation
    STYLE = {"timed_basketball": "basketball", "timed_soccer": "goal", "timed_hockey": "goal",
             "timed_football": "burst", "timed_custom": "burst", "misc": "burst"}

    def __init__(self, width, font):
        self.W = width
        self.font = font
        self.group = displayio.Group()
        self.pal = displayio.Palette(10)
        self.pal.make_transparent(CLEAR)
        self.active = False
        self.caption = None

    # ---------------------------------------------------------------- building blocks

    def _circle(self, r, idx):
        c = vectorio.Circle(pixel_shader=self.pal, radius=r, x=-10, y=-10, color_index=idx)
        self.group.append(c)
        return c

    def _rect(self, x, y, w, h, idx):
        r = vectorio.Rectangle(pixel_shader=self.pal, width=max(1, int(w)), height=max(1, int(h)),
                               x=int(x), y=int(y), color_index=idx)
        self.group.append(r)
        return r

    def _poly(self, idx):
        p = vectorio.Polygon(pixel_shader=self.pal, points=[(0, 0), (1, 0), (0, 1)], x=-10, y=-10, color_index=idx)
        self.group.append(p)
        return p

    @staticmethod
    def _move(shape, x, y):
        shape.x = int(x)
        shape.y = int(y)

    # ---------------------------------------------------------------- control

    def start(self, sport, team, text, level, team_color, other_color, pct, cx=None):
        while len(self.group):
            self.group.pop()
        pal = self.pal
        for idx, color in ((WHITE, 0xFFFFFF), (DIM, 0x505050), (TEAM, team_color), (OTHER, other_color),
                           (TABLE, 0x0030A0), (BLACK, 0x000000)):
            pal[idx] = _scale(color, pct)
        sport = self.STYLE.get(sport, sport)
        ball = {"pickleball": 0xFFD800, "tennis": 0xC8FF20, "badminton": 0xFFFFFF, "pingpong": 0xFF8000,
                "foosball": 0xFFFFFF, "volleyball": 0xFFE060, "racquetball": 0x2060FF, "airhockey": 0xE0E0E0,
                "shuffleboard": team_color, "basketball": 0xFF6000, "goal": 0xFFFFFF}.get(sport, 0xFFFFFF)
        pal[BALL] = _scale(ball, pct)
        pal[TRAIL1] = _scale(ball, pct * 45 // 100)
        pal[TRAIL2] = _scale(ball, pct * 18 // 100)

        self.sport = sport
        self.dir = 1 if team == 0 else -1
        self.x0 = self.W * 0.14 if team == 0 else self.W * 0.86
        self.big = level in ("unit", "match")
        self.duration = 2.6 if self.big else 1.8
        self.t0 = time.monotonic()
        self.team_color = _scale(team_color, pct)
        self.cx = self.W // 2 if cx is None else int(cx)
        self.shapes = getattr(self, "_setup_" + sport, self._setup_pickleball)()
        cap_y = H // 2 if sport in ("foosball", "burst") else 7
        self.caption = label.Label(self.font, text=text.upper()[:9], color=self.team_color, anchor_point=(0.5, 0.5))
        cap_x = self.cx if sport == "burst" else self.W // 2
        half = self.caption.bounding_box[2] // 2 + 1
        cap_x = max(half, min(self.W - half, cap_x))  # keep the caption on the display
        self.caption.anchored_position = (cap_x, cap_y)
        self.caption.hidden = True
        self.group.append(self.caption)
        self.active = True

    def step(self):
        """Advance the animation; returns False once it has finished."""
        if not self.active:
            return False
        tn = (time.monotonic() - self.t0) / self.duration
        if tn >= 1:
            self.active = False
            return False
        getattr(self, "_step_" + self.sport, self._step_pickleball)(tn)
        return True

    def _show_caption(self, tn, start):
        cap = self.caption
        cap.hidden = tn < start
        if not cap.hidden and self.big:
            cap.color = self.team_color if int(tn * 12) % 2 == 0 else self.pal[WHITE]

    # ---------------------------------------------------------------- pickleball

    def _setup_pickleball(self):
        W = self.W
        self._rect(0, H - 1, W, 1, DIM)
        self._rect(W // 2, H - 10, 1, 10, WHITE)
        ball = self._circle(4, BALL)
        holes = [self._circle(1, BLACK), self._circle(1, BLACK)]
        return {"ball": ball, "holes": holes}

    def _step_pickleball(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.75)
        x = self.x0 + self.dir * self.W * 0.72 * p
        arc = abs(math.sin(math.pi * p * 2.2)) * (19 - 9 * p)
        y = H - 5 - arc
        visible = tn < 0.8
        for shape, (dx, dy) in ((s["ball"], (0, 0)), (s["holes"][0], (-1, -1)), (s["holes"][1], (2, 1))):
            shape.hidden = not visible
            self._move(shape, x + dx, y + dy)
        self._show_caption(tn, 0.45)

    # ---------------------------------------------------------------- tennis

    def _setup_tennis(self):
        W = self.W
        self._rect(0, H - 1, W, 1, WHITE)
        self._rect(W // 2, H - 12, 1, 12, WHITE)
        trail = [self._circle(2, TRAIL2), self._circle(2, TRAIL1)]
        ball = self._circle(3, BALL)
        return {"ball": ball, "trail": trail}

    def _tennis_pos(self, p):
        p = max(0.0, min(1.0, p))
        x = self.x0 + self.dir * self.W * 0.9 * p
        # skims the net, lands deep, kicks up
        y = 9 + 20 * p * p if p < 0.8 else 29 - 30 * (p - 0.8)
        return x, y

    def _step_tennis(self, tn):
        s = self.shapes
        p = tn / 0.5
        visible = p < 1.15
        for shape, lag in ((s["ball"], 0), (s["trail"][1], 0.07), (s["trail"][0], 0.14)):
            shape.hidden = not visible or p - lag < 0
            x, y = self._tennis_pos(p - lag)
            self._move(shape, x, y)
        self._show_caption(tn, 0.45)

    # ---------------------------------------------------------------- badminton

    def _setup_badminton(self):
        W = self.W
        self._rect(0, H - 1, W, 1, DIM)
        self._rect(W // 2, H - 12, 1, 12, WHITE)
        feathers = self._poly(DIM)
        cork = self._circle(2, BALL)
        burst = [self._rect(-5, -5, 1, 1, TEAM) for _ in range(4)]
        return {"cork": cork, "feathers": feathers, "burst": burst}

    def _step_badminton(self, tn):
        s = self.shapes
        W = self.W
        x1 = self.x0 + self.dir * W * 0.62
        p = min(1.0, tn / 0.4)
        e = p * p  # accelerating smash
        x = self.x0 + (x1 - self.x0) * e
        y = 2 + (H - 5) * e
        vx, vy = (x1 - self.x0), (H - 7)
        n = math.sqrt(vx * vx + vy * vy) or 1
        vx, vy = vx / n, vy / n
        px, py = -vy, vx
        s["cork"].hidden = p >= 1 and tn > 0.55
        self._move(s["cork"], x, y)
        f = s["feathers"]
        f.hidden = s["cork"].hidden
        f.points = [(0, 0), (int(-7 * vx + 3 * px), int(-7 * vy + 3 * py)), (int(-7 * vx - 3 * px), int(-7 * vy - 3 * py))]
        self._move(f, x, y)
        # impact burst
        k = (tn - 0.4) / 0.2
        for i, r in enumerate(s["burst"]):
            r.hidden = not (0 <= k <= 1)
            if not r.hidden:
                ang = math.pi * (0.15 + 0.23 * i)
                d = 3 + 6 * k
                self._move(r, x1 + d * math.cos(ang) * (1 if i % 2 else -1), H - 3 - d * math.sin(ang))
        self._show_caption(tn, 0.45)

    # ---------------------------------------------------------------- ping pong

    def _setup_pingpong(self):
        W = self.W
        left, width = int(W * 0.08), int(W * 0.84)
        self._rect(left, H - 5, width, 3, TABLE)
        self._rect(left, H - 5, width, 1, WHITE)
        self._rect(W // 2, H - 10, 1, 5, WHITE)
        self._rect(left + 2, H - 2, 2, 2, DIM)
        self._rect(left + width - 4, H - 2, 2, 2, DIM)
        ball = self._circle(2, BALL)
        return {"ball": ball, "left": left, "right": left + width}

    def _step_pingpong(self, tn):
        s = self.shapes
        near = s["left"] + 3 if self.dir == 1 else s["right"] - 3
        far = s["right"] - 3 if self.dir == 1 else s["left"] + 3
        out = far + self.dir * self.W * 0.2
        hops = ((near, far), (far, near), (near, out))  # rally, then the winner
        seg_len = 0.22
        i = int(tn / seg_len)
        ball = s["ball"]
        if i >= len(hops):
            ball.hidden = True
        else:
            a, b = hops[i]
            q = (tn - i * seg_len) / seg_len
            x = a + (b - a) * q
            y = H - 8 - 11 * math.sin(math.pi * q)
            ball.hidden = False
            self._move(ball, x, y)
        self._show_caption(tn, 0.6)

    # ---------------------------------------------------------------- foosball

    def _setup_foosball(self):
        W = self.W
        goal_x = W - 3 if self.dir == 1 else 0
        post_x = W - 6 if self.dir == 1 else 0
        net = self._rect(goal_x, 10, 3, 12, DIM)
        self._rect(post_x, 9, 6, 1, WHITE)
        self._rect(post_x, 22, 6, 1, WHITE)
        rods = []
        for frac, idx in ((0.33, OTHER), (0.66, TEAM)):
            x = int(W * frac)
            rods.append(self._rect(x, 0, 1, H, DIM))
            for y in (4, 14, 24):
                rods.append(self._rect(x - 1, y, 3, 4, idx))
        ball = self._circle(2, BALL)
        return {"ball": ball, "net": net, "goal_x": goal_x, "rods": rods}

    def _step_foosball(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.3)
        start = self.W / 2 - self.dir * self.W * 0.1
        x = start + (s["goal_x"] + 1 - start) * p
        y = 20 - 6 * math.sin(math.pi * p)
        s["ball"].hidden = p >= 1
        self._move(s["ball"], x, y)
        s["net"].color_index = TEAM if (p >= 1 and int(tn * 10) % 2 == 0) else DIM
        cap = self.caption
        cap.hidden = tn < 0.3
        for rod in s["rods"]:
            rod.hidden = not cap.hidden  # clear the field so GOAL! reads cleanly
        if not cap.hidden:
            cap.color = self.team_color if int(tn * 8) % 2 == 0 else self.pal[WHITE]

    # ---------------------------------------------------------------- volleyball

    def _setup_volleyball(self):
        W = self.W
        self._rect(0, H - 1, W, 1, DIM)
        self._rect(W // 2, H - 16, 1, 16, WHITE)
        self._rect(W // 2 - 1, H - 16, 3, 1, WHITE)
        ball = self._circle(3, BALL)
        seam = self._circle(1, TEAM)
        return {"ball": ball, "seam": seam}

    def _step_volleyball(self, tn):
        s = self.shapes
        W = self.W
        net = W // 2
        top_x = net - self.dir * W * 0.12
        land = net + self.dir * W * 0.3
        if tn < 0.3:  # the set: straight up
            q = tn / 0.3
            x, y = top_x, H - 4 - 22 * math.sin(math.pi * 0.5 * q)
        else:  # the spike: steep and fast over the net
            q = min(1.0, (tn - 0.3) / 0.2)
            x = top_x + (land - top_x) * q
            y = 6 + (H - 10) * q * q
        visible = tn < 0.75
        s["ball"].hidden = not visible
        s["seam"].hidden = not visible
        self._move(s["ball"], x, y)
        self._move(s["seam"], x + 1, y - 1)
        self._show_caption(tn, 0.5)

    # ---------------------------------------------------------------- racquetball

    def _setup_racquetball(self):
        W = self.W
        wall_x = W - 2 if self.dir == 1 else 0
        self._rect(wall_x, 0, 2, H, WHITE)
        self._rect(0, H - 1, W, 1, DIM)
        trail = self._circle(2, TRAIL1)
        ball = self._circle(2, BALL)
        return {"ball": ball, "trail": trail, "wall_x": wall_x}

    def _racquet_pos(self, p):
        W = self.W
        wall = W - 5 if self.dir == 1 else 4
        start = self.x0
        if p < 0.5:  # to the front wall, high
            q = p / 0.5
            return start + (wall - start) * q, 18 - 10 * q
        q = (p - 0.5) / 0.5  # rebound low and fast, bouncing once
        x = wall + (start - self.dir * W * 0.1 - wall) * q
        return x, 8 + 22 * abs(math.sin(math.pi * q * 0.9))

    def _step_racquetball(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.6)
        for shape, lag in ((s["ball"], 0), (s["trail"], 0.06)):
            x, y = self._racquet_pos(max(0.0, p - lag))
            shape.hidden = tn > 0.7
            self._move(shape, x, y)
        self._show_caption(tn, 0.55)

    # ---------------------------------------------------------------- air hockey

    def _setup_airhockey(self):
        W = self.W
        self._rect(0, 0, W, 1, DIM)
        self._rect(0, H - 1, W, 1, DIM)
        self._rect(W // 2, 0, 1, H, DIM)
        goal_x = W - 2 if self.dir == 1 else 0
        slot = self._rect(goal_x, 11, 2, 10, OTHER)
        mallet = self._circle(4, TEAM)
        knob = self._circle(1, WHITE)
        puck = self._circle(3, BALL)
        return {"puck": puck, "mallet": mallet, "knob": knob, "slot": slot, "goal_x": goal_x}

    def _step_airhockey(self, tn):
        s = self.shapes
        W = self.W
        p = min(1.0, tn / 0.45)
        start_x = self.x0
        end_x = s["goal_x"] + (1 if self.dir == 1 else 1)
        x = start_x + (end_x - start_x) * p
        # bank shot: off the top rail, then down into the slot
        y = 16 - 12 * math.sin(math.pi * min(1.0, p * 1.6)) if p < 0.62 else 4 + 12 * (p - 0.62) / 0.38
        s["puck"].hidden = p >= 1
        self._move(s["puck"], x, y)
        mx = start_x - self.dir * 6 + (self.dir * 4 if tn < 0.1 else 0)
        self._move(s["mallet"], mx, 16)
        self._move(s["knob"], mx, 16)
        s["slot"].color_index = TEAM if (p >= 1 and int(tn * 10) % 2 == 0) else OTHER
        cap = self.caption
        cap.hidden = tn < 0.45
        if not cap.hidden:
            cap.color = self.team_color if int(tn * 8) % 2 == 0 else self.pal[WHITE]

    # ---------------------------------------------------------------- shuffleboard

    def _setup_shuffleboard(self):
        W = self.W
        far = W - 1 if self.dir == 1 else 0
        zones = []
        for i in range(3):  # scoring lines 1, 2, 3 toward the far end
            x = far - self.dir * (6 + 7 * i)
            zones.append(self._rect(x, 6, 1, 20, DIM))
        self._rect(0, 5, W, 1, DIM)
        self._rect(0, 26, W, 1, DIM)
        puck = self._circle(3, BALL)
        rim = self._circle(1, WHITE)
        return {"puck": puck, "rim": rim, "far": far}

    def _step_shuffleboard(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.6)
        ease = 1 - (1 - p) * (1 - p) * (1 - p)  # slides and slows to a stop
        start = self.x0
        stop = s["far"] - self.dir * 8
        x = start + (stop - start) * ease
        self._move(s["puck"], x, 16)
        self._move(s["rim"], x, 16)
        self._show_caption(tn, 0.55)

    # ---------------------------------------------------------------- basketball

    def _setup_basketball(self):
        W = self.W
        hoop_x = W - 14 if self.dir == 1 else 6
        board_x = W - 4 if self.dir == 1 else 3
        self._rect(board_x, 4, 1, 12, WHITE)
        self._rect(hoop_x, 13, 8, 1, BALL)
        net = [self._rect(hoop_x + 1 + 2 * i, 14, 1, 5, DIM) for i in range(4)]
        ball = self._circle(3, BALL)
        return {"ball": ball, "hoop_x": hoop_x, "net": net}

    def _step_basketball(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.55)
        start_x, start_y = self.x0, H - 6
        end_x, end_y = s["hoop_x"] + 4, 16
        x = start_x + (end_x - start_x) * p
        y = start_y + (end_y - start_y) * p - 20 * math.sin(math.pi * p)
        s["ball"].hidden = tn > 0.75
        self._move(s["ball"], x, y)
        swish = 0.55 <= tn <= 0.75
        for i, r in enumerate(s["net"]):
            r.color_index = TEAM if swish and (int(tn * 20) + i) % 2 == 0 else DIM
        self._show_caption(tn, 0.55)

    # ---------------------------------------------------------------- soccer / hockey goal

    def _setup_goal(self):
        W = self.W
        goal_x = W - 4 if self.dir == 1 else 0
        post_x = W - 8 if self.dir == 1 else 0
        net = self._rect(goal_x, 9, 4, 14, DIM)
        self._rect(post_x, 8, 8, 1, WHITE)
        self._rect(post_x, 23, 8, 1, WHITE)
        self._rect(0, H - 1, W, 1, DIM)
        ball = self._circle(2, BALL)
        return {"ball": ball, "net": net, "goal_x": goal_x}

    def _step_goal(self, tn):
        s = self.shapes
        p = min(1.0, tn / 0.35)
        x = self.x0 + (s["goal_x"] + 2 - self.x0) * p
        y = 24 - 10 * math.sin(math.pi * p * 0.8)
        s["ball"].hidden = p >= 1
        self._move(s["ball"], x, y)
        s["net"].color_index = TEAM if (p >= 1 and int(tn * 10) % 2 == 0) else DIM
        cap = self.caption
        cap.hidden = tn < 0.35
        if not cap.hidden:
            cap.color = self.team_color if int(tn * 8) % 2 == 0 else self.pal[WHITE]

    # ---------------------------------------------------------------- burst

    def _setup_burst(self):
        rays = [self._rect(-5, -5, 2, 2, TEAM if i % 2 else WHITE) for i in range(12)]
        return {"rays": rays}

    def _step_burst(self, tn):
        s = self.shapes
        cx, cy = self.cx, H // 2
        r = 4 + 28 * min(1.0, tn / 0.5)
        fade = tn > 0.8
        for i, ray in enumerate(s["rays"]):
            ang = math.pi * 2 * i / len(s["rays"]) + tn
            ray.hidden = fade
            self._move(ray, cx + r * math.cos(ang), cy + r * 0.45 * math.sin(ang))
        cap = self.caption
        cap.hidden = tn < 0.1
        if not cap.hidden and self.big:
            cap.color = self.team_color if int(tn * 12) % 2 == 0 else self.pal[WHITE]
