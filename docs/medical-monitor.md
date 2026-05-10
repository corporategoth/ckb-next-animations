# Medical Monitor

Turns the keyboard into a hospital bedside monitor. Different zones of
the keyboard show different vitals, and a defibrillator state machine
reacts to whether you're typing or idle.

## Install

```sh
mkdir -p ~/.local/share/ckb-next/animations
cp medical-monitor ~/.local/share/ckb-next/animations/
chmod +x ~/.local/share/ckb-next/animations/medical-monitor
```

In the ckb-next GUI: **Settings → Animation Scripts → Re-scan**, then
add **Medical Monitor** to a mode. In **Properties → Settings →
Playback**, enable **both**:

- **"Start with mode"** — runs the animation while the mode is active.
- **"Start with key press"** with **"on pressed key"** selected —
  delivers per-key events to the script so it can react to your typing
  (per-key red flashes, the STRESSED state, and aborting a defib
  charge).

## Features

### Zoned vitals display

- **Main keyboard** (caps → enter) — **ECG sinus rhythm** trace
- **Numpad** — **Pulse-ox plethysmograph**
- **Nav cluster** (prtscn → pgdn) — **Respiratory rate** (slow breathing)
- **Media keys** (stop / prev / play / next) — **Defibrillator charge bar**

### State machine driven by typing

| State | What you see | How you get here |
| --- | --- | --- |
| **NORMAL** | Sinus rhythm, pulse-ox, slow breathing | Default; fall back here after recovery |
| **STRESSED** | ECG goes vfib (random forward path), breathing speeds up, every keypress flashes its key red | Typing while in NORMAL |
| **CHARGING** | ECG flatlines, pulse-ox replaced by static X, breathing stops (apnea), media keys progressively light over `defib_charge` seconds | `defib_idle` seconds idle |
| **FLATLINE → FLASH/GAP → RECOVERY** | FLASH1 (0.15s) → GAP1 (1.0s) → FLASH2 (0.15s) → GAP2 (0.2s) → FLASH3 (0.15s), whole keyboard red | Defib finishes charging |

CHARGING aborts and snaps back to NORMAL if you start typing again.

## Tunable parameters (in the ckb-next GUI)

| Param | Default | Notes |
| --- | --- | --- |
| `bpm` | 60 | Normal heart rate |
| `vfib_bpm` | 150 | Heart rate during vfib/STRESSED |
| `tracecolor` | white | Color of all waveforms |
| `flashcolor` | red | Discharge flash color |
| `stressed` | on | Bool — enable STRESSED state on typing |
| `defib_idle` | 2.0s | Seconds idle before defib charge starts; **0 disables defib entirely** |
| `defib_charge` | 8.0s | Seconds to charge across all 4 media keys |
| `breathing_normal` | 4.0s | Breath cycle in NORMAL |
| `breathing_stressed` | 1.5s | Breath cycle in STRESSED |
