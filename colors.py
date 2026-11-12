# Team colours: a small palette chosen to look good on an LED matrix (bright, saturated, easy to tell
# apart on a dark background). White is left out on purpose because the board's text is white, and
# dark blues and purples are left out because they look dim on LEDs.

PALETTE = (
    ("Red", "FF3000"),
    ("Orange", "FF7000"),
    ("Yellow", "FFD000"),
    ("Green", "30FF30"),
    ("Cyan", "00E0FF"),
    ("Blue", "0090FF"),
    ("Purple", "A060FF"),
    ("Pink", "FF40FF"),
)

DEFAULTS = ("FF3000", "0090FF")  # team A red, team B blue

_HEXES = tuple(h for _, h in PALETTE)


def valid(hex_str):
    return isinstance(hex_str, str) and hex_str.upper() in _HEXES


def effective(raw):
    """The two team colours as "RRGGBB" strings: the saved choices, with defaults for anything missing."""
    out = []
    for i in range(2):
        pick = raw[i] if isinstance(raw, list) and i < len(raw) else None
        out.append(pick.upper() if valid(pick) else DEFAULTS[i])
    return out


def as_ints(raw):
    return [int(h, 16) for h in effective(raw)]
