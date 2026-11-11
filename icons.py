# 8x8 pixel-art sport icons for the LED panels. Each icon is a character map;
# "." is transparent and every other character is a colour from its legend.

import displayio

ICONS = {
    # holey yellow ball
    "pickleball": ({"Y": 0xFFD000, "o": 0x000000}, (
        "..YYYY..",
        ".YYoYYY.",
        "YYYYYoYY",
        "YoYYYYYY",
        "YYYYoYYY",
        "YYoYYYoY",
        ".YYYYYY.",
        "..YYYY..")),
    # optic yellow ball with a white seam
    "tennis": ({"G": 0xB8F020, "W": 0xFFFFFF}, (
        "..GGGG..",
        ".WGGGGW.",
        "GWGGGGWG",
        "GWGGGGWG",
        "GWGGGGWG",
        "GWGGGGWG",
        ".WGGGGW.",
        "..GGGG..")),
    # shuttlecock: feathers up, cork down
    "badminton": ({"W": 0xFFFFFF, "g": 0x808080, "C": 0xE08000}, (
        "W.W..W.W",
        "WgW..WgW",
        ".WgWWgW.",
        ".WgggW..",
        "..WggW..",
        "..WWWW..",
        "..CCCC..",
        "...CC...")),
    # red paddle and white ball
    "pingpong": ({"R": 0xFF1010, "B": 0xC08040, "W": 0xFFFFFF}, (
        ".RRR....",
        "RRRRR..W",
        "RRRRR...",
        "RRRRR...",
        ".RRR....",
        "..BB....",
        "...BB...",
        "....BB..")),
    # volleyball panels
    "volleyball": ({"W": 0xFFFFFF, "B": 0x2060FF, "Y": 0xFFD000}, (
        "..WWBB..",
        ".WWBBYY.",
        "WWBBYYWW",
        "WBBYYWWW",
        "BBYYWWWB",
        "BYYWWWBB",
        ".YWWWBB.",
        "..WWBB..")),
    # blue ball with a shine
    "racquetball": ({"B": 0x1040FF, "W": 0xFFFFFF}, (
        "..BBBB..",
        ".BWWBBB.",
        "BBWBBBBB",
        "BBBBBBBB",
        "BBBBBBBB",
        "BBBBBBBB",
        ".BBBBBB.",
        "..BBBB..")),
    # player on a rod
    "foosball": ({"g": 0x909090, "R": 0xFF3000, "W": 0xFFFFFF}, (
        "...RR...",
        "gggRRggg",
        "..RRRR..",
        "...RR...",
        "...RR...",
        "..R..R..",
        "..R..RW.",
        "......W.")),
    # mallet and puck
    "airhockey": ({"R": 0xFF2020, "W": 0xFFFFFF, "k": 0x404040}, (
        "..RR....",
        ".RWWR...",
        "RWRRWR..",
        ".RWWR...",
        "..RR....",
        ".....kk.",
        "....kWWk",
        ".....kk.")),
    # puck sliding into the scoring zone
    "shuffleboard": ({"R": 0xFF3000, "W": 0xFFFFFF, "g": 0x707070}, (
        "g......g",
        "g..RR..g",
        "g.RWWR.g",
        "g.RWWR.g",
        "g..RR..g",
        "gggggggg",
        "g.W.W.Wg",
        "g......g")),
    # basketball
    "timed_basketball": ({"O": 0xFF6000, "k": 0x000000}, (
        "..OkOO..",
        ".OOkOOO.",
        "OOOkOOOO",
        "kkkkkkkk",
        "OOOkOOOO",
        "OOOkOOOO",
        ".OOkOOO.",
        "..OkOO..")),
    # soccer ball
    "timed_soccer": ({"W": 0xFFFFFF, "k": 0x101010, "g": 0x606060}, (
        "..WWWW..",
        ".WWkkWW.",
        "WWkkkkWW",
        "WgWkkWgW",
        "WggWWggW",
        "WWWWWWWW",
        ".WkWWkW.",
        "..WWWW..")),
    # stick and puck
    "timed_hockey": ({"B": 0xC08040, "W": 0xFFFFFF, "k": 0x303030}, (
        ".....B..",
        ".....B..",
        "....B...",
        "....B...",
        "...B....",
        "BBBB....",
        "....kkk.",
        "....kkk.")),
    # football with laces
    "timed_football": ({"B": 0x8B4513, "W": 0xFFFFFF}, (
        "........",
        "..BBBB..",
        ".BBWBBB.",
        "BBWWWWBB",
        "BBBWBBBB",
        ".BBBBBB.",
        "..BBBB..",
        "........")),
    # stopwatch
    "timed_custom": ({"W": 0xFFFFFF, "R": 0xFF3000}, (
        "...WW...",
        "..WWWW..",
        ".W....W.",
        "W...R..W",
        "W...R..W",
        "W......W",
        ".W....W.",
        "..WWWW..")),
    # plus and minus
    "misc": ({"G": 0x30FF30, "R": 0xFF3000}, (
        "..G.....",
        "..G.....",
        "GGGGG...",
        "..G.....",
        "..G.....",
        "........",
        "...RRRRR",
        "........")),
}

# Short names for the start-of-match splash (at most 10 characters for one panel).
NAMES = {
    "pickleball": "PICKLEBALL", "tennis": "TENNIS", "badminton": "BADMINTON", "pingpong": "PING PONG",
    "volleyball": "VOLLEYBALL", "racquetball": "RACQUET", "foosball": "FOOSBALL", "airhockey": "AIR HOCKEY",
    "shuffleboard": "SHUFFLE", "timed_basketball": "BASKETBALL", "timed_soccer": "SOCCER",
    "timed_hockey": "HOCKEY", "timed_football": "FOOTBALL", "timed_custom": "GAME CLOCK", "misc": "SCORES",
}


def icon_key(cfg):
    sport = cfg.get("sport", "pickleball")
    if sport == "timed":
        return "timed_" + cfg.get("preset", "custom")
    return sport


def make(key, pct=100):
    """A TileGrid showing the icon (falls back to the misc icon)."""
    colors, rows = ICONS.get(key, ICONS["misc"])
    legend = list(colors)
    bitmap = displayio.Bitmap(8, 8, len(legend) + 1)
    palette = displayio.Palette(len(legend) + 1)
    palette.make_transparent(0)
    for i, ch in enumerate(legend):
        c = colors[ch]
        r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
        palette[i + 1] = (r * pct // 100) << 16 | (g * pct // 100) << 8 | (b * pct // 100)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in colors:
                bitmap[x, y] = legend.index(ch) + 1
    return displayio.TileGrid(bitmap, pixel_shader=palette)
