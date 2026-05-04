# ckb-next-animations

Custom animations for [ckb-next](https://github.com/ckb-next/ckb-next), the
open-source Linux/macOS driver for Corsair keyboards.

Tested on a **Corsair K70**. Other ckb-next-supported keyboards should work
but key layouts (especially the numpad and media-key clusters) may differ
and visuals may not line up exactly.

Both animations are written in pure Python 3 with no external dependencies
and speak the ckb-next animation plugin protocol directly over stdin/stdout.

## Authorship

Designed and refined by **Preston A. Elder** &lt;prez@neuromancy.net&gt;.
Code authored by **Claude AI** under Preston's direction.

## Installation

Drop the animation files into your user-local ckb-next animations
directory and make them executable:

```sh
mkdir -p ~/.local/share/ckb-next/animations
cp brickbreaker medical-monitor ~/.local/share/ckb-next/animations/
chmod +x ~/.local/share/ckb-next/animations/{brickbreaker,medical-monitor}
```

Then in the ckb-next GUI, go to **Settings → Animation Scripts** and click
**Re-scan**. The animations will appear in the **Lighting** tab's animation
picker as **Brick Breaker** and **Medical Monitor**. No daemon or GUI
restart is required.

For a system-wide install instead, copy the files to
`/usr/lib/ckb-next-animations/` (or wherever your distribution's
ckb-next package keeps them) and Re-scan as above.

### Required animation properties

After adding an animation to a mode, open its **Properties → Settings**
and configure the **Playback** section:

- **Both animations** need **"Start with mode"** enabled — this is what
  causes the animation to start running when the mode becomes active.
- **Medical Monitor** additionally needs **"Start with key press"**
  enabled, with **"on pressed key"** selected — this is what delivers
  per-key events to the script so it can react to your typing
  (per-key red flashes, the STRESSED state, and aborting a defib charge).
  Brick Breaker doesn't read keypresses and doesn't need this.

---

## Brick Breaker

A self-playing brick-breaker game that uses your keyboard as the playfield.
Each game restart rolls a new "AI player" with randomized skill, so runs
range from comically bad (loses on level 1) to brilliant (clears all 36
levels).

### Features

- **Progressive levels.** 36 levels with increasing ball speed and a brick
  HP distribution that shifts toward harder bricks. Paddle speed stays
  fixed, so the AI naturally gets less accurate at high speeds.
- **Multi-hit bricks.** Color-coded by remaining HP — red (3), yellow (2),
  green (1).
- **Arkanoid-style paddle physics.** Ball deflection comes from both
  hit-position and paddle friction (the paddle's own motion drags the
  ball). Speed bleeds back to base over ~3 seconds so boosts don't
  accumulate.
- **AI strategy.** Uses a setup-and-glide technique — positions the paddle
  to the opposite side of the desired deflection, then slides through
  the impact point at impact time to impart velocity.
- **Randomized AI skill per game.** Beta(3,3)-distributed skill drives
  paddle speed, prediction error, and aim aggressiveness. Most runs are
  average; very-bad and very-good AIs are rare.
- **Power-up drops** (rate-limited, ~one per 20–30s):
  - LONGER (green) — +1 paddle key
  - SHORTER (red) — −1 paddle key (AI tries to avoid)
  - MULTIBALL (cyan) — +2 balls (cap 3)
  - EXTRA_LIFE (magenta) — +1 life (cap 4)
- **Numpad level counter.** Tiered display:
  - 1–9: num1..num9 cumulative in green
  - 10–18: numlock + num1..num9 in yellow
  - 19–27: numlock+numslash + num1..num9 in orange
  - 28–36: numlock+numslash+numstar + num1..num9 in red
  - 37+: saturated red pulse
- **Game flow.** Miss → red flash → life lost. 0 lives → red strobe →
  reset to level 1. All bricks cleared → rainbow strobe → next level.

### Tunable parameters (in the ckb-next GUI)

- `basespeed` — base ball speed at level 1, in keys/sec
  (default 60.0; range 20.0–200.0)
- `maxspeed` — cap on ball speed at level 1 after paddle-friction boosts
  (default 110.0; range 30.0–300.0)
- `paddlespeed` — paddle movement speed, fixed across all levels
  (default 110.0; range 20.0–300.0)
- `paddlewidth` — starting paddle width in keys, before power-ups
  (default 4; range 2–8)
- `levelspeed` — fractional speed boost added per level beyond 1
  (default 0.12; range 0.0–0.5; e.g. 0.12 = +12% per level)
- `chaos` — randomness applied to ball deflection
  (default 0.20; range 0.0–1.0)
- `aimstrength` — how aggressively the AI tries to aim its returns
  (default 0.50; range 0.0–1.0; multiplied per-game by the rolled
  AI skill)
- `dropchance` — probability a destroyed brick drops a power-up
  (default 0.5; range 0.0–1.0; **0 disables drops entirely**)
- `dropcooldown` — minimum seconds between power-up drops
  (default 25.0; range 5.0–120.0)
- `paddlefriction` — how much of the paddle's motion is imparted to the
  ball on contact (default 0.6; range 0.0–1.0)
- `ballcolor` — ARGB color of the ball (default `ffffffff`, white)
- `paddlecolor` — ARGB color of the paddle (default `ff00ffff`, cyan)

---

## Medical Monitor

Turns the keyboard into a hospital bedside monitor. Different zones of the
keyboard show different vitals, and a defibrillator state machine reacts
to whether you're typing or idle.

### Features

- **Zoned vitals display:**
  - Main keyboard (caps → enter) — **ECG sinus rhythm** trace
  - Numpad — **Pulse-ox plethysmograph**
  - Nav cluster (prtscn → pgdn) — **Respiratory rate** (slow breathing)
  - Media keys (stop / prev / play / next) — **Defibrillator charge bar**
- **State machine driven by typing:**
  - **NORMAL** — sinus rhythm, pulse-ox, slow breathing.
  - **STRESSED** — typing triggers vfib (random forward path) on the ECG,
    breathing speeds up, and each keypress flashes its key red.
  - **CHARGING** — after `defib_idle` seconds idle, ECG flatlines, the
    pulse-ox is replaced by a static X on the numpad, breathing stops
    (apnea), and the media keys progressively light up over
    `defib_charge` seconds. Aborts if you start typing again.
  - **FLATLINE → FLASH/GAP → RECOVERY** — discharge sequence:
    FLASH1 (0.15s) → GAP1 (1.0s) → FLASH2 (0.15s) → GAP2 (0.2s) →
    FLASH3 (0.15s), whole keyboard red, then snap back to NORMAL.

### Tunable parameters (in the ckb-next GUI)

- `bpm` — normal heart rate (default 60)
- `vfib_bpm` — heart rate during vfib/STRESSED (default 150)
- `tracecolor` — color of all waveforms (default white)
- `flashcolor` — discharge flash color (default red)
- `stressed` — bool, enable STRESSED state on typing (default on)
- `defib_idle` — seconds idle before defib charge starts
  (default 2.0; **0 disables defib entirely**)
- `defib_charge` — seconds to charge across all 4 media keys (default 8.0)
- `breathing_normal` — breath cycle in NORMAL (default 4.0s)
- `breathing_stressed` — breath cycle in STRESSED (default 1.5s)

---

## Switching modes on screen lock (`ckb-mode-on-lock.service`)

Optional companion: a tiny user-level systemd service that listens for
the desktop session's lock/unlock signal on D-Bus and switches the
active ckb-next mode (and optionally profile). Use it to run a calmer
(or no) animation while you're away from the keyboard, and snap back
to the active mode on unlock.

**Cross-DE.** Listens on both `org.freedesktop.ScreenSaver.ActiveChanged`
(KDE Plasma, XFCE, Cinnamon, MATE, …) and
`org.gnome.ScreenSaver.ActiveChanged` (GNOME), so a single config covers
the common Linux desktops. No DE-specific shim needed.

### Files

- `ckb-mode-on-lock` — Python script. Watches D-Bus and shells out to
  `ckb-next --mode <name>` (and `--profile <name>` if configured) on
  each lock/unlock event.
- `ckb-mode-on-lock.service` — systemd **user** unit that runs the
  script and restarts it on failure.
- `lock.conf.example` — sample config to copy into
  `~/.config/ckb-next/lock.conf` (alongside ckb-next-gui's own
  `ckb-next.conf`).

### Configuration

Three layers, in priority order (highest first):

1. **Environment variables** (set under `[Service]` in the unit file).
   Useful for one-off overrides:

   - `CKB_LOCK_MODE_UNLOCKED` — mode name to switch to on unlock
   - `CKB_LOCK_MODE_LOCKED` — mode name to switch to on lock
   - `CKB_LOCK_PROFILE` — profile name (optional), or `none`

2. **`~/.config/ckb-next/lock.conf`** — recommended. Lives alongside
   ckb-next-gui's own config in the same directory. INI-ish
   `key = value` format, lines starting with `#` are comments:

   ```ini
   mode_unlocked = EKG
   mode_locked   = BrickBreaker
   profile       = none      # or a profile name
   ```

3. **Defaults** compiled into the top of the script
   (`MODE_UNLOCKED = "EKG"`, `MODE_LOCKED = "BrickBreaker"`,
   `PROFILE = None`) — edit them in place if you'd rather not maintain
   a separate file.

Values are passed verbatim to `ckb-next` as a `--mode <name>` /
`--profile <name>` argument, so they must match a mode/profile name
that exists in your ckb-next-gui setup. Set a value to `none` (or leave
it unset) to skip that switch.

### One-time setup

1. **Create the modes in the ckb-next GUI.** On your active profile,
   make at least two modes (e.g. one running Medical Monitor, one
   running Brick Breaker, or a calmer "locked" mode like a static
   colour). Take note of their names.

2. **Install the script, unit, and config** (no sudo — everything
   stays under `$HOME`):

   ```sh
   mkdir -p ~/.local/bin ~/.config/systemd/user
   cp ckb-mode-on-lock         ~/.local/bin/
   chmod +x                    ~/.local/bin/ckb-mode-on-lock
   cp ckb-mode-on-lock.service ~/.config/systemd/user/
   cp lock.conf.example        ~/.config/ckb-next/lock.conf
   $EDITOR                     ~/.config/ckb-next/lock.conf
   ```

3. **Enable and start:**

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now ckb-mode-on-lock.service
   ```

4. **Verify:**

   ```sh
   systemctl --user status ckb-mode-on-lock.service
   journalctl --user -u ckb-mode-on-lock.service -f
   ```

   Lock the screen — the keyboard should switch to the locked mode.
   Unlock — it switches back. Edit the config and
   `systemctl --user restart ckb-mode-on-lock.service` to apply
   changes.

### Stop / disable

```sh
systemctl --user disable --now ckb-mode-on-lock.service
```

### Requirements

- `python3`
- `dbus-monitor` (from the `dbus` / `dbus-tools` package — already on
  any system with a working desktop)
- `ckb-next` on `$PATH` and a running ckb-next instance (the GUI, or
  `ckb-next --background`) — that's how mode/profile switches are
  applied
- `ckb-next-daemon` running (the standard ckb-next install handles
  this)

### Testing without locking your screen

You can fire the same `ActiveChanged` signal your DE would emit, to
exercise the full path without actually locking:

```sh
# Fake "screen locked"
dbus-send --session --type=signal /org/freedesktop/ScreenSaver \
    org.freedesktop.ScreenSaver.ActiveChanged boolean:true

# Fake "screen unlocked"
dbus-send --session --type=signal /org/freedesktop/ScreenSaver \
    org.freedesktop.ScreenSaver.ActiveChanged boolean:false
```

Then check `journalctl --user -u ckb-mode-on-lock -n 10` for the
`Screen LOCKED` / `Screen UNLOCKED` log lines and watch the keyboard.

### Troubleshooting

- **Locking the screen doesn't change modes** (but the synthetic
  `dbus-send` test above does). Your DE isn't emitting
  `org.freedesktop.ScreenSaver.ActiveChanged`. Probe what it does
  emit:

  ```sh
  dbus-monitor --session "type='signal',interface='org.freedesktop.ScreenSaver'"
  ```

  Lock and unlock and see whether anything appears. If your DE only
  emits the system-level `org.freedesktop.login1.Session` `Lock` /
  `Unlock` signals, adapt `ckb-mode-on-lock` to monitor `--system` on
  that interface.

- **Service is running but `ckb-next --mode …` errors or no-ops.**
  Confirm the mode name matches what's in the ckb-next-gui mode list
  exactly (case-sensitive). Try the same command at a shell:

  ```sh
  ckb-next --mode "BrickBreaker"; echo "exit=$?"
  ```

  Exit 0 means it took. If the GUI isn't running yet, start it once
  (`ckb-next --background`) so there's an instance to receive the
  switch.

  If the manual write doesn't switch modes either, the issue is with
  your ckb-next setup or mode numbering, not this service.

---

## License

MIT — see `LICENSE`.
