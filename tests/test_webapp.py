"""Syntax-checks the web app's JavaScript (www/index.html) without running it.

Needs the QuickJS Python package, which understands modern JavaScript (?. and ??):
    python3 -m pip install quickjs        (in a virtual environment)
The test is skipped when it isn't installed.
"""

import json
import os
import re
import unittest

try:
    import quickjs
except ImportError:
    quickjs = None

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "www", "index.html")


@unittest.skipUnless(quickjs, "QuickJS isn't installed (python3 -m pip install quickjs)")
class WebApp(unittest.TestCase):
    def test_script_parses(self):
        with open(PAGE) as f:
            js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", f.read(), re.S))
        self.assertTrue(js)
        # new Function() compiles the script's code without running it (there's no browser here)
        quickjs.Context().eval("new Function(%s); 1" % json.dumps(js))


if __name__ == "__main__":
    unittest.main()
