"""Tests for the parts of the scoreboard that are plain Python (scoring rules, app, API, storage).

Run from the repository root:  python3 -m unittest discover -s tests
They run on the computer (CPython); the display code needs the board and is not covered here.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import colors  # noqa: E402
import scoreapp  # noqa: E402
from scoreapp import ROSTER_MAX, App  # noqa: E402

DOUBLES = {"sport": "pickleball", "mode": "doubles"}
TEAMS = [{"name": "Team A", "players": ["AL", "BO"]}, {"name": "Team B", "players": ["CY", "DI"]}]


class Board(unittest.TestCase):
    """Each test gets a fresh App on an empty data directory."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.app = App(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, method, path, body=None):
        raw = body if isinstance(body, str) else ("" if body is None else json.dumps(body))
        return self.app.handle(method, path, {}, raw)

    def ok(self, method, path, body=None):
        code, _, text = self.call(method, path, body)
        self.assertEqual(code, 200, text)
        return text

    def start(self, cfg=DOUBLES, teams=TEAMS, first=0):
        self.ok("POST", "/api/match", {"cfg": cfg, "teams": teams, "first_serving": first})
        return self.app.match

    def rally(self, team):
        self.ok("POST", "/api/rally", {"team": team})

    def call_text(self):
        return self.app.match.game.call()


class PickleballRules(Board):
    def test_first_serve_is_server_two(self):
        m = self.start()
        self.assertEqual(self.call_text(), "0-0-2")
        self.assertEqual(m.game.serving, 0)
        self.assertEqual(m.game.server_idx, 0)  # the player starting on the right court
        self.assertEqual(m.game.side(), "R")

    def test_serving_team_scores_and_server_switches_courts(self):
        m = self.start()
        self.rally(0)
        self.assertEqual(self.call_text(), "1-0-2")
        self.assertEqual(m.game.server_idx, 0)  # same server ...
        self.assertEqual(m.game.side(), "L")  # ... now on the left court

    def test_side_out_then_first_and_second_server(self):
        m = self.start()
        self.rally(1)  # server 2 loses the rally: side out
        self.assertEqual(self.call_text(), "0-0-1")
        self.assertEqual(m.game.serving, 1)
        self.assertEqual(m.game.server_idx, 0)  # first server is on the right court
        self.rally(0)  # server 1 loses: the partner serves
        self.assertEqual(self.call_text(), "0-0-2")
        self.assertEqual(m.game.server_idx, 1)
        self.rally(0)  # server 2 loses: side out back to team A
        self.assertEqual(self.call_text(), "0-0-1")
        self.assertEqual(m.game.serving, 0)

    def test_receiving_team_never_scores_in_side_out_scoring(self):
        self.start()
        for _ in range(6):
            self.rally(1 - self.app.match.game.serving)  # always the receiving team
        self.assertEqual(self.app.match.game.unit_score(), [0, 0])

    def test_rally_scoring_receivers_score_and_serve(self):
        m = self.start(cfg={"sport": "pickleball", "mode": "doubles", "scoring": "rally"})
        self.rally(1)
        self.assertEqual(m.game.unit_score(), [0, 1])
        self.assertEqual(m.game.serving, 1)
        # odd score: the player on the left court serves
        self.assertEqual(m.game.side(), "L")

    def test_win_by_two(self):
        m = self.start(cfg={"sport": "pickleball", "mode": "singles"},
                       teams=[{"players": ["AL"]}, {"players": ["CY"]}])
        game = m.game
        game.scores = [10, 10]
        game.serving = 0
        self.rally(0)  # 11-10: not over yet
        self.assertIsNone(self.app.match.game.winner)
        self.rally(0)  # 12-10: won by two
        self.assertEqual(self.app.match.game.winner, 0)

    def test_singles_side_follows_the_servers_score(self):
        m = self.start(cfg={"sport": "pickleball", "mode": "singles"},
                       teams=[{"players": ["AL"]}, {"players": ["CY"]}])
        self.assertEqual(m.game.side(), "R")
        self.rally(0)
        self.assertEqual(m.game.side(), "L")

    def test_undo_restores_the_previous_call(self):
        self.start()
        self.rally(1)
        before = self.call_text()
        self.rally(0)
        self.ok("POST", "/api/undo", {})
        self.assertEqual(self.call_text(), before)


class Remote(Board):
    def test_point_and_loss_follow_the_remote_team(self):
        self.start()  # team A serves; the remote defaults to team B
        self.ok("POST", "/api/w/loss")  # a rally for team A: A scores
        self.assertEqual(self.app.match.game.unit_score(), [1, 0])
        self.ok("POST", "/api/settings", {"remote_team": "a"})
        self.ok("POST", "/api/w/point")  # a rally for team A again
        self.assertEqual(self.app.match.game.unit_score(), [2, 0])


class Validation(Board):
    """Malformed values must be refused with 400, never escape (that would stop the board)."""

    BAD = [
        ("POST", "/api/settings", {"brightness": None}),
        ("POST", "/api/settings", {"brightness": "x"}),
        ("POST", "/api/positions", {"right": 5}),
        ("POST", "/api/rally", {"team": 0, "n": "x"}),
        ("POST", "/api/clock", {"action": "set", "secs": "x"}),
        ("POST", "/api/settings", {"colors": ["123456"]}),
        ("POST", "/api/roster", {"add": "TOOLONG"}),
        ("POST", "/api/match", {"cfg": DOUBLES, "teams": [{"players": ["TOOLONG", "B"]}, {"players": ["C", "D"]}]}),
        ("POST", "/api/match", "not json"),
    ]

    def test_bad_requests_are_refused(self):
        self.start()
        for method, path, body in self.BAD:
            with self.subTest(path=path, body=body):
                code, _, _ = self.call(method, path, body)  # must not raise
                self.assertIn(code, (400, 409), (path, body))

    def test_odd_team_entries_still_start_a_saved_match(self):
        self.ok("POST", "/api/match", {"cfg": {"sport": "timed"}, "teams": ["x", "y"]})
        self.assertEqual(self.app.match.sport, "timed")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "current.json")))


class Roster(Board):
    def test_new_match_remembers_players_upper_case_most_recent_first(self):
        self.start(teams=[{"players": ["al", "Bo"]}, {"players": ["CY", "di"]}])
        self.assertEqual(self.app.roster[:4], ["AL", "BO", "CY", "DI"])
        self.start(teams=[{"players": ["ZED", "AL"]}, {"players": ["CY", "DI"]}])
        self.assertEqual(self.app.roster[:2], ["ZED", "AL"])
        self.assertEqual(len(self.app.roster), len(set(self.app.roster)))  # no duplicates

    def test_add_forget_and_limit(self):
        for i in range(ROSTER_MAX + 5):
            self.ok("POST", "/api/roster", {"add": "N%d" % i})
        self.assertEqual(len(self.app.roster), ROSTER_MAX)
        self.ok("POST", "/api/roster", {"remove": "n%d" % (ROSTER_MAX + 4)})  # any case
        self.assertNotIn("N%d" % (ROSTER_MAX + 4), self.app.roster)

    def test_survives_a_restart(self):
        self.ok("POST", "/api/roster", {"add": "KEEP"})
        self.assertIn("KEEP", App(self.dir).roster)


class Storage(Board):
    def test_settings_do_not_rewrite_the_match_file(self):
        self.start()
        self.app.flush()
        path = os.path.join(self.dir, "current.json")
        before = os.stat(path).st_mtime_ns
        v = self.app.version
        self.ok("POST", "/api/settings", {"brightness": 50, "court": "same", "colors": ["FF7000"]})
        self.assertGreater(self.app.version, v)  # phones and the board still redraw
        self.assertIsNone(self.app.save_due)
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_power_cut_between_remove_and_rename(self):
        m = self.start()
        self.rally(0)
        self.app.flush()
        os.rename(os.path.join(self.dir, "current.json"), os.path.join(self.dir, "current.json.tmp"))
        again = App(self.dir)
        self.assertEqual(again.match.d["id"], m.d["id"])
        self.assertEqual(again.match.game.unit_score(), [1, 0])

    def test_lost_settings_never_reuse_a_game_id(self):
        self.start()
        self.rally(0)
        self.ok("POST", "/api/end_match", {})  # saved to the history
        used = [s["id"] for s in self.app.index]
        os.remove(os.path.join(self.dir, "meta.json"))
        again = App(self.dir)
        self.assertGreater(again.meta["next_id"], max(used))


class History(Board):
    def play_games(self, count):
        for i in range(count):
            self.start()
            self.rally(0)
            self.ok("POST", "/api/end_match", {})  # saved to the history (as abandoned)

    def games_on_disk(self):
        return sorted(os.listdir(os.path.join(self.dir, "games")))

    def test_only_the_last_twenty_games_are_kept(self):
        self.play_games(25)
        self.assertEqual(len(self.app.index), scoreapp.HISTORY_MAX)
        self.assertEqual(scoreapp.HISTORY_MAX, 20)
        self.assertEqual(len(self.games_on_disk()), 20)
        ids = [s["id"] for s in self.app.index]
        self.assertEqual(ids, sorted(ids, reverse=True))  # newest first: the oldest five went

    def test_a_longer_history_is_trimmed_at_start_up(self):
        saved = scoreapp.HISTORY_MAX
        scoreapp.HISTORY_MAX = 100  # as before this limit existed
        try:
            self.play_games(24)
        finally:
            scoreapp.HISTORY_MAX = saved
        self.assertEqual(len(self.games_on_disk()), 24)
        again = App(self.dir)
        self.assertEqual(len(again.index), 20)
        self.assertEqual(len(self.games_on_disk()), 20)


class RemovedSports(Board):
    def test_a_saved_match_from_the_removed_team_scorer_is_dropped(self):
        old = {"id": 5, "cfg": {"sport": "misc", "teams": 3, "games": 1},
               "teams": [{"name": "A", "players": []}, {"name": "B", "players": []}, {"name": "C", "players": []}],
               "start": 1.8e9, "games": [], "cur": {"id": 6, "n": 1, "start": 1.8e9, "sv": 0, "events": []}}
        with open(os.path.join(self.dir, "current.json"), "w") as f:
            json.dump(old, f)
        self.assertIsNone(App(self.dir).match)

    def test_asking_for_it_gives_pickleball(self):
        self.ok("POST", "/api/match", {"cfg": {"sport": "misc", "teams": 4}, "teams": TEAMS})
        self.assertEqual(self.app.match.sport, "pickleball")
        self.assertEqual(self.app.match.n_teams, 2)


class Colours(unittest.TestCase):
    def test_defaults_are_red_and_blue(self):
        self.assertEqual(colors.effective(None)[:2], ["FF3000", "0090FF"])

    def test_only_palette_colours_are_accepted(self):
        self.assertTrue(colors.valid("ff7000"))
        self.assertFalse(colors.valid("123456"))
        self.assertEqual(colors.effective(["a060ff"])[0], "A060FF")


if __name__ == "__main__":
    unittest.main()
