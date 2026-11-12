"""Runs the real panel.py and anim.py against stand-in display libraries (tests/fakes), with a fake
clock, through the screens the board shows. It catches crashes and wrong text or layout choices; it
can't show what the LEDs look like (use the serial `snap` command on the board for that).

Run from the repository root:  python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fakes"))
sys.path.insert(0, os.path.join(HERE, ".."))

import anim  # noqa: E402
import displayio  # noqa: E402  (the stand-in from tests/fakes)
import panel  # noqa: E402
from scoreapp import App  # noqa: E402

NET = {"mode": "ap", "ap_ssid": "Scoreboard", "ap_password": "pickleball", "ip": "192.168.4.1", "host": ""}


class Clock:
    """Stands in for time.monotonic() in panel.py and anim.py, and for the app's wall clock."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def wall(self):
        return 1.8e9 + self.t


class Screens(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.clock = Clock()
        self._saved = (panel.time, anim.time)
        panel.time = anim.time = self.clock
        self.app = App(self.dir, clock=self.clock.wall)
        self.panel = panel.make_panel(displayio.FakeDisplay())

    def tearDown(self):
        panel.time, anim.time = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_for(self, seconds, step=0.25):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += step
            self.panel.update(self.app, NET)

    def api(self, path, body):
        import json
        code, _, text = self.app.handle("POST", path, {}, json.dumps(body))
        self.assertEqual(code, 200, text)

    def start(self, cfg, players=(("AL", "BO"), ("CY", "DI"))):
        self.api("/api/match", {"cfg": cfg, "teams": [{"name": "Team A", "players": list(players[0])},
                                                      {"name": "Team B", "players": list(players[1])}]})
        self.run_for(3)  # past the start-of-match splash

    def shown(self):
        return self.panel._shown

    # ---------------------------------------------------------------- tests

    def test_start_screen_shows_the_wifi_details(self):
        self.run_for(0.5)
        p = self.panel
        self.assertIs(self.shown(), p.start_group)
        self.assertEqual([lbl.text for lbl in p.s_lines], ["Scoreboard", "p---------", "192.168.4.1"])

    def test_pickleball_court_view_and_call(self):
        self.start({"sport": "pickleball", "mode": "doubles"})
        p = self.panel
        self.assertIs(self.shown(), p.court_group)
        self.assertEqual("".join(lbl.text for lbl in p.c_call).strip(), "0-0-2")
        names = sorted(lbl.text for row in p.c_slots for lbl in row)
        self.assertEqual(names, ["AL", "BO", "CY", "DI"])

    def test_a_point_plays_an_animation_then_returns_to_the_court(self):
        self.start({"sport": "pickleball", "mode": "doubles"})
        self.api("/api/rally", {"team": 0})
        self.run_for(0.25)
        self.assertTrue(self.panel.anim.active)
        self.assertIs(self.shown(), self.panel.anim.group)
        self.run_for(4)
        self.assertFalse(self.panel.anim.active)
        self.assertIs(self.shown(), self.panel.court_group)
        self.assertEqual("".join(lbl.text for lbl in self.panel.c_call).strip(), "1-0-2")

    def test_switch_ends_message(self):
        self.start({"sport": "pickleball", "mode": "singles"}, players=(("AL",), ("CY",)))
        for _ in range(6):  # 6-0: half way to 11 in the only game
            self.api("/api/rally", {"team": 0})
        self.run_for(3)
        self.assertIs(self.shown(), self.panel.msg_group)
        self.assertEqual(self.panel.msg_top.text, "SWITCH")

    def test_idle_timer_stops_at_twenty_minutes(self):
        self.start({"sport": "pickleball", "mode": "doubles"})
        self.run_for(19 * 60, step=30)
        self.assertNotEqual(self.panel.c_timer.text, "--:--")
        self.run_for(2 * 60, step=30)
        self.assertEqual(self.panel.c_timer.text, "--:--")

    def test_tennis_uses_the_standard_layout(self):
        self.start({"sport": "tennis", "mode": "singles"}, players=(("AL",), ("CY",)))
        self.assertIs(self.shown(), self.panel.score_group)
        self.assertEqual([lbl.text for lbl in self.panel.names], ["A", "B"])  # "Team " dropped

    def test_timed_game_layout(self):
        self.start({"sport": "timed", "preset": "basketball"}, players=((), ()))
        self.assertIs(self.shown(), self.panel.timed_group)
        self.assertTrue(self.panel.t_clock.text.startswith("12:00"))

    def test_settings_changes_redraw(self):
        self.start({"sport": "pickleball", "mode": "doubles"})
        self.api("/api/settings", {"brightness": 30, "colors": ["FF7000", "A060FF"], "court": "mirror"})
        self.run_for(0.5)
        self.assertEqual(panel.TEAM_COLORS[:2], [0xFF7000, 0xA060FF])
        self.assertIs(self.shown(), self.panel.court_group)

    def test_test_pattern_then_back(self):
        self.start({"sport": "pickleball", "mode": "doubles"})
        self.panel.test_fill(0xFFFFFF, 2)
        self.run_for(1)
        self.assertIs(self.shown(), self.panel.test_group)
        self.run_for(2)
        self.assertIs(self.shown(), self.panel.court_group)


if __name__ == "__main__":
    unittest.main()
