"""Stand-in for adafruit_bitmap_font: checks the font file exists in the repo and reads its cell size."""

import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")


class Font:
    def __init__(self, path):
        self.path = path
        self.width, self.height = 6, 10
        with open(os.path.join(ROOT, path.lstrip("/"))) as f:  # a missing font fails like on the board
            for line in f:
                if line.startswith("FONTBOUNDINGBOX"):
                    self.width, self.height = (int(v) for v in line.split()[1:3])
                    break
        self.loaded = set()

    def load_glyphs(self, chars):
        self.loaded.update(chars)


def load_font(path):
    return Font(path)
