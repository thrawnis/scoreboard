# Scoreboard application: match state, game history storage and the HTTP API.
# Platform independent: code.py (CircuitPython) and devserver.py (CPython) wrap it.
#
# Files under data_dir:
#   current.json      match in progress (Match.d)
#   index.json        summaries of saved games, newest first
#   games/g<id>.json  full game record with its change history
#   meta.json         id counter and board settings
#   wifi.json         Wi-Fi network to join

import json
import os
import time

import colors
from sports import Match, clean_cfg, teams_error

HISTORY_MAX = 100
SAVE_DELAY = 2  # seconds; coalesces bursts of taps into one flash write


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(obj))
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(tmp, path)


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class App:
    def __init__(self, data_dir, clock=time.time, set_clock=None, net_info=None, reboot=None):
        self.dir = data_dir.rstrip("/")
        self.clock = clock
        self.set_clock = set_clock
        self.net_info = net_info or (lambda: {})
        self.reboot = reboot
        self.version = 1
        self.alert = None
        self.save_due = None
        self.reboot_due = None
        self.celebrate = None  # last scoring event, for the board's animation
        self._cache = None  # (version, match state) - replaying for previews isn't free
        self.synced = set_clock is None  # host clocks are already right
        self.unsynced_ids = []  # games saved this boot before the clock was set
        self.writable = True
        try:
            for d in (self.dir, self.dir + "/games"):
                if not _exists(d):
                    os.mkdir(d)
        except OSError:
            self.writable = False

        self.meta = _read_json(self._p("meta.json"), None) or {"next_id": 1, "brightness": 100}
        self.index = _read_json(self._p("index.json"), None)
        if self.index is None:
            self.index = self._rebuild_index()
        self.match = None
        cur = _read_json(self._p("current.json"), None)
        if cur:
            try:
                self.match = Match(cur)
            except (KeyError, TypeError, ValueError, IndexError):
                self.match = None
        self._save_meta()

    # ---------------------------------------------------------------- storage

    def _p(self, name):
        return self.dir + "/" + name

    def _game_path(self, gid):
        return self._p("games/g%05d.json" % gid)

    def _write(self, path, obj):
        try:
            _write_json(path, obj)
            self.writable = True
        except OSError:
            self.writable = False

    def _save_meta(self):
        self._write(self._p("meta.json"), self.meta)

    def _new_id(self):
        gid = self.meta["next_id"]
        self.meta["next_id"] = gid + 1
        self._save_meta()
        return gid

    def _rebuild_index(self):
        index = []
        try:
            names = os.listdir(self._p("games"))
        except OSError:
            return index
        for name in names:
            if name.startswith("g") and name.endswith(".json"):
                rec = _read_json(self._p("games/" + name))
                if rec:
                    index.append(self._summary(rec))
        index.sort(key=lambda s: s["id"], reverse=True)
        return index[:HISTORY_MAX]

    @staticmethod
    def _summary(rec):
        cfg = rec["cfg"]
        return {
            "id": rec["id"],
            "sport": cfg.get("sport", "pickleball"),
            "preset": cfg.get("preset"),
            "variant": cfg.get("variant"),
            "match": rec["match"],
            "n": rec["n"],
            "games": cfg["games"],
            "mode": cfg.get("mode"),
            "scoring": cfg.get("scoring"),
            "teams": rec["teams"],
            "start": rec["start"],
            "end": rec["end"],
            "score": rec["score"],
            "winner": rec["winner"],
            "status": rec["status"],
            "changes": len(rec["events"]),
        }

    def _save_record(self, status):
        m = self.match
        cur = m.d["cur"]
        g = m.game
        rec = {
            "id": cur["id"],
            "match": m.d["id"],
            "n": cur["n"],
            "cfg": m.d["cfg"],
            "teams": m.d["teams"],
            "start": cur["start"],
            "end": self.clock(),
            "score": g.unit_score(),
            "winner": g.winner,
            "first_serving": g.first_serving,
            "status": status,
            "events": cur["events"],
        }
        self._write(self._game_path(rec["id"]), rec)
        if not self.synced:
            self.unsynced_ids.append(rec["id"])
        self.index = [s for s in self.index if s["id"] != rec["id"]]
        self.index.insert(0, self._summary(rec))
        self.index.sort(key=lambda s: s["id"], reverse=True)
        for old in self.index[HISTORY_MAX:]:
            self._remove(self._game_path(old["id"]))
        self.index = self.index[:HISTORY_MAX]
        self._write(self._p("index.json"), self.index)

    def _delete_record(self, gid):
        self._remove(self._game_path(gid))
        self.index = [s for s in self.index if s["id"] != gid]
        self._write(self._p("index.json"), self.index)

    def _remove(self, path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _changed(self, save_now=False):
        self.version += 1
        if save_now:
            self.flush()
        else:
            self.save_due = time.monotonic() + SAVE_DELAY

    def flush(self):
        self.save_due = None
        if self.match:
            self._write(self._p("current.json"), self.match.d)
        else:
            self._remove(self._p("current.json"))

    def tick(self):
        """Call regularly from the main loop."""
        if self.save_due is not None and time.monotonic() >= self.save_due:
            self.flush()
        g = self.match.game if self.match else None
        if g is not None and g.layout == "timed" and g.expired(self.clock()):
            try:
                self._event({"k": "cexp"})  # the period's clock ran out
            except ApiError:
                pass
        if self.alert and self.clock() >= self.alert["until"]:
            self.alert = None
            self.version += 1
        if self.reboot_due is not None and time.monotonic() >= self.reboot_due:
            self.reboot_due = None
            self.flush()
            if self.reboot:
                self.reboot()

    # ---------------------------------------------------------------- time

    def sync_time(self, now):
        """Adopt a client's clock when ours is unset (the Pico has no RTC battery)."""
        try:
            now = float(now)
        except (TypeError, ValueError):
            return
        if now < 1.6e9 or self.set_clock is None:
            return
        delta = now - self.clock()
        if abs(delta) < 3:
            self.synced = True
            return
        was_unset = self.clock() < 1.6e9
        self.set_clock(now)
        if was_unset and not self.synced and self.match:
            # Times logged before the first sync used the unset clock; shift them.
            # Times restored from an earlier, synced boot are already right.
            cur = self.match.d["cur"]
            if cur["start"] < 1.6e9:
                cur["start"] += delta
            for e in cur["events"]:
                if e["t"] < 1.6e9:
                    e["t"] += delta
            self.match.rebuild()
        if was_unset and not self.synced:
            self._shift_records(delta)
            self._cache = None
            self.version += 1
        if self.alert:
            self.alert["until"] += delta
        self.synced = True

    def _shift_records(self, delta):
        for gid in self.unsynced_ids:
            rec = _read_json(self._game_path(gid))
            if not rec:
                continue
            rec["start"] += delta
            rec["end"] += delta
            for e in rec["events"]:
                e["t"] += delta
            self._write(self._game_path(gid), rec)
            self.index = [self._summary(rec) if s["id"] == gid else s for s in self.index]
        if self.unsynced_ids:
            self._write(self._p("index.json"), self.index)
        self.unsynced_ids = []

    # ---------------------------------------------------------------- actions

    def _need_match(self):
        if not self.match:
            raise ApiError(400, "no match in progress")
        return self.match

    def _team(self, body):
        team = body.get("team")
        n = self.match.n_teams if self.match else 2
        if isinstance(team, str):
            low = team.lower()
            team = "abcdef".index(low) if low in "abcdef" and len(low) == 1 else (int(low) if low.isdigit() else None)
        if team in range(n):
            return team
        raise ApiError(400, "team must be 0-%d" % (n - 1))

    def _event(self, e):
        m = self._need_match()
        was_over = m.game.over
        e["t"] = self.clock()
        err = m.act(e)
        if err:
            raise ApiError(400, err)
        now_over = m.game.over
        g = m.game
        if e["k"] == "r" and g.scored is not None:
            level = g.level
            if level == "unit" and m.over():
                level = "match"
            if level == "point":
                text = g.point_caption(e)
            else:
                text = {"game": "GAME", "unit": g.unit, "match": "MATCH"}[level]
            n = self.celebrate["n"] + 1 if self.celebrate else 1
            sport = m.sport
            if sport == "timed":
                sport = "timed_" + m.d["cfg"].get("preset", "custom")
            self.celebrate = {"n": n, "tm": g.scored, "level": level, "text": text, "sport": sport,
                              "teams": m.n_teams}
        if m.game.switch_now:
            self.alert = {"k": "switch", "until": e["t"] + 8}
        if now_over and not was_over:
            self._save_record("final")
        elif was_over and not now_over:
            self._delete_record(m.d["cur"]["id"])
        self._changed(save_now=now_over != was_over)

    def _abandon(self):
        m = self.match
        if m and not m.game.over and m.effective():
            self._save_record("abandoned")

    def new_match(self, body):
        cfg = clean_cfg(body.get("cfg"))
        err = teams_error(body.get("teams"), cfg)
        if err:
            raise ApiError(400, err)  # before abandoning the current game
        self._abandon()
        match_id = self._new_id()
        game_id = self._new_id()
        first = 1 if body.get("first_serving") in (1, "1", "b", "B") else 0
        self.match = Match.new(match_id, game_id, body.get("cfg"), body.get("teams"), first, self.clock())
        self.alert = None
        self._changed(save_now=True)

    def end_match(self):
        self._abandon()
        self.match = None
        self.alert = None
        self._changed(save_now=True)

    def next_game(self):
        m = self._need_match()
        # Check before allocating an id so a refused request doesn't burn one.
        if not m.game.over:
            raise ApiError(400, "%s is not over" % m.game.unit.lower())
        if m.over():
            raise ApiError(400, "match is over")
        err = m.next_game(self._new_id(), self.clock())
        if err:
            raise ApiError(400, err)
        self.alert = None
        self._changed(save_now=True)

    # ---------------------------------------------------------------- views

    def state(self):
        now = self.clock()
        out = {
            "v": self.version,
            "now": now,
            "synced": self.synced,
            "storage": self.writable,
            "brightness": self.meta.get("brightness", 100),
            "panels": 2,  # single-screen mode is disabled for now (code.py always runs the stacked board)
            "serpentine": self.meta.get("serpentine", True),
            "court": self.meta.get("court", "over"),
            "remote_team": 1 if self.meta.get("remote_team", 1) else 0,
            "animations": self.meta.get("animations", True),
            "alert": self.alert,
            "match": None,
        }
        m = self.match
        if m and self._cache and self._cache[0] == self.version:
            out["match"] = self._cache[1]
        elif m:
            g = m.game
            cur = m.d["cur"]
            out["match"] = {
                "id": m.d["id"],
                "cfg": m.d["cfg"],
                "teams": m.d["teams"],
                "games": m.d["games"],
                "n": cur["n"],
                "won": m.games_won(),
                "need": m.need(),
                "winner": m.winner(),
                "over": m.over(),
                "sport": m.sport,
                "game": {
                    "id": cur["id"],
                    "start": cur["start"],
                    "unit": g.unit,
                    "verb": g.verb,
                    "scores": g.unit_score(),
                    "layout": g.layout,
                    "buttons": g.buttons,
                    "allow_minus": g.allow_minus,
                    "big": [g.big(t) for t in range(m.n_teams)],
                    "mid": [g.mid(t) for t in range(m.n_teams)],
                    "status": g.status(),
                    "serving": g.serving,
                    "first_serving": g.first_serving,
                    "server_idx": g.server_idx,
                    "server_num": g.server_num,
                    "show_server": g.show_server,
                    "side": g.side(),
                    "right": g.right,
                    "positions": g.positions(),
                    "call": g.call(),
                    "callsub": m.callsub(),
                    "winner": g.winner,
                    "over": g.over,
                    "clock": g.clock(now),
                    "last_t": g.last_t,
                    "rallies": g.rallies,
                    "can_setup": g.rallies == 0 and g.unit_score() == [0] * m.n_teams,
                    "setup_label": g.setup_label,
                    "effect": [g.effect(t) for t in range(m.n_teams)],
                    "badge": [m.badge(t) for t in range(m.n_teams)],
                    "adjust": g.adjust_spec(),
                    "undo": m.undo_label(),
                    "changes": len(cur["events"]),
                },
            }
            self._cache = (self.version, out["match"])
        return out

    def watch_text(self):
        m = self.match
        if not m:
            return "No match in progress"
        g = m.game
        t = m.d["teams"]
        if m.n_teams == 2:
            lines = ["%s %s - %s %s" % (t[0]["name"], g.big(0), g.big(1), t[1]["name"])]
        else:
            lines = [", ".join("%s %s" % (t[i]["name"], g.big(i)) for i in range(m.n_teams))]
        if g.mid(0) is not None:
            lines.append("Games %s-%s" % (g.mid(0), g.mid(1)))
        clock = g.clock(self.clock())
        if clock and not g.over:
            lines.append("%s %s%s" % (clock["period_text"], clock["text"], "" if clock["running"] else " (stopped)"))
        if g.over:
            if g.winner is None:
                lines.append("Final: tie")
            else:
                lines.append("%s wins the %s" % (t[g.winner]["name"], "match" if m.over() else g.unit.lower()))
        elif not clock:
            lines.append("%s  %s" % (g.call(), m.callsub()))
        return "\n".join(lines)

    # ---------------------------------------------------------------- HTTP

    def handle(self, method, path, query, raw_body):
        """Returns (status_code, content_type, body_text)."""
        try:
            body = json.loads(raw_body) if raw_body else {}
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            return 400, "application/json", json.dumps({"error": "invalid JSON"})
        now = body.get("now") or (query or {}).get("now")
        if now:
            self.sync_time(now)
        try:
            ctype, payload = self._route(method, path.rstrip("/"), body)
            return 200, ctype, payload
        except ApiError as err:
            return err.code, "application/json", json.dumps({"error": err.message, "state": self.state()})

    def _route(self, method, path, body):
        js = "application/json"
        if path.startswith("/api/w/") and (method == "POST" or path in ("/api/w/point", "/api/w/loss")):
            # Minimal endpoints for Apple Watch / Siri Shortcuts: plain-text reply.
            what = path[7:]
            try:
                bits = what.split("/")
                if what in ("point", "loss"):
                    # For a one- or two-button remote: "point" is a rally won by the remote's team (the
                    # Settings choice), "loss" a rally won by the other team. (GET works too, for simple
                    # button apps.)
                    g = self._need_match().game
                    if self.match.n_teams != 2 or g.layout != "standard":
                        raise ApiError(400, "the remote needs a two-team rally game")
                    mine = 1 if self.meta.get("remote_team", 1) else 0
                    self._event({"k": "r", "tm": mine if what == "point" else 1 - mine})
                elif what == "undo":
                    self._event({"k": "u"})
                elif what == "clock":
                    g = self._need_match().game
                    if g.layout != "timed":
                        raise ApiError(400, "no game clock in this sport")
                    self._event({"k": "cp" if g.running else "cs"})
                elif bits[0] in "abcdef" and len(bits[0]) == 1:
                    # /api/w/a scores the first button's value; /api/w/a/3 scores 3
                    e = {"k": "r", "tm": self._team({"team": bits[0]})}
                    g = self._need_match().game
                    if g.buttons is not None:
                        e["n"] = int(bits[1]) if len(bits) > 1 else g.buttons[0]
                    self._event(e)
                elif what != "score":
                    raise ApiError(404, "unknown action")
            except ApiError as err:
                return "text/plain", "%s\n%s" % (err.message, self.watch_text())
            return "text/plain", self.watch_text()

        if method == "GET" and path == "/api/state":
            return js, json.dumps(self.state())

        if method == "POST":
            if "v" in body and body["v"] != self.version:
                raise ApiError(409, "the score changed on another device; check it and try again")
            if path == "/api/rally":
                e = {"k": "r", "tm": self._team(body)}
                if "n" in body:
                    e["n"] = int(body["n"])
                self._event(e)
            elif path == "/api/clock":
                g = self._need_match().game
                kinds = {"start": "cs", "stop": "cp", "set": "cset", "next": "cper", "ot": "cot", "end": "cend"}
                action = body.get("action")
                if action == "toggle":
                    action = "stop" if getattr(g, "running", False) else "start"
                if g.layout != "timed" or action not in kinds:
                    raise ApiError(400, "unknown clock action")
                e = {"k": kinds[action]}
                if action == "set":
                    e["secs"] = float(body.get("secs", 0))
                self._event(e)
            elif path == "/api/finish":
                g = self._need_match().game
                self._event({"k": "cend" if g.layout == "timed" else "fin"})
            elif path == "/api/undo":
                self._event({"k": "u"})
            elif path == "/api/positions":
                right = body.get("right") or [0, 0]
                self._event({"k": "pos", "sv": 1 if body.get("serving") else 0,
                             "rt": [1 if right[0] else 0, 1 if right[1] else 0]})
            elif path == "/api/adjust":
                e = {"k": "adj", "sv": 0, "si": 0, "sn": 1, "rt": [0, 0]}
                for key in ("s", "g", "p", "sv", "si", "sn", "rt"):
                    if key in body:
                        e[key] = body[key]
                self._event(e)
            elif path == "/api/next_game":
                self.next_game()
            elif path == "/api/match":
                self.new_match(body)
            elif path == "/api/end_match":
                self.end_match()
            elif path == "/api/settings":
                if "brightness" in body:
                    self.meta["brightness"] = max(5, min(100, int(body["brightness"])))
                    self._save_meta()
                    self._changed()
                if "animations" in body:
                    self.meta["animations"] = bool(body["animations"])
                    self._save_meta()
                    self._changed()
                if body.get("panels") == 1:
                    raise ApiError(400, "single-screen mode is disabled for now; the board runs as two stacked screens")
                if body.get("panels") == 2 and body["panels"] != self.meta.get("panels", 2):
                    # The matrix size is fixed at start-up, so restart once the reply is sent.
                    self.meta["panels"] = body["panels"]
                    self._save_meta()
                    self._changed()
                    self.reboot_due = time.monotonic() + 1.5
                if body.get("remote_team") in (0, 1, "a", "b", "A", "B"):
                    self.meta["remote_team"] = 1 if body["remote_team"] in (1, "b", "B") else 0
                    self._save_meta()
                    self._changed()
                if body.get("court") in ("over", "mirror", "same"):
                    self.meta["court"] = body["court"]  # court diagram orientation; applies at once
                    self._save_meta()
                    self._changed()
                if "serpentine" in body and bool(body["serpentine"]) != self.meta.get("serpentine", True):
                    # Is the bottom panel of the stacked board mounted upside-down? Also applied at start-up.
                    self.meta["serpentine"] = bool(body["serpentine"])
                    self._save_meta()
                    self._changed()
                    self.reboot_due = time.monotonic() + 1.5
            elif path == "/api/wifi":
                ssid = str(body.get("ssid", "")).strip()
                if ssid:
                    self._write(self._p("wifi.json"), {"ssid": ssid, "password": str(body.get("password", ""))})
                else:
                    self._remove(self._p("wifi.json"))
                return js, json.dumps({"ok": True, "saved": self.writable})
            elif path == "/api/reboot":
                self.flush()
                if self.reboot:
                    self.reboot()
                return js, json.dumps({"ok": True})
            else:
                raise ApiError(404, "not found")
            return js, json.dumps(self.state())

        if method == "GET" and path == "/api/history":
            return js, json.dumps({"games": self.index, "max": HISTORY_MAX})
        if path.startswith("/api/history/"):
            try:
                gid = int(path[13:])
            except ValueError:
                raise ApiError(404, "not found")
            if method == "GET":
                try:
                    with open(self._game_path(gid)) as f:
                        return js, f.read()
                except OSError:
                    raise ApiError(404, "game not found")
            if method == "DELETE":
                self._delete_record(gid)
                return js, json.dumps({"ok": True})
        if method == "GET" and path == "/api/info":
            wifi = _read_json(self._p("wifi.json"), None) or {}
            info = {"storage": self.writable, "synced": self.synced, "wifi_ssid": wifi.get("ssid", "")}
            info.update(self.net_info())
            return js, json.dumps(info)
        raise ApiError(404, "not found")
