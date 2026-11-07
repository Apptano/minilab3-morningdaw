# MiniLab 3 ↔ MorningDAW bridge

Makes the **Arturia MiniLab 3** pad lights and OLED show **MorningDAW's real state**, not the
state the controller remembers locally. Play/Stop/Record/Loop, track arm, level meters on the pads,
the selected track's name on the OLED, and Shift + main encoder to select tracks.
Changes made from the pads, the computer keyboard or the GUI all show up the same way.

MorningDAW 1.6.6 has no native MiniLab 3 driver, and its `.mdmidimap` profiles cannot send the
Arturia SysEx the MiniLab needs for lights and text. This small Python bridge translates what
MorningDAW *does* send (profile feedback + its built-in Mackie Control driver) into MiniLab 3 SysEx.

➡️ **[Feature request / protocol spec for a native driver](MorningDAW_MiniLab3_native_support.md)**: MiniLab 3 protocol, Ableton reference behaviour, and the MorningDAW issues found.

## How it works
```
MiniLab 3 (DAW program) ──► MorningDAW profile "MiniLab3 DAW Feedback"
MorningDAW profile feedback ──► loopMIDI "MorningDAW Feedback" ─┐
MorningDAW Mackie Control   ──► loopMIDI "MorningDAW MCU"      ─┼─► bridge ──SysEx──► MiniLab 3 (LEDs, OLED)
MiniLab 3 (presses, Shift+encoder) ─────────────────────────────┘
bridge ──CC 112/113──► loopMIDI "MorningDAW Ctl" ──► MorningDAW Keyboard controls (prev/next track)
```

## Layout
| Control | Action | Light (state source) |
|---|---|---|
| Play / Stop / Rec | transport | green / white / red, bright = on (profile feedback) |
| Loop | loop | amber (Mackie Cycle LED) |
| Pads bank A 1–8 | arm tracks 1–8 of the bank | off · green brightness = level (Mackie meters) · dark red = armed · red = armed + recording |
| Pads bank B | play notes (not captured) | dim white |
| Main encoder / Shift + encoder | jog / previous–next track | – |
| Knobs 1–8, faders 1 & 4 | bank volumes; selected track volume & pan | – |
| OLED | selected track name (Mackie LCD, 7 chars), number, Play/Stop/Rec, Loop, pictograms | – |

## Requirements
- Windows, Python 3.10+, `pip install -r requirements.txt`
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) with 3 ports: `MorningDAW Feedback`, `MorningDAW MCU`, `MorningDAW Ctl`
- MorningDAW 1.6.6, MiniLab 3 in its **DAW** program

## Setup
1. **MIDI Mappings:** import `MiniLab3 DAW Feedback.mdmidimap`, then set Output = `MorningDAW Feedback` and turn on *Send values back*. Turn off other MiniLab profiles.
2. **Controllers → Mackie Control** (exactly one): In = `Minilab3 MCU/HUI`, Out = `MorningDAW MCU`, LCD on.
   ⚠️ Never In = Out on a loopMIDI port: loopMIDI echoes, and arming a track un-arms it instantly.
3. **Controllers → Keyboard controls:** In = `MorningDAW Ctl`. The previous/next track fields are **not in the UI**:
   with MorningDAW **closed**, edit `%APPDATA%\Morningdew Media\Morningdew MorningDAW\Settings\pref_controllers.txt`.
   In the `driver=keys` entry set `out=` (empty), `prev=112`, `next=113`. Make a backup first.
4. Start the bridge **before** MorningDAW: `start_bridge.vbs` (background) or `python minilab3_bridge.py --verbose`.

## Good to know
- MorningDAW sends its full state only when it starts (or after **Send all again** in the profile's Output section).
  If the bridge starts later, click Send all again.
- Unplugging and replugging the MiniLab is handled: the bridge redoes the handshake and repaints.
- After adding or removing loopMIDI ports, **restart MorningDAW**: it keeps sending to the old port handles.
- Run only one MorningDAW instance.
- Settings in `bridge_config.json`: colours (names or `[r,g,b]` 0–127), `meter_mode` (`level` / `sounding`), timings.
- `minilab3_diag.py` is a MIDI monitor and test sender. It only sends documented messages.

## Files
| File | |
|---|---|
| `minilab3_bridge.py`, `bridge_config.json` | the bridge |
| `minilab3_protocol.py` | MiniLab 3 SysEx: LEDs, OLED, handshake |
| `mcu_decoder.py` | decodes MorningDAW's Mackie output (LCD, LEDs, meters, timecode) |
| `MiniLab3 DAW Feedback.mdmidimap` | MorningDAW profile |
| `minilab3_diag.py` | diagnostics |
| `start_bridge.vbs`, `stop_bridge.bat` | run in background / stop |

## Credits
Protocol constants were read from Ableton Live 12's bundled `MiniLab_3` remote script and cross-checked with
[Arturia's MiniLab 3 FL Studio scripts](https://github.com/danicc097/fl-studio-minilab-mk-iii-scripts) and
[osc-bridge](https://github.com/roomi-fields/osc-bridge), then verified on hardware. Not affiliated with Arturia, Ableton or Morningdew Media.
