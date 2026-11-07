"""
MorningDAW -> Arturia MiniLab 3 bridge (LEDs + OLED).

Two state sources, both plain MIDI out of MorningDAW into loopMIDI ports:

 1. "MorningDAW Feedback"  - output of the .mdmidimap profile ("Send values back").
    Same message as the mapped control, 127 = on / 0 = off. Gives Play, Record, Mute,
    Solo, Arm. (MorningDAW 1.6.6 sends NO feedback for transport.loop.)
 2. "MorningDAW MCU"       - output of MorningDAW's built-in Mackie Control driver.
    Standard MCU: LCD SysEx with track names, Select / Cycle / Mute / Solo / Rec LEDs.
    Gives the selected track's name and the Loop (Cycle) state.

MiniLab 3 only takes LED colours and OLED text as Arturia SysEx (the messages Ableton's
MiniLab_3 script sends), so this bridge translates.

  python minilab3_bridge.py [--config bridge_config.json] [--verbose]
"""
import argparse
import datetime
import json
import os
import signal
import sys
import threading
import time

import rtmidi

import minilab3_protocol as P
from mcu_decoder import McuState

HERE = os.path.dirname(os.path.abspath(__file__))


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


class Bridge:
    def __init__(self, cfg, verbose=False):
        self.cfg = cfg
        self.verbose = verbose
        self.lock = threading.RLock()
        self.logf = open(os.path.join(HERE, cfg.get("log", "bridge.log")), "a", encoding="utf-8")
        self.state = {}            # led name -> bool
        self.leds = {}             # led name -> spec
        self.by_feedback = {}      # (kind, ch, num) -> led name
        self.by_press = {}         # (kind, ch, num) from controller -> led name
        self.by_mcu = {}           # mcu led name ("cycle", "play", ...) -> led name
        for spec in cfg["leds"]:
            name = spec["name"]
            led_id = spec["led"]
            spec["_id"] = P.LED_NAMES[led_id] if isinstance(led_id, str) else int(led_id)
            spec["_on"] = self._color(spec.get("on", "white"))
            spec["_off"] = self._color(spec.get("off", "off"))
            # "on_while": {"record": "red"} -> colour used while ON and that other LED's state is ON
            spec["_on_while"] = {k: self._color(v) for k, v in spec.get("on_while", {}).items()}
            # "meter": 1..8 -> when OFF, the pad shows meter_color while that MCU strip sounds
            spec["_meter_color"] = self._color(spec.get("meter_color", "green"))
            self.leds[name] = spec
            self.state[name] = False
            fb = spec.get("feedback")
            if fb:
                self.by_feedback[self._key(fb)] = name
            if spec.get("mcu"):
                self.by_mcu[spec["mcu"]] = name
            for pr in spec.get("press", [fb] if fb else []):
                self.by_press[self._key(pr)] = name

        self.pending = {}          # led name -> Timer that reverts an optimistic toggle
        self.meter_seen = [0.0] * 8    # last time each MCU strip's meter was above the threshold
        self.sounding = [False] * 8
        self.meter_step = [0] * 8      # brightness step shown (meter_mode "level")
        self.mcu = McuState()
        self.track_name = ""
        self.track_index = None
        self._display_timer = None
        self._last_display = None
        self._last_lcd_logged = None

        self.inputs = []
        self.ack = threading.Event()
        self.open_ports()

    def open_ports(self):
        cfg = self.cfg
        self.out = self._open(rtmidi.MidiOut(), cfg["minilab_out"])
        self.inputs = []
        self._listen(cfg.get("feedback_in"), self.on_feedback, sysex=False)
        self._listen(cfg.get("mcu_in"), self.on_mcu, sysex=True)
        self._listen(cfg.get("minilab_in"), self.on_controller, sysex=True)
        # commands the profile cannot express (e.g. select next / previous track): sent as Mackie
        # buttons to MorningDAW's Mackie Control driver, whose input is this loopMIDI port
        self.ctl = self._open(rtmidi.MidiOut(), cfg["ctl_out"], required=False) if cfg.get("ctl_out") else None
        self.commands = {self._key(c["from"]): c for c in cfg.get("commands", [])}

    def close_ports(self):
        for mi in self.inputs:
            try:
                mi.cancel_callback(); mi.close_port()
            except Exception:
                pass
        for port in (self.out, getattr(self, "ctl", None)):
            try:
                if port:
                    port.close_port()
            except Exception:
                pass
        self.inputs = []

    def minilab_present(self):
        try:
            return any(p.lower().startswith(self.cfg["minilab_out"].lower())
                       for p in rtmidi.MidiOut().get_ports())
        except Exception:
            return False

    def reconnect(self, why):
        self.log(f"MiniLab {why}: reopening ports, handshake, repaint")
        with self.lock:
            self.close_ports()
            self.open_ports()
            self._last_display = None
            self.start()

    # ---------- helpers
    @staticmethod
    def _color(c):
        return P.COLORS[c] if isinstance(c, str) else tuple(c)

    @staticmethod
    def _key(m):
        return (m["type"], int(m.get("channel", 1)), int(m["number"]))

    def log(self, line):
        line = f"{now()}  {line}"
        print(line, flush=True)
        self.logf.write(line + "\n")
        self.logf.flush()

    def _open(self, port, name, required=True):
        names = port.get_ports()
        for i, p in enumerate(names):
            if p.lower().startswith(name.lower()):
                port.open_port(i)
                self.log(f"opened [{p}]")
                return port
        if required:
            sys.exit(f"Port '{name}' not found.\nAvailable: {names}\n"
                     f"(Virtual port missing? Create it in loopMIDI first - see README.)")
        self.log(f"port '{name}' not found - skipped")
        return None

    def _listen(self, name, callback, sysex):
        if not name:
            return
        mi = self._open(rtmidi.MidiIn(), name, required=False)
        if mi is None:
            return
        mi.ignore_types(sysex=not sysex, timing=True, active_sense=True)
        mi.set_callback(callback)
        self.inputs.append(mi)

    @staticmethod
    def _parse(msg):
        st = msg[0] & 0xF0
        ch = (msg[0] & 0x0F) + 1
        if st == 0x90 and len(msg) >= 3:
            return ("note", ch, msg[1]), msg[2]
        if st == 0x80 and len(msg) >= 3:
            return ("note", ch, msg[1]), 0
        if st == 0xB0 and len(msg) >= 3:
            return ("cc", ch, msg[1]), msg[2]
        return None, None

    def send(self, msg, why=""):
        self.out.send_message(list(msg))
        if self.verbose:
            self.log(f"OUT  {P.hexs(msg)}  {why}")

    def paint(self, name, why=""):
        spec = self.leds[name]
        on = self.state[name]
        if spec.get("invert_of"):
            on = not self.state.get(spec["invert_of"], False)
        color = spec["_on"] if on else spec["_off"]
        if not on and spec.get("meter") and self.sounding[spec["meter"] - 1]:
            color = spec["_meter_color"]
            if self.cfg.get("meter_mode", "sounding") == "level":
                steps = self.cfg.get("meter_steps", 6)
                k = self.meter_step[spec["meter"] - 1] / steps
                color = tuple(max(1 if c else 0, round(c * k)) for c in color)
        if on:
            for other, c in spec["_on_while"].items():
                if self.state.get(other):
                    color = c
                    break
        self.send(P.led_message(spec["_id"], color),
                  f"{name}={'ON' if on else 'off'} {why}")

    def set_state(self, name, value, why):
        changed = self.state[name] != value
        self.state[name] = value
        self.paint(name, why)
        if changed:
            for other, spec in self.leds.items():          # derived LEDs (Stop = not Play, armed+rec)
                if spec.get("invert_of") == name or name in spec["_on_while"]:
                    self.paint(other, "(derived)")
            self.schedule_display()

    # ---------- inputs
    def on_feedback(self, event, _data=None):
        msg, _ = event
        key, val = self._parse(msg)
        with self.lock:
            name = self.by_feedback.get(key)
            if name is None:
                self.log(f"FB   {P.hexs(msg):<12s} (not mapped to a LED)")
                return
            self.log(f"FB   {P.hexs(msg):<12s} {name} -> {'ON' if val >= 64 else 'off'}")
            t = self.pending.pop(name, None)
            if t:
                t.cancel()
            self.set_state(name, val >= 64, "(feedback)")

    def on_mcu(self, event, _data=None):
        msg, _ = event
        with self.lock:
            what = self.mcu.feed(msg)
            if what and what.startswith("meter"):
                if what != "meter":
                    i = int(what[5:]) - 1
                    if self.mcu.meter[i] >= self.cfg.get("meter_threshold", 1):
                        self.meter_seen[i] = time.time()
                    self.update_meters()
                return
            if what == "timecode":
                tc = self.mcu.timecode()
                if tc != getattr(self, "_last_tc", None):
                    self._last_tc = tc
                    if self.verbose:
                        self.log(f"TC   |{tc}|")
                return
            if self.verbose or what is None:
                self.log(f"MCU< {P.hexs(msg[:24])}{' ...' if len(msg) > 24 else ''}  {what or '(unknown)'}")
            if what is None:
                return
            if what in self.by_mcu:
                name = self.by_mcu[what]
                val = self.mcu.leds.get(what, False)
                self.log(f"MCU  {what} -> {'ON' if val else 'off'}")
                self.set_state(name, val, "(mcu)")
            elif self.verbose and what != "lcd":
                self.log(f"MCU  {what}")
            if what == "lcd" or what.startswith("select"):
                self._update_track_from_mcu()

    def update_meters(self):
        """A strip 'sounds' while its meter was above the threshold within meter_hold_ms.
        Repaint only when that changes (meters arrive ~10x a second per strip)."""
        hold = self.cfg.get("meter_hold_ms", 400) / 1000.0
        level_mode = self.cfg.get("meter_mode", "sounding") == "level"
        steps = self.cfg.get("meter_steps", 6)
        t = time.time()
        for i in range(8):
            s = t - self.meter_seen[i] < hold
            # level 1..12 -> step 1..steps (0 when silent)
            step = max(1, -(-self.mcu.meter[i] * steps // 12)) if s else 0
            changed = s != self.sounding[i] or (level_mode and step != self.meter_step[i])
            self.sounding[i] = s
            self.meter_step[i] = step
            if changed:
                for name, spec in self.leds.items():
                    if spec.get("meter") == i + 1:
                        self.paint(name, f"(meter {step if level_mode else ('on' if s else 'off')})")

    def _update_track_from_mcu(self):
        sel = self.mcu.selected()
        row1 = "".join(self.mcu.lcd[:56])
        if row1 != self._last_lcd_logged:
            self._last_lcd_logged = row1
            self.log(f"LCD  |{row1}|")
        if sel is None:
            return                                      # selected track outside the MCU bank: keep last
        name = self.mcu.strip_name(sel) or f"Track {sel + 1}"
        if name != self.track_name or sel != self.track_index:
            self.track_name, self.track_index = name, sel
            self.log(f"TRK  strip {sel + 1}: '{name}'")
            self.schedule_display()

    def on_controller(self, event, _data=None):
        msg, _ = event
        if msg and msg[0] == 0xF0:
            if tuple(msg[:10]) == P.CONNECT_ACK:
                self.ack.set()
                self.log(f"CTL  {P.hexs(msg)}  (handshake acknowledged)")
            elif self.verbose:
                self.log(f"CTL  {P.hexs(msg)}  (MiniLab SysEx)")
            return
        key, val = self._parse(msg)
        if key == ("cc", 1, self.cfg.get("shift_cc", 27)) and val == 0:
            # the firmware draws its own screen while Shift is held ("DAW"): draw ours again
            def redraw():
                with self.lock:
                    self._last_display = None
                    self.update_display()
            threading.Timer(self.cfg.get("redraw_after_shift_ms", 150) / 1000.0, redraw).start()
        cmd = self.commands.get(key)
        if cmd and self.ctl:
            self.run_command(cmd, val)
            return
        name = self.by_press.get(key)
        if self.verbose:
            self.log(f"CTL  {P.hexs(msg)}  {name or ''}")
        if name is None:
            return
        spec = self.leds[name]
        if spec.get("optimistic") == "toggle" and key[0] in ("note", "cc") and val > 0                 and msg[0] & 0xF0 != 0x80:
            # MorningDAW toggles at once, but its mixer feedback comes ~0.4 s later:
            # show the result now; feedback confirms it, or the old state comes back
            with self.lock:
                before = self.state[name]
                old_t = self.pending.pop(name, None)
                if old_t:
                    old_t.cancel()
                    before = not before          # a press while one is pending: keep the true origin
                self.set_state(name, not self.state[name], "(press, optimistic)")

                def revert(name=name, before=before):
                    with self.lock:
                        if self.pending.pop(name, None) is not None:
                            self.log(f"no feedback for {name}: back to {'ON' if before else 'off'}")
                            self.set_state(name, before, "(revert)")
                t = threading.Timer(self.cfg.get("optimistic_timeout_ms", 1500) / 1000.0, revert)
                self.pending[name] = t
                t.start()
        delay = self.cfg.get("repaint_after_press_ms", 60) / 1000.0

        def later():
            with self.lock:
                self.paint(name, "(repaint after press)")
        threading.Timer(delay, later).start()
        threading.Timer(delay * 4, later).start()      # once more, after the firmware's own fade

    def run_command(self, cmd, val):
        """Relative encoder step(s) -> one note per step on the control port."""
        if cmd.get("relative") == "offset":
            steps = val - 64
        elif cmd.get("relative") == "twos":
            steps = val - 128 if val >= 64 else val
        else:
            steps = 1 if val >= 64 else 0
        if steps == 0:
            return
        target = cmd["up"] if steps > 0 else cmd["down"]
        n = min(abs(steps), cmd.get("max_steps", 4))
        ch = (target["channel"] - 1) & 0x0F
        if "cc" in target:                                # button as CC: 127 press, 0 release
            press, release = [0xB0 | ch, target["cc"], 127], [0xB0 | ch, target["cc"], 0]
        else:
            press = [0x90 | ch, target["note"], 127]
            release = [0x90 | ch, target["note"], 0] if cmd.get("release") == "note_on_0"                 else [0x80 | ch, target["note"], 0]      # Mackie buttons release with Note On 0
        for _ in range(n):
            self.ctl.send_message(press)
            self.ctl.send_message(release)
        if self.verbose:
            self.log(f"CMD  {cmd['name']}: {'next' if steps > 0 else 'previous'} x{n}")

    # ---------- OLED
    def schedule_display(self):
        d = self.cfg.get("display")
        if not d or not d.get("enabled"):
            return
        if self._display_timer:                          # LCD text arrives in pieces: debounce
            self._display_timer.cancel()
        self._display_timer = threading.Timer(d.get("debounce_ms", 60) / 1000.0, self.update_display)
        self._display_timer.start()

    def update_display(self):
        d = self.cfg["display"]
        with self.lock:
            s = self.state
            playing, recording = s.get("play", False), s.get("record", False)
            line1 = self.track_name or d.get("line1", "MorningDAW")
            words = ["Rec" if recording else ("Play" if playing else "Stop")]
            if s.get("loop"):
                words.append("Loop")
            if s.get("mute"):
                words.append("M")
            if s.get("solo"):
                words.append("S")
            if self.track_index is not None:
                words.insert(0, f"{self.track_index + 1}:")
            line2 = " ".join(words)
            picto = "record" if recording else ("play" if playing else None)
            armed = any(v for k, v in s.items() if k.startswith("arm"))
            key = (line1, line2, picto, armed)
            if key == self._last_display:
                return
            self._last_display = key
            self.send(P.display_message(line1, line2, picto, arm=armed),
                      f"(display '{line1}' / '{line2}')")

    # ---------- life cycle
    def handshake(self):
        """Send Ableton's connection message until the MiniLab answers (it ignores it while booting),
        then give its firmware time to draw its own 'connected' screen before we draw ours."""
        tries = self.cfg.get("connect_retries", 15)
        for i in range(tries):
            self.ack.clear()
            self.send(P.CONNECT, f"(connect, try {i + 1})")
            if self.ack.wait(1.0):
                time.sleep(self.cfg.get("after_connect_ms", 800) / 1000.0)
                return True
        self.log(f"no handshake answer after {tries} tries - LEDs work anyway, the OLED will not")
        return False

    def start(self):
        if self.cfg.get("send_connect"):
            self.handshake()
        for name in self.leds:
            self.paint(name, "(initial)")
        self._last_display = None
        if self.cfg.get("display", {}).get("enabled"):
            self.update_display()
        self.log("running - in MorningDAW press 'Send all again' on the profile to sync. Ctrl+C stops.")

    def stop(self):
        if self._display_timer:
            self._display_timer.cancel()
        if self.cfg.get("send_connect") and self.cfg.get("send_disconnect_on_exit", True):
            self.send(P.DISCONNECT, "(disconnect)")
        self.close_ports()
        self.log("stopped")


def wait_for_ports(cfg):
    need_out = [cfg["minilab_out"]]
    need_in = [n for n in (cfg.get("feedback_in"), cfg.get("mcu_in"), cfg.get("minilab_in")) if n]
    while True:
        try:
            outs = [p.lower() for p in rtmidi.MidiOut().get_ports()]
            ins = [p.lower() for p in rtmidi.MidiIn().get_ports()]
        except Exception:
            outs, ins = [], []
        if all(any(p.startswith(n.lower()) for p in outs) for n in need_out) and            all(any(p.startswith(n.lower()) for p in ins) for n in need_in):
            return
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "bridge_config.json"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--wait", action="store_true",
                    help="wait until the MiniLab and the loopMIDI ports exist (for autostart)")
    a = ap.parse_args()
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    if a.wait:
        wait_for_ports(cfg)
    b = Bridge(cfg, a.verbose)
    b.start()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    refresh = cfg.get("refresh_seconds", 0)
    last = time.time()
    present, last_check = True, 0.0
    while not stop.is_set():
        stop.wait(0.2)
        with b.lock:
            b.update_meters()
        if time.time() - last_check > 2:                 # MiniLab unplugged / power-cycled?
            last_check = time.time()
            now_present = b.minilab_present()
            if present and not now_present:
                b.log("MiniLab disappeared - waiting for it")
            elif now_present and not present:
                stop.wait(cfg.get("reconnect_delay_ms", 1500) / 1000.0)   # let it boot
                b.reconnect("is back")
            present = now_present
        if refresh and time.time() - last > refresh:
            with b.lock:
                for name in b.leds:
                    b.paint(name, "(refresh)")
            last = time.time()
    b.stop()


if __name__ == "__main__":
    main()
