# Feature request: native Arturia MiniLab 3 support in MorningDAW

**MorningDAW version tested:** 1.6.6 (build 3602337), Windows 11 (Windows MIDI Services active)
**Controller:** Arturia MiniLab 3, DAW program
**Date:** 2026-11-07

## TL;DR

The MiniLab 3's pad colours and OLED screen are driven by documented-in-practice Arturia SysEx
(the same messages Ableton Live 12's bundled `MiniLab_3` remote script sends). I built a working
**external bridge** (Python, ~600 lines, attached) that gets close to Ableton-level integration using
what MorningDAW already outputs: a `.mdmidimap` profile with "Send values back" plus the built-in
Mackie Control driver, both routed to loopMIDI ports.

It works: transport, loop, arm, track names on the OLED and level meters on the pads all follow
MorningDAW's real state. But it needs 3 virtual ports, 3 controller entries, a hand-edited
preferences file and a background process. A native driver, like the existing Komplete Kontrol /
Launchkey MK4 / Maschine Jam / Osmose ones, would replace all of that. This document gives
everything needed to write one: the protocol bytes, Ableton's behaviour as a reference, and the
MorningDAW 1.6.6 issues found along the way, with evidence.

---

## 1. MiniLab 3 protocol (host → controller)

All messages: `F0 00 20 6B 7F 42 <payload> F7` (Arturia ID `00 20 6B`, device `7F`, model `42`).
✅ = verified on hardware during this work. 📄 = taken from Ableton's script / public scripts, not separately verified.

### 1.1 DAW connection handshake ✅
| | Bytes |
|---|---|
| Connect (as sent by Ableton) | `F0 00 20 6B 7F 42 02 00 40 6A 21 F7` |
| Disconnect | `F0 00 20 6B 7F 42 02 00 40 6A 20 F7` |
| MiniLab's answer to Connect | `F0 00 20 6B 7F 42 02 00 40 63 00 F7` |

Behaviour observed:
- **LED colours work without the handshake. OLED text does NOT**: it is ignored until Connect is acknowledged.
- While the controller is still booting (just plugged in) Connect is ignored and gets no answer. Retry about once a second until the `…40 63 00` answer arrives.
- After the answer, the firmware draws its own screen. Text sent sooner than about **800 ms** after the answer is overwritten. 170 ms was too early, 560 ms and 800 ms worked.
- The handshake is lost when the controller is power-cycled, so it has to be repeated when the port reappears.
- While **Shift** is held (CC 27) the firmware shows its own screen ("DAW"). Redraw your text after Shift is released.
- 📄 Arturia's FL Studio script uses `02 02 40 6A 21` / `…20`. The Ableton variant above is the one verified here.

### 1.2 Pad / button colour ✅
`F0 00 20 6B 7F 42 02 02 16 <led_id> <R> <G> <B> F7`, with R, G and B in **0–127**.

| Element (DAW program) | MIDI it sends | `led_id` |
|---|---|---|
| Pads bank A 1–8 | Note On ch 10, notes 36–43 ✅ | `0x34`–`0x3B` ✅ |
| Pads bank B 1–8 | Note On ch 10, notes 44–51 ✅ | `0x44`–`0x4B` ✅ |
| Loop | CC 105 ch 1 | `0x57` ✅ |
| Stop | CC 106 ch 1 | `0x58` ✅ |
| Play | CC 107 ch 1 ✅ | `0x59` ✅ |
| Record | CC 108 ch 1 ✅ | `0x5A` ✅ |
| Tap | CC 109 ch 1 | `0x5B` ✅ |

The transport buttons send 127 on press and 0 on release (momentary). The colour stays exactly as set
by the host. After a press the firmware may briefly draw its own highlight, so re-sending the state
colour about 60 ms after a press or release keeps it correct.

📄 osc-bridge (roomi-fields) documents the byte after `02` as a mode: `00` user, `01` arturia, `02` daw.
That would mean pads can also be coloured in the User/Arturia programs. Not tested here.

### 1.3 OLED text ✅
Full-screen, two lines, non-transient (what Ableton uses for track or scene names):
```
F0 00 20 6B 7F 42 04 02 60
   1F 07 01 <picto_a> <picto_b> 00 00      options: full screen, non-transient, pictograms
   01 <line 1 ASCII, max 10 chars> 00
   02 <line 2 ASCII, max 18 chars> 00
F7
```
Pictogram bytes ✅: `00` none, `02` play, `03` record (position a), `04` arm (position b). `01` arp (📄, position c).

📄 Other screen types (Arturia FL Studio script), useful for a "parameter touched" display:
`1F 03 01 <value 0–127> 00 00` encoder screen with value bar; `1F 04 01 <value> 00 00` fader screen;
`1F 02 01 00` two-line text; `1F 05 01 00 00 00` scroll.

### 1.4 Controls in the DAW program
| Control | Message |
|---|---|
| Shift | CC 27 (127/0) ✅ |
| Main encoder | CC 28, relative, 64 ± n ✅ |
| Shift + main encoder | CC 29, relative ✅ (clockwise 65, counter-clockwise 62/63) |
| Encoder push / Shift+push | CC 118 / 119 📄 |
| Knobs 1–8 | CC 86, 87, 89, 90, 110, 111, 116, 117 📄 |
| Faders 1–4 | CC 14, 15, 30, 31 📄 (Ableton: volume, send A, send B, pan of the selected track) |

---

## 2. Reference behaviour: Ableton Live 12 `MiniLab_3` script

- **Bank A pads = Session clip launcher** for the 8 tracks of the session ring, at the selected scene.
  **Bank B pads = Drum Rack** (play notes).
- **Shift + main encoder** selects the previous/next track. Main encoder scrolls scenes, push launches the scene.
- On connect the script blacks out all pads, then paints them from the current state.
- Colours (`colors.py`, RGB 0–127):

| State | Colour |
|---|---|
| Empty slot | off (0,0,0) |
| Empty slot on an armed track | (8,0,0) dark red |
| Clip stopped | the clip's colour |
| Clip triggered to play / playing | (0,20,0) / (0,127,0) |
| Clip triggered to record / recording | (20,0,0) / (127,0,0) |
| Play on / off | (0,127,0) / (0,20,0) |
| Stop on / off | (127,127,127) / (20,20,20) |
| Loop on / off | (127,50,0) / (20,5,0) |
| Record on / off | (127,0,0) / (20,0,0) |

## 3. Suggested native driver (what would replace the bridge)

1. Recognise the MiniLab by its port name (`Minilab3 MIDI` on Windows; other OS names not checked) and offer it like the other known controllers.
2. Send Connect, wait for the answer (retry while it boots), and redo it when the port comes back.
3. On connect, send the full state: transport LEDs, pad colours, and the OLED text about 800 ms after the answer.
4. **Pads bank A = Clip Stage row** (8 tracks × selected scene), using the Ableton colour logic above and the tracks' colours,
   as MorningDAW already does for the Maschine Jam. Bank B plays the selected instrument.
5. Transport buttons with their lights (including **Loop**). Tap = tap tempo.
6. **Shift + encoder = select previous/next track**, encoder = jog or scene, knobs = device controls / bank volumes, faders = selected track volume/sends/pan.
7. OLED: selected track's full name (10 + 18 chars), play/record/arm pictograms, parameter screen while a knob moves.
8. Send changes immediately. Section 4.2 shows the MCU driver already reacts in about 7 ms.

## 4. Issues found in MorningDAW 1.6.6 (with evidence)

These are independent of the MiniLab and affect any controller set up through profiles.

### 4.1 Profile feedback never sends `transport.loop` on change
A profile mapping `{"in":"cc","number":105,"target":"transport.loop"}` with feedback on sends Play
(CC 107) and Record (CC 108) on every change, but **nothing for Loop**, whether toggled from the
controller or the GUI. In the "Send all again" dump CC 105 is sent as `127` **while loop is off**
(the MCU Cycle LED reports off at the same moment), so it does not reflect the loop state.
```
18:04:52.058  MCU  cycle -> off          (Mackie Cycle LED, correct)
18:04:54.098  FB   B0 69 7F               (profile, CC105 = 127 although loop is off)
```

### 4.2 Profile feedback for mixer targets is about 400 ms late
Pressing a pad mapped to `mixer.arm`: the track arms instantly, and the Mackie driver reports it 7 ms later,
but the profile's feedback arrives 350–440 ms later, so the controller light visibly lags.
```
16:46:31.369  pad press (99 24 14)
16:46:31.376  MCU rec1 LED            (+7 ms)
16:46:31.809  profile FB 99 24 7F     (+440 ms)
```

### 4.3 No way to ask for the full state over MIDI
The full state goes out only when MorningDAW starts or the controller connects, or after a click on
**Send all again**. A controller (or bridge) that starts later sees everything dark until the
next change. Suggestion: resend everything on a learnable command, or on the device's own
handshake/hello (as with the Osmose).

### 4.4 `.mdmidimap` cannot send SysEx
Feedback is the mapped message echoed back (note/CC 127/0). Many controllers (MiniLab 3, KeyLab,
Launchkey, APC…) need SysEx or a different message for their lights. An optional per-mapping
`"feedback": {...}` template (e.g. SysEx bytes with `{value}` / `{r}{g}{b}` placeholders) would
cover a lot of controllers without native drivers.

### 4.5 No profile target to select the previous/next track
`cmd:mixer.track_next` / `track_prev` only work in the Mixer view ("Mixer: select the strip to the right").
`bank.track_next` / `bank.track_prev` scroll the bank but do not change the selection.
The **Keyboard controls** driver has working `prev=` / `next=` fields in `pref_controllers.txt`.
Setting `prev=112 next=113` by hand (MorningDAW closed) makes CC 112/113 select tracks in any view.
These fields are **not shown in the UI** for a generic keyboard. Please expose them in the UI and
add `track.select_next` / `track.select_prev` as profile targets.

### 4.6 The Mackie driver ignores input from virtual (loopMIDI) ports
With Mackie Control In = a loopMIDI port, MorningDAW takes the port (it becomes greyed out in MIDI
inputs and its "messages received" counter counts), but **ignores every message**: SELECT
(`90 18..1F`), Cursor Up/Down (`90 60/61`). The controller does not flash. Tried with a separate
port and with an `X In` / `X Out` pair. The same driver reacts to its hardware port, and to a
loopMIDI port when In = Out (see 4.8). The Keyboard controls driver does accept loopMIDI input.

### 4.7 Ports added or removed in loopMIDI break running outputs
After adding or removing a loopMIDI port while MorningDAW runs (Windows MIDI Services renumbers the
ports), MorningDAW keeps sending profile feedback and Mackie output to stale handles: nothing arrives
until MorningDAW is restarted. Re-opening outputs when the port list changes would fix it.

### 4.8 Auto-detection can create a feedback loop
A Mackie Control entry with In = Out = the same loopMIDI port (loopMIDI echoes everything back)
appeared during this work, probably from the "new controller" offer when ports appeared. Its own LED
messages came back as button presses, so arming a track un-armed it instantly
(`90 00 7F` LED → read as REC 1 press). Suggestion: never offer or accept In = Out on a virtual port,
or ignore input that equals what was just sent.

### 4.9 Minor
- The Mackie LCD limits track names to 7 characters. Fine for Mackie, but a native driver could show full names.
- `pref_controllers.txt` stores `play=/stop=/rec=` for Mackie entries too (54/51/50 from a KeyStep default). Harmless, but confusing when editing.

## 5. Reference implementation (attached)

| File | Purpose |
|---|---|
| `minilab3_protocol.py` | the MiniLab 3 messages above |
| `minilab3_bridge.py`, `bridge_config.json` | bridge: profile feedback + Mackie output → MiniLab SysEx; Shift+encoder → Keyboard controls prev/next |
| `mcu_decoder.py` | decoder for what MorningDAW sends to a Mackie unit (LCD, LEDs, meters, timecode) |
| `MiniLab3 DAW Feedback.mdmidimap` | the profile used |
| `minilab3_diag.py` | MIDI monitor / test sender |
| `README.md` | setup |

Data flow:
```
MiniLab 3 ──► MorningDAW profile "MiniLab3 DAW Feedback" ──feedback──► loopMIDI "MorningDAW Feedback" ─┐
              MorningDAW Mackie Control (out) ───────────────────────► loopMIDI "MorningDAW MCU"      ─┼─► bridge ──SysEx──► MiniLab 3
              MorningDAW Keyboard controls (in, prev/next) ◄─ CC112/113 ◄─ loopMIDI "MorningDAW Ctl" ◄─┘
```

Sources for the protocol: Ableton Live 12's bundled `MiniLab_3` remote script (constants only),
Arturia's MiniLab 3 FL Studio scripts (public, e.g. github.com/danicc097/fl-studio-minilab-mk-iii-scripts),
and the osc-bridge project (github.com/roomi-fields/osc-bridge). Every ✅ item was confirmed with a
MiniLab 3 and MorningDAW 1.6.6.

Thank you for MorningDAW. The profile/feedback system and the Mackie driver made this possible at all,
and a native MiniLab 3 driver would make one of the most popular small controllers work out of the box.
