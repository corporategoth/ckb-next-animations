# Visual test harness (`tools/test_visual.py`)

A generic offline runner for any ckb-next animation. Speaks the
standard `--ckb-info` / `--ckb-run` plugin protocol — the same one the
daemon speaks — so it works against this repo's animations, against
the bundled compiled animations in `/usr/lib/ckb-next-animations/`, and
against any third-party animation that follows the ckb-next plugin
contract. Output renders to a pygame window over a captured copy of
your real keyboard layout. **Pygame is only a test dependency** — the
animations themselves remain pure stdlib.

## One-time setup

### 1. Capture your keyboard's layout

The harness needs to know where each key sits to draw a faithful image
of your board.

```sh
cp tools/dump_keymap ~/.local/share/ckb-next/animations/
chmod +x ~/.local/share/ckb-next/animations/dump_keymap
```

In ckb-next: **Settings → Animation Scripts → Re-scan**, apply the
**Capture Keymap** animation once (every key glows soft cyan to
confirm). It writes to `~/.cache/ckb-next/keymap.json`. Switch back to
your normal animation when done.

### 2. Set up the test virtualenv

```sh
uv sync   # creates .venv with pygame-ce
```

## Running

The animation is the only required CLI argument. Bare names are
resolved against `~/.local/share/ckb-next/animations/` first, then
`/usr/lib/ckb-next-animations/` — so a user-installed copy shadows
the bundled one of the same name (e.g. this repo's `snake`
overriding the system `snake`). Absolute or relative paths work too. Everything else (params, presets, speed, colors) is
configured live in the window.

```sh
uv run tools/test_visual.py brickbreaker
uv run tools/test_visual.py snake
uv run tools/test_visual.py random
uv run tools/test_visual.py /path/to/some/animation
uv run tools/test_visual.py wave --list-params   # dump and exit
```

## Recording (GIF / MP4 / WebM)

The harness can pipe its rendered output straight to an `ffmpeg`
subprocess for capture, so what you see in the window is exactly what
gets saved — no compositor scaling, no window decorations, frame-rate
locked to the animation. Requires `ffmpeg` on your PATH (no other
Python deps).

| Flag | Default | Notes |
| --- | --- | --- |
| `--record FILE` | (off) | Output path. Format picked from extension: `.gif`, `.mp4`, `.webm`. |
| `--record-fps N` | 15 (gif) / 60 (video) | Output framerate, independent of render rate. |
| `--record-duration SECONDS` | 10 | Auto-stop after this many wall seconds; `0` means "until you quit (Esc/q)". |
| `--record-headless` | off | Don't open a window — render to an off-screen surface only. Useful for unattended captures. |

Examples:

```sh
# 10-second GIF of brickbreaker, headless (no window pops up):
uv run tools/test_visual.py brickbreaker \
    --record brickbreaker.gif --record-headless

# 30-second MP4 of snake at 30 fps:
uv run tools/test_visual.py snake \
    --record snake.mp4 --record-fps 30 --record-duration 30

# Interactive — open the window, drive it with the keyboard panel,
# and stop recording when you quit:
uv run tools/test_visual.py wave \
    --record wave.gif --record-duration 0
```

GIFs go through ffmpeg's `palettegen` + `paletteuse` filter for sane
colour quality and reasonable file size. Videos use `libx264` at
CRF 20.

## Idle / background colour

Set your keyboard's idle ("always-on") color once with `--base R,G,B`
(or `--base RRGGBB`); it's persisted to
`~/.cache/ckb-next/test_visual.json` and used as the composite
background for every subsequent run. Animations that emit
alpha-blended colors (e.g. medical-monitor's ECG trace at partial
alpha) blend over it the same way the real ckb-next daemon does, so
what you see in the window matches what would land on the keyboard.

```sh
uv run tools/test_visual.py brickbreaker --base 0,85,0   # set & remember
uv run tools/test_visual.py medical-monitor              # uses the saved value
```

The window background colour can also be set live in the panel's
settings rows.

## Live controls (animation window)

| Key / Action       | Effect                                              |
| ------------------ | --------------------------------------------------- |
| `tab`              | toggle the param panel                              |
| `space`            | pause / resume                                      |
| `r`                | restart the animation                               |
| `q` / `Esc`        | quit (or close the panel if it's open)              |
| **mouse click**    | toggle **interactive mode** — keystrokes are forwarded to the animation as `key NAME down/up`, so animations that respond to typing (medical-monitor, heat, ripple) actually respond. Click again to exit. None of the harness shortcuts work while interactive — that's the whole point. |

When the animation declares `harness speed`, `+` / `-` scrub the
multiplier and `1` resets it to the declared default.

`--speed N` overrides the initial multiplier from the CLI — useful
when recording (e.g. capturing brickbreaker at real-time even though
it declares `harness speed default=5.0`) or when you want to start
slowed-down/sped-up without reaching for the panel:

```sh
uv run tools/test_visual.py brickbreaker --speed 1   # real-time
uv run tools/test_visual.py snake        --speed 5   # 5× faster
```

The flag works even on animations that don't declare `harness
speed` — the time delta is always scaled, just the in-app `+`/`-`
hotkeys stay disabled.

## In the param panel

| Key          | Action                                            |
| ------------ | ------------------------------------------------- |
| `↑` / `↓`    | select row                                        |
| `←` / `→`    | adjust the selected param  /  cycle the preset row |
| `enter`      | enter text-edit mode  /  apply the selected preset |
| `del`        | reset a hidden (•) param to `auto`                |
| `c`          | toggle color display between hex and decimal      |

### Gradient stops

Gradient and agradient parameters expand into one row per stop. With a
stop row focused:

| Key          | Action                                            |
| ------------ | ------------------------------------------------- |
| `←` / `→`    | nudge position ±1 (clamped to neighbors)          |
| `0`-`9`      | open numeric position editor pre-filled with that digit |
| `+` / Insert | insert a new stop after the focused one (midpoint of current and next, color copied) |
| `-` / Delete | remove the focused stop (no-op on the 0% / 100% endpoints) |
| `b`          | rebalance all stops to evenly span 0..100         |
| `enter`      | edit the stop's color                             |

The endpoints (0% and 100%) lock their positions — only their colors
are editable.

## Harness extras (animation opt-in)

The harness recognises non-standard `harness …` lines in `--ckb-info`
that let an animation declare which extra interactive controls it
supports. The real ckb-next daemon ignores any line it doesn't
recognise, so the declarations are no-ops in production.

| Form                                              | Effect                                      |
| ------------------------------------------------- | ------------------------------------------- |
| `harness speed default=N min=N max=N`             | Animation is self-paced; harness exposes `+` / `-` / `1` to scrub the wall→animation time multiplier. |
| `harness param NAME type=TYPE default=auto min=N max=N label=...` | Hidden tunable. Shown alongside the regular params in the in-app panel (marked with a leading `•`); accepts the literal `auto` to mean "let the animation decide" and any other value to force a specific override. The harness sends changes back through the standard `param NAME VALUE` protocol, so the animation just handles it in its existing param dispatch. |

## Live vs. static params

Animations declaring `parammode live` (this repo's animations and
almost all of the bundled ckb-next ones) accept param changes
mid-run, so edits in the panel apply instantly. For the rare
`parammode static` animation, edits are queued locally and take effect
on the next `r` restart.

## Time-mode handling

Bundled animations in `/usr/lib/ckb-next-animations/` use one of two
time modes (declared in `--ckb-info`):

- `time absolute` — `dt` is in real seconds. The harness passes wall
  seconds straight through.
- `time duration` — `dt` is a fraction of one `duration` cycle (where
  `duration` is a daemon-level param set by the preset). The harness
  scales wall seconds by `1/duration` so e.g. snake's 40s cycle
  actually takes 40s rather than 1s.

This means snake / wave / ripple / pinwheel etc. play at their
declared cadence at speed=1×; crank the harness speed multiplier to
fast-forward.
