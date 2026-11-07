"""
Decoder for what a DAW sends TO a Mackie Control (MCU) surface - standard Mackie protocol.
Used to read MorningDAW's built-in MCU driver output (track names on the LCD, Select /
Cycle / Mute / Solo / Rec LEDs) from a virtual port, and turn it into MiniLab 3 OLED/LEDs.

  LCD   : F0 00 00 66 <14 main | 15 XT> 12 <offset 0..111> <ascii...> F7
          2 rows x 56 chars, strip i = chars 7*i .. 7*i+6 (row 1 = names)
  Meter : D0 <strip*16 + level 0..12>, ~10 per second while a track sounds
  LEDs  : Note On ch1, velocity 7F on / 01 blink / 00 off
          00-07 Rec arm, 08-0F Solo, 10-17 Mute, 18-1F Select,
          56 Cycle, 5B Rewind, 5C FF, 5D Stop, 5E Play, 5F Record, 59 Click
"""

MCU_LCD_PREFIX = (0xF0, 0x00, 0x00, 0x66)
LED_NAMES = {0x56: "cycle", 0x5D: "stop", 0x5E: "play", 0x5F: "record", 0x59: "click",
             0x5B: "rewind", 0x5C: "forward"}


class McuState:
    def __init__(self):
        self.lcd = [" "] * 112
        self.rec = [False] * 8
        self.solo = [False] * 8
        self.mute = [False] * 8
        self.select = [False] * 8
        self.leds = {}            # name -> bool
        self.ignored = 0
        # 7-segment timecode / bars-beats display: CC 0x40 (rightmost) .. 0x49 (leftmost)
        self.tc = [" "] * 10
        self.tc_dot = [False] * 10
        # meters: D0 <strip*16 + level>, level 0..12 (0x0E overload, 0x0F clear overload)
        self.meter = [0] * 8

    def feed(self, msg):
        """Returns a short description of what changed, or None."""
        if not msg:
            return None
        if msg[0] == 0xF0 and tuple(msg[:4]) == MCU_LCD_PREFIX and len(msg) > 7 and msg[5] == 0x12:
            off = msg[6]
            text = msg[7:-1] if msg[-1] == 0xF7 else msg[7:]
            for i, c in enumerate(text):
                if 0 <= off + i < 112:
                    self.lcd[off + i] = chr(c) if 32 <= c < 127 else " "
            return "lcd"
        st = msg[0] & 0xF0
        if st == 0xD0 and len(msg) >= 2:
            strip, level = msg[1] >> 4, msg[1] & 0x0F
            if strip < 8 and level != 0x0F:
                self.meter[strip] = min(level, 12)
                return f"meter{strip + 1}"
            return "meter"
        if st == 0xB0 and len(msg) >= 3 and 0x40 <= msg[1] <= 0x49:
            pos = 9 - (msg[1] - 0x40)                 # index 0 = leftmost digit
            v = msg[2]
            self.tc_dot[pos] = bool(v & 0x40)
            c = v & 0x3F
            self.tc[pos] = chr(c) if 0x20 <= c <= 0x39 else (chr(c + 0x40) if 1 <= c <= 0x1A else " ")
            return "timecode"
        if st in (0x90, 0x80) and len(msg) >= 3:
            n, on = msg[1], (st == 0x90 and msg[2] > 0)
            if n < 0x08:
                self.rec[n] = on; return f"rec{n + 1}"
            if n < 0x10:
                self.solo[n - 8] = on; return f"solo{n - 7}"
            if n < 0x18:
                self.mute[n - 16] = on; return f"mute{n - 15}"
            if n < 0x20:
                self.select[n - 24] = on; return f"select{n - 23}"
            if n in LED_NAMES:
                self.leds[LED_NAMES[n]] = on; return LED_NAMES[n]
        self.ignored += 1
        return None

    def timecode(self):
        """The display as shown, e.g. '  1. 3. 2.000' -> dots after digits that carry them."""
        return "".join(c + ("." if d else "") for c, d in zip(self.tc, self.tc_dot))

    def strip_name(self, i, row=0):
        s = "".join(self.lcd[row * 56 + 7 * i: row * 56 + 7 * i + 7])
        return s.strip()

    def selected(self):
        for i, s in enumerate(self.select):
            if s:
                return i
        return None
