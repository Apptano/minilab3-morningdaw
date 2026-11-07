"""
MiniLab 3 / MorningDAW MIDI diagnostic tool.

  python minilab3_diag.py ports
  python minilab3_diag.py monitor [--port Minilab3] [--port "MorningDAW Feedback"] [--log file.log]
  python minilab3_diag.py led play green           (named LED + named colour)
  python minilab3_diag.py led pad6 0 0 127         (named LED + r g b 0..127)
  python minilab3_diag.py led 0x59 off
  python minilab3_diag.py connect | disconnect     (Ableton's DAW connection handshake)
  python minilab3_diag.py oled "MorningDAW" "Track 1"
  python minilab3_diag.py note 10 41 127           (channel 1-16, note, velocity)
  python minilab3_diag.py cc 1 107 127
  python minilab3_diag.py sweep                    (all transport LEDs through on/half/off, slowly)

Every outgoing message is printed and appended to the log with time, port and direction.
Only documented messages can be sent (see minilab3_protocol.py) - no raw SysEx.
Ports are opened shared (Windows MIDI Services / MidiSrv); nothing is taken from a DAW.
"""
import argparse
import datetime
import sys
import time

import rtmidi

import minilab3_protocol as P

DEFAULT_OUT = "Minilab3 MIDI"
LOG = "minilab3_diag.log"


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(line, path=LOG):
    print(line, flush=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_port(midi, name):
    ports = midi.get_ports()
    for i, p in enumerate(ports):
        if p.lower().startswith(name.lower()):
            return i, p
    for i, p in enumerate(ports):
        if name.lower() in p.lower():
            return i, p
    sys.exit(f"Port '{name}' not found. Available: {ports}")


def describe(msg):
    st = msg[0]
    if st == 0xF0:
        if tuple(msg[:9]) == P.LED_HEADER:
            return f"SysEx LED id=0x{msg[9]:02X} rgb={tuple(msg[10:13])}"
        if tuple(msg[:9]) == P.DISPLAY_HEADER:
            return "SysEx DISPLAY"
        if tuple(msg[:6]) == P.SYSEX_HEADER:
            return "SysEx Arturia MiniLab3"
        return "SysEx"
    kind, ch = st & 0xF0, (st & 0x0F) + 1
    names = {0x80: "NoteOff", 0x90: "NoteOn", 0xA0: "PolyAT", 0xB0: "CC", 0xC0: "PC",
             0xD0: "ChanAT", 0xE0: "PitchBend"}
    if kind in names and len(msg) >= 2:
        rest = " ".join(str(b) for b in msg[1:])
        return f"{names[kind]:9s} ch{ch:<2d} {rest}"
    return "System"


def cmd_ports(_):
    print("INPUTS :", rtmidi.MidiIn().get_ports())
    print("OUTPUTS:", rtmidi.MidiOut().get_ports())


def cmd_monitor(a):
    names = a.port or ["Minilab3"]
    ins = []
    probe = rtmidi.MidiIn()
    for idx, pname in enumerate(probe.get_ports()):
        if any(n.lower() in pname.lower() for n in names):
            mi = rtmidi.MidiIn()
            mi.ignore_types(sysex=False, timing=True, active_sense=True)
            mi.open_port(idx)

            def cb(event, data, pname=pname):
                msg, _ = event
                log(f"{now()}  IN   [{pname}]  {P.hexs(msg):<40s} {describe(msg)}", a.log)

            mi.set_callback(cb)
            ins.append(mi)
            log(f"{now()}  listening on [{pname}]", a.log)
    if not ins:
        sys.exit(f"No input matches {names}")
    print("Ctrl+C to stop")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


class Out:
    def __init__(self, name, logpath):
        self.mo = rtmidi.MidiOut()
        idx, self.name = find_port(self.mo, name)
        self.mo.open_port(idx)
        self.logpath = logpath

    def send(self, msg):
        msg = list(msg)
        self.mo.send_message(msg)
        log(f"{now()}  OUT  [{self.name}]  {P.hexs(msg):<40s} {describe(msg)}", self.logpath)
        time.sleep(0.005)


def parse_led(s):
    s = s.lower()
    if s in P.LED_NAMES:
        return P.LED_NAMES[s]
    v = int(s, 0)
    if v not in P.PAD_ID_TO_LED.values():
        sys.exit(f"LED id 0x{v:02X} is not one of the documented ids {sorted(P.PAD_ID_TO_LED.values())}")
    return v


def parse_rgb(vals):
    if len(vals) == 1:
        return P.COLORS[vals[0].lower()]
    if len(vals) == 3:
        return tuple(int(v) for v in vals)
    sys.exit("colour: a name (" + ", ".join(P.COLORS) + ") or three numbers r g b (0-127)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT, help="output port name (default: Minilab3 MIDI)")
    ap.add_argument("--log", default=LOG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    m = sub.add_parser("monitor"); m.add_argument("--port", action="append")
    l = sub.add_parser("led"); l.add_argument("led"); l.add_argument("color", nargs="+")
    sub.add_parser("connect"); sub.add_parser("disconnect")
    o = sub.add_parser("oled"); o.add_argument("line1"); o.add_argument("line2", nargs="?", default="")
    o.add_argument("--picto", choices=["play", "record"], default=None)
    n = sub.add_parser("note"); n.add_argument("ch", type=int); n.add_argument("num", type=int); n.add_argument("vel", type=int)
    c = sub.add_parser("cc"); c.add_argument("ch", type=int); c.add_argument("num", type=int); c.add_argument("val", type=int)
    sub.add_parser("sweep")
    a = ap.parse_args()

    if a.cmd == "ports":
        return cmd_ports(a)
    if a.cmd == "monitor":
        return cmd_monitor(a)

    out = Out(a.out, a.log)
    if a.cmd == "led":
        out.send(P.led_message(parse_led(a.led), parse_rgb(a.color)))
    elif a.cmd == "connect":
        out.send(P.CONNECT)
    elif a.cmd == "disconnect":
        out.send(P.DISCONNECT)
    elif a.cmd == "oled":
        out.send(P.display_message(a.line1, a.line2, a.picto))
    elif a.cmd == "note":
        out.send((0x90 | (a.ch - 1), a.num & 0x7F, a.vel & 0x7F))
    elif a.cmd == "cc":
        out.send((0xB0 | (a.ch - 1), a.num & 0x7F, a.val & 0x7F))
    elif a.cmd == "sweep":
        for name, on, half in [("loop", "amber", "amber_half"), ("stop", "white", "white_half"),
                               ("play", "green", "green_half"), ("rec", "red", "red_half"),
                               ("tap", "white", "white_half")]:
            for col in (on, half, "off"):
                print(f"-> {name}: {col}")
                out.send(P.led_message(P.LED_NAMES[name], P.COLORS[col]))
                time.sleep(1.0)


if __name__ == "__main__":
    main()
