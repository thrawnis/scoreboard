"""Stand-in for CircuitPython's vectorio (shapes), for running panel.py and anim.py on the computer."""


class _Shape:
    def __init__(self, pixel_shader=None, x=0, y=0, color_index=0, **kwargs):
        self.pixel_shader = pixel_shader
        self.x, self.y = x, y
        self.color_index = color_index
        self.hidden = False
        self._parent = None
        for k, v in kwargs.items():
            setattr(self, k, v)


class Rectangle(_Shape):
    def __init__(self, pixel_shader=None, width=1, height=1, x=0, y=0, color_index=0):
        if width < 1 or height < 1:
            raise ValueError("width and height must be at least 1")
        super().__init__(pixel_shader, x, y, color_index, width=width, height=height)


class Circle(_Shape):
    def __init__(self, pixel_shader=None, radius=1, x=0, y=0, color_index=0):
        super().__init__(pixel_shader, x, y, color_index, radius=radius)


class Polygon(_Shape):
    def __init__(self, pixel_shader=None, points=(), x=0, y=0, color_index=0):
        super().__init__(pixel_shader, x, y, color_index, points=list(points))
