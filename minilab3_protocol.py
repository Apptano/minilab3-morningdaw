"""
Arturia MiniLab 3 - host -> controller protocol, as used by Ableton Live 12's own
MIDI Remote Script (Resources/MIDI Remote Scripts/MiniLab_3/midi.py, elements.py,
display_util.py, colors.py - decoded from the shipped .pyc files, Live 12, 2026-09-25 build).

Only messages that Ableton itself sends to the controller in normal use are defined here.
None of them writes to the controller's memory or firmware.
"""

SYSEX_START, SYSEX_END = 0xF0, 0xF7

# F0 00 20 6B 7F 42  (Arturia manufacturer ID 00 20 6B, device 7F, model 42 = MiniLab 3)
SYSEX_HEADER = (SYSEX_START, 0x00, 0x20, 0x6B, 0x7F, 0x42)
LED_HEADER = SYSEX_HEADER + (0x02, 0x02, 0x16)       # + led_id, r, g, b, F7   (r,g,b: 0..127)
DISPLAY_HEADER = SYSEX_HEADER + (0x04, 0x02, 0x60)    # + options..., lines..., F7


def connection_message(param):
    # Ableton: CONNECTION_MESSAGE = param 0x21, DISCONNECTION_MESSAGE = param 0x20
    return SYSEX_HEADER + (0x02, 0x00, 0x40, 0x6A, param, SYSEX_END)


CONNECT = connection_message(0x21)
DISCONNECT = connection_message(0x20)
# MiniLab 3 answers CONNECT with F0 00 20 6B 7F 42 02 00 40 63 00 F7 (observed 2026-11-07)
CONNECT_ACK = SYSEX_HEADER + (0x02, 0x00, 0x40, 0x63)

# PAD_ID_TO_SYSEX_ID from midi.py
#  - pads in DAW mode send Note On, channel 10, notes 36..43 (bank A) and 44..51 (bank B)
#  - transport buttons in DAW mode send CC on channel 1: 105 Loop, 106 Stop, 107 Play, 108 Record, 109 Tap
PAD_ID_TO_LED = {n: 0x34 + (n - 36) for n in range(36, 44)}
PAD_ID_TO_LED.update({n: 0x44 + (n - 44) for n in range(44, 52)})
PAD_ID_TO_LED.update({105: 0x57, 106: 0x58, 107: 0x59, 108: 0x5A, 109: 0x5B})

LED_NAMES = {
    "loop": 0x57, "stop": 0x58, "play": 0x59, "rec": 0x5A, "record": 0x5A, "tap": 0x5B,
}
for i in range(8):
    LED_NAMES[f"pad{i + 1}"] = 0x34 + i      # bank A, pads 1-8
    LED_NAMES[f"padb{i + 1}"] = 0x44 + i     # bank B, pads 1-8

# Colours from colors.py (Skin.Transport / Skin.Recording)
COLORS = {
    "off": (0, 0, 0),
    "white": (127, 127, 127), "white_half": (20, 20, 20),
    "red": (127, 0, 0), "red_half": (20, 0, 0), "red_low": (8, 0, 0),
    "green": (0, 127, 0), "green_half": (0, 20, 0),
    "blue": (0, 0, 127), "ocean": (20, 80, 127),
    "amber": (127, 50, 0), "amber_half": (20, 5, 0),
    "yellow": (127, 127, 0), "yellow_half": (20, 20, 0),
}


def led_message(led_id, rgb):
    r, g, b = (max(0, min(127, int(c))) for c in rgb)
    return LED_HEADER + (led_id & 0x7F, r, g, b, SYSEX_END)


# display_util.py
LINE_1_WIDTH, LINE_2_WIDTH = 10, 18
PICTO = {None: 0, "arp": 1, "play": 2, "record": 3, "arm": 4}


def _ascii(text, width):
    s = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(text))[:width].strip()
    return tuple(ord(c) for c in s)


def display_message(line1, line2="", transport=None, arm=False):
    """Full-screen, non-transient text (what Ableton shows: track/scene names).
    transport: None | 'play' | 'record' -> pictogram."""
    options = (0x1F, 0x07, 0x01, PICTO[transport], PICTO["arm"] if arm else 0, 0, 0x00)
    return (DISPLAY_HEADER + options + (0x01,) + _ascii(line1, LINE_1_WIDTH)
            + (0x00, 0x02) + _ascii(line2, LINE_2_WIDTH) + (0x00, SYSEX_END))


def hexs(msg):
    return " ".join(f"{b:02X}" for b in msg)
