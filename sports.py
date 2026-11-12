# Scoring engines for every sport the scoreboard supports.
# Pure Python: runs on CircuitPython and CPython.
#
# A match is a series of "units" (games; sets in tennis and volleyball). Each
# unit is a list of events; the current state is rebuilt by replaying the
# events still in effect, so "undo" is just another logged event and the full
# change history is kept.
#
# Event kinds ("k"):
#   r     point/rally won by team "tm" (timed games: "n" points)
#   pos   before the first point: serving team "sv", per-team player choice "rt"
#   adj   manual correction (fields depend on the sport, see adjust_spec)
#   u     undo the most recent undoable event still in effect
# Game clock (timed games; time passing can't be undone, so undo skips these):
#   cs start, cp stop, cset set to "secs", cexp period ran out, cper next period, cot overtime
#   cend  end the game now (undoable)

SPORTS = ("pickleball", "tennis", "badminton", "pingpong", "volleyball", "racquetball",
          "foosball", "airhockey", "shuffleboard", "timed")

DEFAULTS = {
    "pickleball": {"mode": "doubles", "scoring": "sideout", "points": 11, "win_by": 2, "games": 1, "switch_ends": True},
    "tennis": {"mode": "singles", "games": 3, "set_games": 6, "no_ad": False, "final_tb": False, "switch_ends": True},
    "badminton": {"mode": "singles", "points": 21, "games": 3, "switch_ends": True},
    "pingpong": {"mode": "singles", "points": 11, "games": 3, "switch_ends": True},
    "volleyball": {"variant": "indoor", "games": 5, "switch_ends": True},
    "racquetball": {"mode": "singles", "games": 3},
    "foosball": {"mode": "doubles", "points": 5, "win_by": 1, "games": 3},
    "airhockey": {"mode": "singles", "points": 7, "win_by": 1, "games": 1},
    "shuffleboard": {"mode": "singles", "points": 15, "games": 1},
    "timed": {"preset": "basketball", "periods": 4, "minutes": 12, "count": "down", "buttons": [1, 2, 3], "games": 1},
}

TIMED_PRESETS = {
    "basketball": {"periods": 4, "minutes": 12, "count": "down", "buttons": [1, 2, 3], "label": "Q", "ot": 5},
    "soccer": {"periods": 2, "minutes": 45, "count": "up", "buttons": [1], "label": "H", "ot": 15},
    "hockey": {"periods": 3, "minutes": 20, "count": "down", "buttons": [1], "label": "P", "ot": 5},
    "football": {"periods": 4, "minutes": 15, "count": "down", "buttons": [6, 3, 2, 1], "label": "Q", "ot": 10},
    "custom": {"periods": 2, "minutes": 10, "count": "down", "buttons": [1, 2, 3], "label": "P", "ot": 5},
}

WITH_MODE = ("pickleball", "tennis", "badminton", "pingpong", "racquetball", "foosball", "airhockey", "shuffleboard")

# Effect strings shown on the "won rally" buttons and stored in the change log.
POINT = "+1 point"
SECOND = "2nd server"
SIDEOUT = "Side out"
POINT_SIDEOUT = "+1 & serve"

CLOCK_KINDS = ("cs", "cp", "cset", "cexp", "cper", "cot")  # not undoable


def default_cfg(sport="pickleball"):
    cfg = dict(DEFAULTS[sport])
    if "buttons" in cfg:
        cfg["buttons"] = list(cfg["buttons"])
    cfg["sport"] = sport
    return cfg


def clean_cfg(raw):
    raw = raw if isinstance(raw, dict) else {}
    sport = raw.get("sport") if raw.get("sport") in SPORTS else "pickleball"
    cfg = default_cfg(sport)
    if sport in WITH_MODE and raw.get("mode") in ("doubles", "singles"):
        cfg["mode"] = raw["mode"]
    if "scoring" in cfg and raw.get("scoring") in ("sideout", "rally"):
        cfg["scoring"] = raw["scoring"]
    for key, lo, hi in (("points", 1, 99), ("win_by", 1, 5), ("set_games", 1, 9)):
        if key in cfg and key in raw:
            cfg[key] = max(lo, min(hi, int(raw[key])))
    if sport != "timed" and raw.get("games") in (1, 3, 5, 7):
        cfg["games"] = raw["games"]
    for key in ("switch_ends", "no_ad", "final_tb"):
        if key in cfg and key in raw:
            cfg[key] = bool(raw[key])
    if sport == "volleyball":
        if raw.get("variant") in ("indoor", "beach"):
            cfg["variant"] = raw["variant"]
        if raw.get("games") not in (1, 3, 5):
            cfg["games"] = 5 if cfg["variant"] == "indoor" else 3
    if sport == "timed":
        preset = raw.get("preset") if raw.get("preset") in TIMED_PRESETS else "basketball"
        p = TIMED_PRESETS[preset]
        cfg.update(preset=preset, periods=p["periods"], minutes=p["minutes"], count=p["count"], buttons=list(p["buttons"]))
        if "periods" in raw:
            cfg["periods"] = max(1, min(9, int(raw["periods"])))
        if "minutes" in raw:
            cfg["minutes"] = max(1, min(90, int(raw["minutes"])))
        if preset == "custom":
            if raw.get("count") in ("up", "down"):
                cfg["count"] = raw["count"]
            buttons = raw.get("buttons")
            if isinstance(buttons, list):
                clean = []
                for b in buttons[:4]:
                    v = int(b)
                    if 1 <= v <= 9 and v not in clean:
                        clean.append(v)
                if clean:
                    cfg["buttons"] = clean
    return cfg


def team_count(cfg):
    return 2  # every sport is two teams (the generic 2-6 team scorer was removed)


def players_per_team(cfg):
    sport = cfg.get("sport", "pickleball")
    if sport == "timed":
        return 0
    if sport == "volleyball":
        return 2 if cfg.get("variant") == "beach" else 0
    return 2 if cfg.get("mode") == "doubles" else 1


PLAYER_MIN, PLAYER_MAX = 1, 5


def player_error(name):
    """Why a player name isn't allowed, or None. Names are 1-5 printable ASCII characters."""
    if not isinstance(name, str):
        return "player names are required"
    name = name.strip()
    if not PLAYER_MIN <= len(name) <= PLAYER_MAX:
        return "player names must be %d-%d characters" % (PLAYER_MIN, PLAYER_MAX)
    for ch in name:
        if not " " <= ch <= "~":  # the board's fonts are ASCII only
            return "player names can only use letters, numbers and basic symbols"
    return None


def _team_raw(raw, i):
    return raw[i] if isinstance(raw, list) and i < len(raw) and isinstance(raw[i], dict) else {}


def teams_error(raw, cfg):
    count = players_per_team(cfg)
    for i in range(team_count(cfg)):
        t = _team_raw(raw, i)
        players = t.get("players") if isinstance(t.get("players"), list) else []
        for p in range(count):
            err = player_error(players[p] if p < len(players) else None)
            if err:
                return err
        name = str(t.get("name") or "")
        for ch in name:
            if not " " <= ch <= "~":
                return "team names can only use letters, numbers and basic symbols"
    return None


def clean_teams(raw, cfg):
    teams = []
    count = players_per_team(cfg)
    for i in range(team_count(cfg)):
        t = _team_raw(raw, i)
        name = str(t.get("name") or ("TEAM %s" % "ABCDEF"[i])).strip()[:16]
        players = t.get("players") if isinstance(t.get("players"), list) else []
        clean = []
        for p in range(count):
            clean.append(str(players[p]).strip().upper()[:PLAYER_MAX] if p < len(players) and players[p] else "P%d" % (p + 1))
        teams.append({"name": name, "players": clean})
    return teams


def _int(v, lo, hi):
    return max(lo, min(hi, int(v)))


def _bit(v):
    return 1 if int(v) else 0


def _leader(scores):
    best = max(scores)
    return scores.index(best) if scores.count(best) == 1 else None


# ---------------------------------------------------------------- base


class BaseGame:
    unit = "GAME"  # what one record in the match is called
    verb = "won point"  # "<team> won point"
    point_word = "+1"  # animation caption for an ordinary point
    courts = False  # tracks left/right court positions (court diagram)
    setup_label = "serves first"  # per-team player choice before the first point (doubles)
    show_server = True  # highlight the server's name
    layout = "standard"  # panel layout: standard or timed
    buttons = None  # point values for "+N" buttons instead of a single "won rally" button
    allow_minus = False
    # Attributes that make up the scoring state (used to preview a point without applying it).
    STATE = ("scores", "winner", "switched", "switch_now", "scored", "level",
             "serving", "server_idx", "server_num", "right", "first_serving")

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        self.cfg = cfg
        self.n = team_count(cfg)
        self.players = players_per_team(cfg)
        self.doubles = self.players == 2
        self.final_game = final_game
        self.scores = [0] * self.n
        self.winner = None
        self.switched = False
        self.switch_now = False
        self.scored = None  # team that scored on the last event
        self.points_scored = 1
        self.level = None  # "point", "game" (tennis) or "unit"
        self.last_t = start_t
        self.rallies = 0
        self.server_num = 0
        self._place(serving, right)

    @property
    def over(self):
        return self.winner is not None

    def _place(self, serving, right):
        self.serving = serving
        self.first_serving = serving
        self.right = [right[0], right[1]]
        self.server_idx = self.right[serving] if self.doubles else 0

    # -- events

    def apply(self, e):
        """Apply one event. Returns an error string or None."""
        self.switch_now = False
        self.scored = None
        self.level = None
        k = e.get("k")
        if k == "r":
            if self.over:
                return "%s is over" % self.unit.lower()
            err = self._score(e)
            if err:
                return err
            self.rallies += 1
            self.last_t = e["t"]
            if self.scored is not None and self.level is None:
                self.level = "unit" if self.over else "point"
            return None
        if k == "pos":
            if self.rallies or self.scores != [0] * self.n:
                return "the serve can only be set before the first point"
            self._place(1 if e["sv"] else 0, e["rt"])
            self.last_t = e["t"]
            return None
        if k == "adj":
            try:
                err = self._adjust(e)
            except (KeyError, TypeError, ValueError, IndexError):
                return "bad correction"
            if not err:
                self.last_t = e["t"]
            return err
        return self._other(e)

    def _score(self, e):
        self._rally(e["tm"])
        return None

    def _other(self, e):
        return "not available in this sport"

    def _check(self, t, points, win_by, cap=None):
        s, o = self.scores[t], self.scores[1 - t]
        if (s >= points and s - o >= win_by) or (cap and s >= cap):
            self.winner = t

    def _switch_check(self, at):
        if self.final_game and self.cfg.get("switch_ends") and not self.switched and max(self.scores) >= at:
            self.switched = True
            self.switch_now = True

    def _sim(self, team):
        """Outcome of a point won by `team`, without changing anything: (level, call, winner)."""
        saved = []
        for k in self.STATE:
            v = getattr(self, k)
            saved.append((k, list(v) if isinstance(v, list) else v))
        self.scored = None
        self.level = None
        self._rally(team)
        level = self.level or ("unit" if self.winner is not None else "point")
        out = (level, self.call(), self.winner)
        for k, v in saved:
            setattr(self, k, v)
        return out

    # -- display

    def big(self, t):
        return str(self.scores[t])

    def mid(self, t):
        return None

    def status(self):
        return None

    def side(self):
        return None

    def call(self):
        s = self.serving
        return "%d-%d" % (self.scores[s], self.scores[1 - s])

    def serve_text(self, teams):
        return "%s serving" % teams[self.serving]["players"][self.server_idx]

    def effect(self, t):
        return None if self.over else POINT

    def point_caption(self, e):
        return self.point_word

    def positions(self):
        return None

    def unit_score(self):
        return list(self.scores)

    def next_first(self):
        return 1 - self.first_serving

    def clock(self, now):
        return None

    def adjust_spec(self):
        spec = [{"k": "s", "type": "pair", "label": "Score", "val": list(self.scores)},
                {"k": "sv", "type": "team", "label": "Serving", "val": self.serving}]
        if self.doubles and self.show_server:
            spec.append({"k": "si", "type": "server", "label": "Server", "val": self.server_idx})
        return spec


# ---------------------------------------------------------------- pickleball / badminton / racquetball


class PickleballGame(BaseGame):
    verb = "won rally"
    courts = True
    setup_label = "right court"

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        self.rally = cfg.get("scoring") == "rally"
        BaseGame.__init__(self, cfg, final_game, start_t, serving, right)

    def _place(self, serving, right):
        BaseGame._place(self, serving, right)
        self.server_num = 2 if (self.doubles and not self.rally) else 0

    def _rally(self, team):
        s = self.serving
        if team == s:
            self.scores[s] += 1
            self.scored = s
            if self.doubles:
                self.right[s] = 1 - self.right[s]  # server and partner swap courts
        elif self.rally:
            self.scores[team] += 1
            self.scored = team
            self._side_out(team)
        elif self.server_num == 1:
            self.server_num = 2
            self.server_idx = 1 - self.server_idx
        else:
            self._side_out(team)
        self._switch_check((self.cfg["points"] + 1) // 2)
        self._win()

    def _win(self):
        for t in (0, 1):
            self._check(t, self.cfg["points"], self.cfg["win_by"])

    def _side_out(self, team):
        self.serving = team
        if not self.doubles:
            return
        if self.rally:
            # Rally scoring: the player on the court matching the score's parity serves.
            even = self.scores[team] % 2 == 0
            self.server_idx = self.right[team] if even else 1 - self.right[team]
        else:
            self.server_num = 1
            self.server_idx = self.right[team]  # first server is on the right court

    def side(self):
        if self.doubles:
            return "R" if self.right[self.serving] == self.server_idx else "L"
        return "R" if self.scores[self.serving] % 2 == 0 else "L"

    def call(self):
        text = BaseGame.call(self)
        if self.server_num:
            text += "-%d" % self.server_num
        return text

    def serve_text(self, teams):
        name = teams[self.serving]["players"][self.server_idx]
        text = "%s serving from the %s" % (name, "right" if self.side() == "R" else "left")
        if self.server_num:
            text += " · Server %d" % self.server_num
        return text

    def effect(self, team):
        if self.over:
            return None
        if team == self.serving:
            return POINT
        if self.rally:
            return POINT_SIDEOUT
        if self.server_num == 1:
            return SECOND
        return SIDEOUT

    def positions(self):
        # player index on each court, per team
        out = []
        for t in (0, 1):
            if self.doubles:
                out.append({"R": self.right[t], "L": 1 - self.right[t]})
            else:
                out.append({self.side(): 0})
        return out

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 99), _int(e["s"][1], 0, 99)]
        self.serving = _bit(e["sv"])
        self.right = [_bit(e["rt"][0]), _bit(e["rt"][1])]
        if self.doubles:
            self.server_idx = _bit(e.get("si", 0))
            self.server_num = 0 if self.rally else (2 if int(e.get("sn", 1)) == 2 else 1)
        else:
            self.server_idx = 0
            self.server_num = 0
        if max(self.scores) >= (self.cfg["points"] + 1) // 2:
            self.switched = True
        self.winner = None
        self._win()
        return None

    def adjust_spec(self):
        spec = BaseGame.adjust_spec(self)
        if self.doubles:
            if not self.rally:
                spec.append({"k": "sn", "type": "choice", "label": "Server number", "options": [1, 2], "val": self.server_num})
            if self.courts:
                spec.append({"k": "rt", "type": "players", "label": "Right court", "val": list(self.right)})
        return spec


class BadmintonGame(PickleballGame):
    """Rally scoring to 21 (cap 30); service courts follow the server's score."""

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        cfg = dict(cfg)
        cfg["scoring"] = "rally"
        cfg["win_by"] = 2
        PickleballGame.__init__(self, cfg, final_game, start_t, serving, right)

    def _win(self):
        points = self.cfg["points"]
        cap = {21: 30, 15: 21, 11: 15}.get(points, points + 9)
        for t in (0, 1):
            self._check(t, points, 2, cap)

    def next_first(self):
        return self.winner if self.winner is not None else self.first_serving


class RacquetballGame(PickleballGame):
    """Side-out scoring to 15 (tiebreaker game to 11), win by 1. Doubles: the
    first serving team gets one server at the start of each game."""

    courts = False
    setup_label = "serves first"

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        cfg = dict(cfg)
        cfg["scoring"] = "sideout"
        cfg["win_by"] = 1
        cfg["switch_ends"] = False
        cfg["points"] = 11 if (final_game and cfg["games"] > 1) else 15
        PickleballGame.__init__(self, cfg, final_game, start_t, serving, right)

    def side(self):
        return None

    def positions(self):
        return None

    def serve_text(self, teams):
        text = "%s serving" % teams[self.serving]["players"][self.server_idx]
        if self.server_num:
            text += " · Server %d" % self.server_num
        return text


# ---------------------------------------------------------------- table tennis


class PingPongGame(BaseGame):
    """2 serves each (5 when playing to 21); alternate every point at deuce."""

    def _place(self, serving, right):
        BaseGame._place(self, serving, right)
        self._update_server()

    def _turn(self):
        points = self.cfg["points"]
        per = 2 if points <= 11 else 5
        deuce = points - 1
        total = self.scores[0] + self.scores[1]
        if min(self.scores) >= deuce:
            return (2 * deuce) // per + (total - 2 * deuce), 1
        return total // per, per - total % per

    def _update_server(self):
        turn, _ = self._turn()
        team = self.first_serving if turn % 2 == 0 else 1 - self.first_serving
        self.serving = team
        # Doubles order A1, B1, A2, B2: each team alternates its server every other turn.
        self.server_idx = (self.right[team] + turn // 2) % 2 if self.doubles else 0

    def _rally(self, team):
        self.scores[team] += 1
        self.scored = team
        self._switch_check(self.cfg["points"] // 2)
        self._check(team, self.cfg["points"], 2)
        self._update_server()

    def serve_text(self, teams):
        _, left = self._turn()
        return "%s · %d serve%s left" % (BaseGame.serve_text(self, teams), left, "" if left == 1 else "s")

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 99), _int(e["s"][1], 0, 99)]
        sv = _bit(e["sv"])
        turn, _ = self._turn()
        self.first_serving = sv if turn % 2 == 0 else 1 - sv
        if self.doubles:
            self.right[sv] = (_bit(e.get("si", 0)) - turn // 2) % 2
        if max(self.scores) >= self.cfg["points"] // 2:
            self.switched = True
        self.winner = None
        for t in (0, 1):
            self._check(t, self.cfg["points"], 2)
        self._update_server()
        return None


# ---------------------------------------------------------------- volleyball


class VolleyballGame(BaseGame):
    """Rally scoring. Indoor: sets to 25, deciding set to 15. Beach: sets to 21
    (deciding 15), partners alternate serving each time their team wins the serve,
    and teams switch sides every 7 points (5 in the deciding set)."""

    unit = "SET"
    verb = "won rally"
    STATE = BaseGame.STATE + ("team_idx",)

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        self.beach = cfg.get("variant") == "beach"
        self.show_server = self.beach
        self.setup_label = "serves first" if self.beach else None
        BaseGame.__init__(self, cfg, final_game, start_t, serving, right)

    def _place(self, serving, right):
        BaseGame._place(self, serving, right)
        # team_idx: who served that team's last turn; it flips each time the team regains the serve.
        self.team_idx = [right[0], right[1]]
        self.team_idx[1 - serving] = 1 - right[1 - serving]
        self.server_idx = self.team_idx[serving] if self.beach else 0

    def _deciding(self):
        return self.final_game and self.cfg["games"] > 1

    def _target(self):
        if self._deciding():
            return 15
        return 21 if self.beach else 25

    def _rally(self, team):
        self.scores[team] += 1
        self.scored = team
        if team != self.serving:
            self.serving = team
            if self.beach:
                self.team_idx[team] = 1 - self.team_idx[team]
            self.server_idx = self.team_idx[team] if self.beach else 0
        if self.cfg.get("switch_ends"):
            if self.beach:
                every = 5 if self._deciding() else 7
                if (self.scores[0] + self.scores[1]) % every == 0:
                    self.switch_now = True
            else:
                self._switch_check(8)
        self._check(team, self._target(), 2)

    def serve_text(self, teams):
        if self.beach:
            return BaseGame.serve_text(self, teams)
        return "%s serving" % teams[self.serving]["name"]

    def effect(self, t):
        if self.over:
            return None
        return POINT if t == self.serving else POINT_SIDEOUT

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 99), _int(e["s"][1], 0, 99)]
        self.serving = _bit(e["sv"])
        if self.beach:
            self.server_idx = _bit(e.get("si", 0))
            self.team_idx[self.serving] = self.server_idx
        self.winner = None
        for t in (0, 1):
            self._check(t, self._target(), 2)
        return None


# ---------------------------------------------------------------- foosball / air hockey


class FoosballGame(BaseGame):
    """Goals; the team that was scored on serves."""

    verb = "scored"
    point_word = "GOAL!"
    setup_label = None
    show_server = False

    def _rally(self, team):
        self.scores[team] += 1
        self.scored = team
        self.serving = 1 - team
        self._check(team, self.cfg["points"], self.cfg["win_by"])

    def call(self):
        return "%d-%d" % (self.scores[0], self.scores[1])  # no server-first convention

    def serve_text(self, teams):
        return "%s to serve" % teams[self.serving]["name"]

    def effect(self, t):
        return None if self.over else "+1 goal"

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 99), _int(e["s"][1], 0, 99)]
        self.serving = _bit(e["sv"])
        self.winner = None
        for t in (0, 1):
            self._check(t, self.cfg["points"], self.cfg["win_by"])
        return None

    def next_first(self):
        return 1 - self.winner if self.winner is not None else self.first_serving


class AirHockeyGame(FoosballGame):
    """First to 7 goals; the team scored on gets the puck."""

    def serve_text(self, teams):
        return "%s's puck" % teams[self.serving]["name"]


# ---------------------------------------------------------------- shuffleboard


class ShuffleboardGame(BaseGame):
    """Table shuffleboard: points per round (1-4), first to 15 or 21. The team
    that scores shoots first next round, so the other team has the hammer."""

    verb = "scored"
    setup_label = None
    show_server = False
    buttons = [1, 2, 3, 4]

    def _score(self, e):
        n = int(e.get("n", 1))
        if n not in self.buttons:
            return "points must be 1-4"
        t = e["tm"]
        self.scores[t] += n
        self.scored = t
        self.points_scored = n
        self.serving = 1 - t
        if self.scores[t] >= self.cfg["points"]:
            self.winner = t
        return None

    def call(self):
        return "%d-%d" % (self.scores[0], self.scores[1])

    def serve_text(self, teams):
        return "Hammer: %s" % teams[self.serving]["name"]

    def effect(self, t):
        return None

    def point_caption(self, e):
        return "+%d" % self.points_scored

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 99), _int(e["s"][1], 0, 99)]
        self.serving = _bit(e["sv"])
        self.winner = None
        for t in (0, 1):
            if self.scores[t] >= self.cfg["points"]:
                self.winner = t
        return None

    def next_first(self):
        return 1 - self.winner if self.winner is not None else self.first_serving

    def adjust_spec(self):
        return [{"k": "s", "type": "pair", "label": "Score", "val": list(self.scores)},
                {"k": "sv", "type": "team", "label": "Hammer (shoots last)", "val": self.serving}]


# ---------------------------------------------------------------- tennis

TENNIS_POINTS = ("0", "15", "30", "40")
TENNIS_CALL = ("Love", "15", "30", "40")


class TennisSet(BaseGame):
    """One set: points, games, tiebreak at 6-6 (or a 10-point match tiebreak)."""

    unit = "SET"
    STATE = BaseGame.STATE + ("games", "points", "tb", "tb_first", "tb_idx0", "team_idx")

    def _place(self, serving, right):
        self.games = [0, 0]
        self.points = [0, 0]
        self.match_tb = bool(self.cfg.get("final_tb")) and self.final_game and self.cfg["games"] > 1
        self.tb = self.match_tb
        self.tb_first = serving
        self.tb_idx0 = [right[0], right[1]]
        BaseGame._place(self, serving, right)
        self.team_idx = [right[0], right[1]]  # who serves that team's next service game

    @property
    def scores(self):
        return self.points if self.match_tb else self.games

    @scores.setter
    def scores(self, value):
        pass  # BaseGame.__init__ assigns [0, 0]; games/points are set in _place

    def _rally(self, team):
        other = 1 - team
        self.points[team] += 1
        self.scored = team
        p = self.points
        if self.tb:
            target = 10 if self.match_tb else 7
            if p[team] >= target and p[team] - p[other] >= 2:
                self._win_set(team)
                return
            if self.cfg.get("switch_ends") and (p[0] + p[1]) % 6 == 0:
                self.switch_now = True
            self._tb_server()
            return
        if self.cfg.get("no_ad"):
            won = p[team] >= 4
        else:
            won = p[team] >= 4 and p[team] - p[other] >= 2
            if not won and p[0] >= 4 and p[1] >= 4:
                self.points = [3, 3]  # back to deuce
        if won:
            self._win_game(team)

    def _win_game(self, team):
        other = 1 - team
        self.games[team] += 1
        self.points = [0, 0]
        self.level = "game"
        # Rotate the serve: the team that just served switches server for next time.
        self.team_idx[self.serving] = 1 - self.team_idx[self.serving]
        self.serving = 1 - self.serving
        self.server_idx = self.team_idx[self.serving] if self.doubles else 0
        sg = self.cfg["set_games"]
        if self.games[team] >= sg and self.games[team] - self.games[other] >= 2:
            self.winner = team
            self.level = "unit"
            return
        if self.cfg.get("switch_ends") and (self.games[0] + self.games[1]) % 2 == 1:
            self.switch_now = True
        if self.games[0] == sg and self.games[1] == sg:
            self.tb = True
            self.tb_first = self.serving
            self.tb_idx0 = [self.team_idx[0], self.team_idx[1]]

    def _win_set(self, team):
        if not self.match_tb:
            self.games[team] += 1
        self.winner = team
        self.level = "unit"

    def _tb_server(self):
        turn = (self.points[0] + self.points[1] + 1) // 2
        team = self.tb_first if turn % 2 == 0 else 1 - self.tb_first
        self.serving = team
        self.server_idx = (self.tb_idx0[team] + turn // 2) % 2 if self.doubles else 0

    def big(self, t):
        if self.tb:
            return str(self.points[t])
        p, q = self.points[t], self.points[1 - t]
        if p >= 3 and q >= 3:
            return "AD" if p > q else "40"
        return TENNIS_POINTS[min(p, 3)]

    def mid(self, t):
        return None if self.match_tb else str(self.games[t])

    def status(self):
        return "TIEBREAK" if self.tb else None

    def side(self):
        return "R" if (self.points[0] + self.points[1]) % 2 == 0 else "L"

    def call(self):
        s, o = self.serving, 1 - self.serving
        p, q = self.points[s], self.points[o]
        if self.tb:
            return "%d-%d" % (p, q)
        if p >= 3 and q >= 3:
            return "Deuce" if p == q else ("Ad in" if p > q else "Ad out")
        if p == q:
            return "%s-All" % TENNIS_CALL[p]
        return "%s-%s" % (TENNIS_CALL[p], TENNIS_CALL[q])

    def serve_text(self, teams):
        court = "deuce" if self.side() == "R" else "ad"
        extra = " · tiebreak" if self.tb else ""
        return "%s · %s court%s" % (BaseGame.serve_text(self, teams), court, extra)

    def effect(self, t):
        if self.over:
            return None
        level, call, _ = self._sim(t)
        if level == "unit":
            return "Set"
        if level == "game":
            return "Game"
        return "→ " + call

    def point_caption(self, e):
        return self.call()

    def unit_score(self):
        return list(self.points) if self.match_tb else list(self.games)

    def next_first(self):
        if self.tb and not self.match_tb:
            return 1 - self.tb_first  # who received first in the tiebreak serves first
        return self.serving

    def _adjust(self, e):
        self.games = [_int(e["g"][0], 0, 13), _int(e["g"][1], 0, 13)]
        sg = self.cfg["set_games"]
        self.tb = self.match_tb or self.games == [sg, sg]
        cap = 99 if self.tb else 4
        self.points = [_int(e["p"][0], 0, cap), _int(e["p"][1], 0, cap)]
        self.serving = _bit(e["sv"])
        if self.doubles:
            self.server_idx = _bit(e.get("si", 0))
            self.team_idx[self.serving] = self.server_idx
        if self.tb:
            # Treat the correction as the tiebreak's current state.
            turn = (self.points[0] + self.points[1] + 1) // 2
            self.tb_first = self.serving if turn % 2 == 0 else 1 - self.serving
            self.tb_idx0[self.serving] = (self.server_idx - turn // 2) % 2
        self.winner = None
        return None

    def adjust_spec(self):
        spec = [
            {"k": "g", "type": "pair", "label": "Games", "val": list(self.games)},
            {"k": "p", "type": "pair", "label": "Points (0, 15, 30, 40, Ad = 0-4; tiebreak: actual points)",
             "val": list(self.points)},
            {"k": "sv", "type": "team", "label": "Serving", "val": self.serving},
        ]
        if self.doubles:
            spec.append({"k": "si", "type": "server", "label": "Server", "val": self.server_idx})
        return spec


# ---------------------------------------------------------------- timed games


class TimedGame(BaseGame):
    """Basketball, soccer, hockey, football or custom: +N scoring and a game clock
    with periods. The game ends when the last period runs out (unless tied: then
    add overtime or end it as a tie) or when ended by hand."""

    verb = "scored"
    layout = "timed"
    setup_label = None
    show_server = False
    STATE = BaseGame.STATE

    def __init__(self, cfg, final_game, start_t, serving=0, right=(0, 0)):
        self.preset = TIMED_PRESETS.get(cfg.get("preset"), TIMED_PRESETS["custom"])
        self.buttons = list(cfg.get("buttons") or self.preset["buttons"])
        self.periods = cfg.get("periods", self.preset["periods"])
        self.period_secs = cfg.get("minutes", self.preset["minutes"]) * 60
        self.count = cfg.get("count", self.preset["count"])
        self.period = 1
        self.used = 0.0  # seconds played in this period (as of ref_t when running)
        self.running = False
        self.ref_t = start_t
        self.ended = False
        self.tied = False
        BaseGame.__init__(self, cfg, final_game, start_t, serving, right)

    @property
    def over(self):
        return self.ended

    def length(self):
        if self.period > self.cfg.get("periods", self.preset["periods"]):
            return self.preset["ot"] * 60
        return self.period_secs

    def played(self, now):
        used = self.used
        if self.running and now > self.ref_t:
            used += now - self.ref_t
        return min(used, self.length())

    def remaining(self, now):
        return self.length() - self.played(now)

    def expired(self, now):
        return self.running and self.remaining(now) <= 0

    def _score(self, e):
        n = int(e.get("n", 1))
        if n not in self.buttons:
            return "points must be one of %s" % ", ".join(str(b) for b in self.buttons)
        t = e["tm"]
        self.scores[t] += n
        self.scored = t
        self.points_scored = n
        self.tied = False
        return None

    def _end(self):
        self.ended = True
        self.running = False
        self.winner = _leader(self.scores)

    def _other(self, e):
        k, t = e.get("k"), e["t"]
        if k == "cend":
            if self.ended:
                return "game is over"
            if self.running:
                self.used = self.played(t)
            self._end()
            return None
        if self.ended:
            return "game is over"
        if k == "cs":
            if self.running:
                return None
            if self.played(t) >= self.length():
                return "this period is over; start the next one"
            self.running = True
            self.ref_t = t
        elif k == "cp":
            if self.running:
                self.used = self.played(t)
                self.running = False
        elif k == "cset":
            secs = max(0, min(self.length(), float(e["secs"])))
            self.used = self.length() - secs if self.count == "down" else secs
            self.running = False
        elif k == "cexp":
            self.used = self.length()
            self.running = False
            if self.period >= self.periods:
                if _leader(self.scores) is None:
                    self.tied = True
                else:
                    self._end()
        elif k == "cper":
            if self.period >= self.periods:
                return "that was the last period"
            self.period += 1
            self.used = 0.0
            self.running = False
        elif k == "cot":
            self.periods += 1
            self.period = self.periods
            self.used = 0.0
            self.running = False
            self.tied = False
        else:
            return "unknown event"
        return None

    def period_text(self):
        regular = self.cfg.get("periods", self.preset["periods"])
        if self.period > regular:
            extra = self.period - regular
            return "OT" if extra == 1 else "OT%d" % extra
        return "%s%d" % (self.preset["label"], self.period)

    def clock_text(self, now):
        secs = self.remaining(now) if self.count == "down" else self.played(now)
        if self.count == "down" and secs < 60:
            tenths = int(secs * 10)
            return "%d.%d" % (tenths // 10, tenths % 10)
        secs = int(secs + 0.999) if self.count == "down" else int(secs)
        return "%d:%02d" % (secs // 60, secs % 60)

    def clock(self, now):
        return {
            "text": self.clock_text(now),
            "running": self.running,
            "used": self.used,
            "ref_t": self.ref_t,
            "length": self.length(),
            "count": self.count,
            "period": self.period,
            "periods": self.periods,
            "period_text": self.period_text(),
            "last_period": self.period >= self.periods,
            "tied": self.tied,
            "low": self.count == "down" and self.remaining(now) < 60,
        }

    def status(self):
        if self.ended:
            return "FINAL"
        if self.tied:
            return "TIED"
        return None

    def call(self):
        return "%d-%d" % (self.scores[0], self.scores[1])

    def serve_text(self, teams):
        return "%s · clock %s" % (self.period_text(), "running" if self.running else "stopped")

    def effect(self, t):
        return None

    def point_caption(self, e):
        name = self.cfg.get("preset")
        n = self.points_scored
        if name in ("soccer", "hockey"):
            return "GOAL!"
        if name == "football":
            return {6: "TD!", 3: "FG!"}.get(n, "+%d" % n)
        return "+%d" % n

    def _adjust(self, e):
        self.scores = [_int(e["s"][0], 0, 999), _int(e["s"][1], 0, 999)]
        self.tied = False
        return None

    def adjust_spec(self):
        return [{"k": "s", "type": "pair", "label": "Score", "val": list(self.scores)}]


GAMES = {
    "pickleball": PickleballGame,
    "tennis": TennisSet,
    "badminton": BadmintonGame,
    "pingpong": PingPongGame,
    "volleyball": VolleyballGame,
    "racquetball": RacquetballGame,
    "foosball": FoosballGame,
    "airhockey": AirHockeyGame,
    "shuffleboard": ShuffleboardGame,
    "timed": TimedGame,
}


def game_class(cfg):
    return GAMES.get(cfg.get("sport", "pickleball"), PickleballGame)


def describe(e, teams, cfg=None):
    k = e.get("k")
    if k == "r":
        name = teams[e["tm"]]["name"]
        cls = game_class(cfg or {})
        if cls.buttons is not None or cls is TimedGame:  # points games: "+3"
            n = int(e.get("n", 1))
            return "%s %s%d" % (name, "+" if n > 0 else "", n)
        return "%s %s: %s" % (name, cls.verb, e.get("x", ""))
    if k == "pos":
        return "Starting server/positions set"
    if k == "adj":
        return "Score corrected"
    if k == "u":
        return "Undo: %s" % e.get("ud", "")
    if k == "fin" or k == "cend":
        return "Game ended"
    if k == "cs":
        return "Clock started"
    if k == "cp":
        return "Clock stopped"
    if k == "cset":
        secs = int(float(e.get("secs", 0)))
        return "Clock set to %d:%02d" % (secs // 60, secs % 60)
    if k == "cexp":
        return "Period ended"
    if k == "cper":
        return "Next period"
    if k == "cot":
        return "Overtime"
    return k


# ---------------------------------------------------------------- match


class Match:
    """A match in progress. `d` is the persisted dict; `game` is derived state."""

    def __init__(self, d):
        self.d = d
        self.d["cfg"].setdefault("sport", "pickleball")
        self.rebuild()

    @staticmethod
    def new(match_id, game_id, cfg, teams, first_serving, now):
        cfg = clean_cfg(cfg)
        d = {
            "id": match_id,
            "cfg": cfg,
            "teams": clean_teams(teams, cfg),
            "start": now,
            "games": [],
            "cur": {"id": game_id, "n": 1, "start": now, "sv": 1 if first_serving else 0, "events": []},
        }
        return Match(d)

    @property
    def sport(self):
        return self.d["cfg"]["sport"]

    @property
    def n_teams(self):
        return len(self.d["teams"])

    def effective(self):
        """Events still in effect. Undo removes the latest undoable one; clock
        events (time passing) are never undone."""
        eff = []
        for e in self.d["cur"]["events"]:
            if e.get("k") == "u":
                for i in range(len(eff) - 1, -1, -1):
                    if eff[i].get("k") not in CLOCK_KINDS:
                        eff.pop(i)
                        break
            else:
                eff.append(e)
        return eff

    def _last_undoable(self):
        for e in reversed(self.effective()):
            if e.get("k") not in CLOCK_KINDS:
                return e
        return None

    def rebuild(self):
        cur = self.d["cur"]
        final_game = cur["n"] == self.d["cfg"]["games"]
        self.game = game_class(self.d["cfg"])(self.d["cfg"], final_game, cur["start"], cur["sv"], cur.get("rt", (0, 0)))
        for e in self.effective():
            self.game.apply(e)
        self.game.switch_now = False
        self.game.scored = None
        self.game.level = None

    def games_won(self):
        won = [0] * self.n_teams
        for g in self.d["games"]:
            if g.get("winner") is not None:
                won[g["winner"]] += 1
        if self.game.winner is not None:
            won[self.game.winner] += 1
        return won

    def need(self):
        return self.d["cfg"]["games"] // 2 + 1

    def over(self):
        """The whole match is decided."""
        if self.d["cfg"]["games"] == 1:
            return self.game.over
        return self.winner() is not None

    def winner(self):
        if self.d["cfg"]["games"] == 1:
            return self.game.winner
        won = self.games_won()
        for t in range(self.n_teams):
            if won[t] >= self.need():
                return t
        return None

    def undo_label(self):
        e = self._last_undoable()
        return describe(e, self.d["teams"], self.d["cfg"]) if e else None

    def badge(self, t):
        """"MATCH POINT", "SET POINT", "GAME POINT", "BREAK POINT" or None."""
        g = self.game
        if g.over or g.layout != "standard" or g.buttons is not None:
            return None
        level, _, winner = g._sim(t)
        if winner == t:
            return "MATCH POINT" if self.games_won()[t] + 1 >= self.need() else "%s POINT" % g.unit
        if level == "game" and t != g.serving:
            return "BREAK POINT"
        return None

    def callsub(self):
        g = self.game
        if g.over:
            if g.layout != "standard":
                return "Final"
            return "Final · %s %d" % (g.unit.capitalize(), self.d["cur"]["n"])
        return g.serve_text(self.d["teams"])

    def act(self, e):
        """Validate and record an event. Returns an error string or None."""
        g = self.game
        k = e["k"]
        if k == "u":
            last = self._last_undoable()
            if last is None:
                return "nothing to undo"
            e["ud"] = describe(last, self.d["teams"], self.d["cfg"])
            self.d["cur"]["events"].append(e)
            self.rebuild()
        else:
            if k == "r" and e.get("tm") not in range(self.n_teams):
                return "bad team"
            if k == "r" and g.buttons is None:
                e["x"] = g.effect(e["tm"])
            err = g.apply(e)
            if err:
                self.rebuild()  # restore state after a partial change
                return err
            self.d["cur"]["events"].append(e)
        g = self.game  # rebuild() replaces it on undo
        e["s"] = g.unit_score()
        e["c"] = g.call()
        return None

    def next_game(self, game_id, now):
        g = self.game
        if not g.over:
            return "%s is not over" % g.unit.lower()
        if self.over():
            return "match is over"
        cur = self.d["cur"]
        self.d["games"].append(
            {"id": cur["id"], "n": cur["n"], "score": g.unit_score(), "winner": g.winner, "start": cur["start"]}
        )
        self.d["cur"] = {"id": game_id, "n": cur["n"] + 1, "start": now, "sv": g.next_first(), "events": []}
        self.rebuild()
        return None

    def view(self, now=0):
        """What the LED panels need, independent of sport."""
        g = self.game
        teams = self.d["teams"]
        n = self.n_teams
        return {
            "layout": g.layout,
            "big": [g.big(t) for t in range(n)],
            "mid": [g.mid(t) for t in range(n)],
            "need": self.need() if self.d["cfg"]["games"] > 1 else 0,
            "won": self.games_won(),
            "server": None if (g.over or not g.show_server) else (g.serving, g.server_idx, g.server_num),
            "serving": None if (g.over or g.layout != "standard") else g.serving,
            "names": [t["name"] for t in teams],
            "players": [t["players"] for t in teams],
            "courts": g.positions() if g.courts else None,  # who stands on each team's R / L court
            "doubles": g.doubles,
            "status": g.status(),
            "over": g.over,
            "winner": g.winner,
            "unit": g.unit,
            "match_over": self.over(),
            "last_t": g.last_t,
            "clock": g.clock(now),
        }
