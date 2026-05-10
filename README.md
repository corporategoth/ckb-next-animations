# ckb-next-animations

Custom animations for [ckb-next](https://github.com/ckb-next/ckb-next),
the open-source Linux/macOS driver for Corsair keyboards. Pure Python
3, no external dependencies — they speak the ckb-next animation plugin
protocol directly over stdin/stdout.

Tested on a **Corsair K70**. Other ckb-next-supported keyboards should
work, but key layouts (especially the numpad and media-key clusters)
may differ and visuals may not line up exactly.

## Authorship

Designed and refined by **Preston A. Elder** &lt;prez@neuromancy.net&gt;.
Code authored by **Claude AI** under Preston's direction.

## What's here

### Animations

| Name | Plays itself? | What it does |
| --- | --- | --- |
| **[Brick Breaker](docs/brickbreaker.md)** (`brickbreaker`) | Yes | Self-playing brick-breaker with 36 progressive levels, AI skill randomized per game, multi-hit bricks, power-up drops, multi-ball, numpad level counter. |
| **[Medical Monitor](docs/medical-monitor.md)** (`medical-monitor`) | Reacts to typing | Hospital bedside monitor: ECG sinus rhythm, pulse-ox, respiratory rate, defibrillator state machine that charges when you're idle and discharges with a flatline flash sequence. |
| **[Snake](docs/snake.md)** (`snake`) | Yes | Self-playing classic snake. Hunts dots, grows on each catch, speeds up as it grows, dies on self- or edge-collision, then respawns small. |

### Tools & integrations

- **[Visual test harness](docs/test-harness.md)** (`tools/test_visual.py`)
  — generic offline runner for any ckb-next animation. Renders frames
  to a pygame window over a captured copy of your real keyboard
  layout. Live-edit params, presets, gradients, and the harness
  speed multiplier in-window.
- **[Lock-mode switching](docs/lock-mode-switching.md)**
  (`ckb-mode-on-lock.service`) — user-level systemd service that
  switches the active ckb-next mode (and optionally profile) when
  your desktop session locks/unlocks. Cross-DE: KDE, GNOME, XFCE,
  Cinnamon, MATE.

## Quick install (animations)

```sh
mkdir -p ~/.local/share/ckb-next/animations
cp brickbreaker medical-monitor snake ~/.local/share/ckb-next/animations/
chmod +x ~/.local/share/ckb-next/animations/{brickbreaker,medical-monitor,snake}
```

In the ckb-next GUI: **Settings → Animation Scripts → Re-scan**, then
add the animation to a mode in the **Lighting** tab. Configure
**Properties → Settings → Playback** as the per-animation docs
specify (most need **"Start with mode"**; medical-monitor also needs
**"Start with key press"**). No daemon or GUI restart required.

For a system-wide install instead, copy the files to
`/usr/lib/ckb-next-animations/` (or wherever your distribution's
ckb-next package keeps them) and Re-scan as above.

## License

MIT — see [LICENSE](LICENSE).
