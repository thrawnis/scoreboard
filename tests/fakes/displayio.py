"""Stand-in for CircuitPython's displayio, just enough to run panel.py on the computer.

It keeps the one rule that matters for the layout code: an element can only be in one group (or be
the display's root) at a time, otherwise ValueError, like the real thing."""


def _adopt(child, parent):
    if getattr(child, "_parent", None) is not None:
        raise ValueError("Layer already in a group")
    child._parent = parent


class Group:
    def __init__(self, scale=1, x=0, y=0):
        self.scale, self.x, self.y = scale, x, y
        self.hidden = False
        self._items = []
        self._parent = None

    def append(self, item):
        _adopt(item, self)
        self._items.append(item)

    def insert(self, i, item):
        _adopt(item, self)
        self._items.insert(i, item)

    def pop(self, i=-1):
        item = self._items.pop(i)
        item._parent = None
        return item

    def remove(self, item):
        for i, it in enumerate(self._items):
            if it is item:
                self.pop(i)
                return
        raise ValueError("not in group")

    def index(self, item):
        for i, it in enumerate(self._items):
            if it is item:
                return i
        raise ValueError("not in group")

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)


class Palette:
    def __init__(self, n):
        self._c = [0] * n
        self.transparent = set()

    def __getitem__(self, i):
        return self._c[i]

    def __setitem__(self, i, v):
        self._c[i] = v

    def __len__(self):
        return len(self._c)

    def make_transparent(self, i):
        self.transparent.add(i)


class Bitmap:
    def __init__(self, width, height, value_count):
        self.width, self.height = width, height
        self._px = {}

    def __setitem__(self, xy, v):
        self._px[xy] = v

    def __getitem__(self, xy):
        return self._px.get(xy, 0)


class TileGrid:
    def __init__(self, bitmap, pixel_shader=None, **kwargs):
        self.bitmap, self.pixel_shader = bitmap, pixel_shader
        self.x = self.y = 0
        self.hidden = False
        self._parent = None


def release_displays():
    pass


class FakeDisplay:
    """What panel.py needs from framebufferio.FramebufferDisplay."""

    width = 64
    height = 64

    def __init__(self):
        self._root = None

    @property
    def root_group(self):
        return self._root

    @root_group.setter
    def root_group(self, group):
        if group is self._root:
            return
        if self._root is not None:
            self._root._parent = None
        if group is not None:
            _adopt(group, self)
        self._root = group
