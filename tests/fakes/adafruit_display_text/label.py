"""Stand-in for adafruit_display_text.label.Label: keeps the attributes panel.py and anim.py use."""


class Label:
    def __init__(self, font, text="", color=0xFFFFFF, anchor_point=(0, 0), **kwargs):
        self.font = font
        self.text = text
        self.color = color
        self.anchor_point = anchor_point
        self.anchored_position = (0, 0)
        self.hidden = False
        self.x = self.y = 0
        self._parent = None

    @property
    def bounding_box(self):
        return (0, 0, len(self.text) * self.font.width, self.font.height)
