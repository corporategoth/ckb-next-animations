#!/usr/bin/env python3
"""test_visual.py - Run any ckb-next animation offline and watch it.

Speaks the standard ckb-next stdin/stdout plugin protocol (--ckb-info to
discover, --ckb-run to drive frames), so any animation that the real
daemon would launch also runs unmodified here as a subprocess.

The animation is the only required CLI argument — everything else
(parameters, presets, speed) is configured live in the window. Non-
standard controls are gated: an animation advertises them in its
--ckb-info output via opt-in lines that the real daemon ignores.

    harness speed default=N min=N max=N
        animation is self-paced — the harness exposes +/- live scrubbing
        of the wall→animation time multiplier.

    harness param NAME type=TYPE default=auto min=N max=N label=...
        a hidden tunable that the harness shows in the in-app param
        panel alongside the regular params. The literal value `auto`
        means "let the animation decide" (e.g. let randomization run);
        any other value forces a specific override. Useful for things
        like AI difficulty, internal seeds, or any knob you want to
        debug interactively without exposing it through the ckb-next
        GUI.

Animations that don't declare anything still play; they just run at real
time with no extra knobs.

Examples:
    uv run tools/test_visual.py brickbreaker
    uv run tools/test_visual.py random
    uv run tools/test_visual.py /path/to/some/animation
    uv run tools/test_visual.py wave --list-params

Live controls (always):
    tab         show / hide the param panel
    space       pause / resume
    r           restart the animation
    q / Esc     quit  (Esc closes the panel first, if open)
    mouse click toggle interactive mode — every keystroke is then
                forwarded to the animation as `key NAME down/up`, so
                animations that respond to typing (medical-monitor,
                heat, ripple) actually respond. Click again to exit.
                While on, none of the harness shortcuts above work;
                that's the whole point.

In the param panel:
    ↑ / ↓       select row
    ← / →       adjust selected param  /  cycle the preset row
    enter       enter text-edit mode  /  apply the selected preset
    del         reset a hidden (•) param to "auto"

Live controls (only when the animation declares `harness speed`):
    + / =       speed up (1.25x)
    -           slow down (0.8x)
    1           reset speed to the declared default

Setup: run tools/dump_keymap once via the ckb-next GUI to capture your
keyboard's layout to ~/.cache/ckb-next/keymap.json, then `uv sync` and
`uv run tools/test_visual.py ANIMATION` from the repo root. Set the
keyboard's idle ('always-on') color once with `--base R,G,B` (or
`--base RRGGBB`); the value is remembered in
~/.cache/ckb-next/test_visual.json for future runs, and animations
that emit alpha-blended colors composite over it the same way the
real ckb-next daemon would.
"""
import argparse
import json
import os
import subprocess
import sys

import pygame


# === ckb-next protocol token encoding ======================================

def _need_encode(c):
    o = ord(c)
    return (o <= 0x2C or o == 0x2F or 0x3A <= o <= 0x40
            or o == 0x5B or o == 0x5D or o >= 0x7F)


def url_encode(s):
    return "".join("%{:02X}".format(ord(c)) if _need_encode(c) else c for c in s)


# === Keymap loading ========================================================

def keymap_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ckb-next", "keymap.json")


def load_keymap(path):
    with open(path) as f:
        return json.load(f)["keys"]


# === Persistent harness settings ===========================================
# Things like the keyboard's base "always-on" color are setup-specific
# (every user has a different idle color), not animation-specific, so we
# remember them in a small JSON file alongside the keymap. CLI overrides
# write through, so passing --base once is enough to persist it.

def settings_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ckb-next", "test_visual.json")


def load_settings():
    try:
        with open(settings_path()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


def parse_base_color(s):
    """Accepts 'R,G,B' (decimal triple) or 'RRGGBB' / '#RRGGBB' (hex)."""
    s = s.strip().lstrip("#")
    try:
        if "," in s:
            parts = [int(x.strip()) for x in s.split(",")]
            if len(parts) != 3 or any(not 0 <= v <= 255 for v in parts):
                raise ValueError
            return tuple(parts)
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        pass
    raise ValueError("expected 'R,G,B' (0-255 each) or 'RRGGBB' hex")


def blend_argb(argb, base):
    """Composite the animation's ARGB output over the keyboard's base
    color. The animation's alpha decides how much it replaces vs. blends
    with what would be there without any animation running."""
    a, r, g, b = argb
    alpha = a / 255.0
    inv = 1 - alpha
    return (
        int(r * alpha + base[0] * inv),
        int(g * alpha + base[1] * inv),
        int(b * alpha + base[2] * inv),
    )


# === Subprocess driver =====================================================

def parse_argb(token):
    if len(token) != 8:
        return (0, 0, 0, 0)
    try:
        return (int(token[0:2], 16), int(token[2:4], 16),
                int(token[4:6], 16), int(token[6:8], 16))
    except ValueError:
        return (0, 0, 0, 0)


# === Key cap labels and width inference ====================================

# Short label per key name. Anything missing falls back to first char upper
# (single-char names) or the verbatim name (multi-char), so an unknown key
# name like "fnlock" still renders something readable instead of being
# silently truncated. Filled triangles (▲▼◀▶ U+25B2/BC/C0/B6) render in
# any decent monospace font with broad Unicode coverage; line-drawing
# arrows (↑↓←→) often miss in stripped-down fonts.
KEY_LABELS = {
    "esc": "Esc", "prtscn": "Prt", "scroll": "Scr", "pause": "Pse",
    "grave": "`", "minus": "-", "equal": "=",
    "bspace": "Bksp", "tab": "Tab",
    "lbrace": "[", "rbrace": "]", "bslash": "\\",
    "caps": "Caps", "colon": ";", "quote": "'", "enter": "Ent",
    "lshift": "Shft", "rshift": "Shft",
    "comma": ",", "dot": ".", "slash": "/",
    "lctrl": "Ctl", "rctrl": "Ctl",
    "lwin": "Win", "rwin": "Win",
    "lalt": "Alt", "ralt": "Alt",
    "rmenu": "Mn", "space": "",
    "ins": "Ins", "home": "Hm", "pgup": "PgU",
    "del": "Del", "end": "End", "pgdn": "PgD",
    "up": "▲", "down": "▼", "left": "◀", "right": "▶",
    "numlock": "NL", "numslash": "/", "numstar": "*",
    "numminus": "-", "numplus": "+", "numenter": "Ent", "numdot": ".",
    "stop": "■", "prev": "◀◀", "play": "▶", "next": "▶▶",
    # ⊘ U+2298 renders in DejaVu; ☀ U+2600 renders in DejaVu. ⚿ U+26BF
    # is missing in DejaVu and shows as a tofu box, so use a plain "L".
    "mute": "⊘", "volup": "V+", "voldn": "V-",
    "light": "☀", "lock": "L",
}
for n in range(10):
    KEY_LABELS["num{}".format(n)] = str(n)
for n in range(1, 13):
    KEY_LABELS["f{}".format(n)] = "F{}".format(n)


def label_for(name):
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    return name[0].upper() if len(name) == 1 else name.capitalize()


# Keys that should render as round buttons (not rounded rectangles).
# On the K70 these sit on the top media bar and are physically circular.
ROUND_KEYS = frozenset({"light", "lock"})


# K70 ANSI cap-position overrides as (cap_x, cap_y, cap_w, cap_h) in
# keymap units. The ckb-next daemon emits LED positions, not cap positions,
# and the LED-to-cap relationship is irregular for wide keys, so for the
# K70 ANSI layout we just hardcode the visually-correct cap rects. The
# typing block's right edge is x=180; the right cluster (ins/del/...)
# starts at x=184, mirroring the F-row's natural F12→PrtScn gap.
K70_OVERRIDES = {
    # Typing-row left widenings (force cap_left = 0 to align with esc/grave).
    "tab":      (0,   39, 18, 12),  # 1.5u
    "caps":     (0,   51, 21, 12),  # 1.75u
    "lshift":   (0,   63, 27, 12),  # 2.25u
    # Typing-row right widenings (cap_right = 180, abutting the key on the
    # left so there's no internal gap inside the typing block).
    "bspace":   (156, 27, 24, 12),  # 2u
    "bslash":   (162, 39, 18, 12),  # 1.5u
    "enter":    (153, 51, 27, 12),  # 2.25u
    "rshift":   (147, 63, 33, 12),  # 2.75u
    # F12 sized like F11 (the data's F12→PrtScn gap is 16, just over
    # default, which would otherwise stretch F12 wider than F11).
    "f12":      (168, 14, 12, 12),
    # Right cluster: PgUp/PgDn/Pause have small (<1.4×) gaps to numpad/
    # media so the heuristic would over-stretch them. Force 1u.
    "pause":    (208, 14, 12, 12),
    "pgup":     (208, 27, 12, 12),
    "pgdn":     (208, 39, 12, 12),
    # Bottom row: lctrl ≈ lalt = ralt > lwin = rwin = rmenu, rctrl widest;
    # space fills the entire distance between the alts.
    "lctrl":    (0,   75, 15, 12),
    "lwin":     (15,  75, 12, 12),
    "lalt":     (27,  75, 15, 12),
    "space":    (42,  75, 81, 12),
    "ralt":     (123, 75, 15, 12),
    "rwin":     (138, 75, 12, 12),
    "rmenu":    (150, 75, 12, 12),
    "rctrl":    (162, 75, 18, 12),
    # Numpad specials: num0 is double-wide, numplus/numenter span two rows.
    "num0":     (223, 75, 24, 12),
    "numplus":  (259, 39, 12, 24),
    "numenter": (259, 63, 12, 24),
}


def compute_defaults(keys):
    """Infer the standard (default_w, default_h) of a 1u key from the
    captured layout, by taking the median gap between adjacent keys
    inside primary rows (rows with ≥3 keys)."""
    rows = {}
    for k in keys:
        rows.setdefault(k["y"], []).append(k)
    for y in rows:
        rows[y].sort(key=lambda k: k["x"])

    primary_ys = sorted(y for y, kl in rows.items() if len(kl) >= 3)
    if len(primary_ys) >= 2:
        ygaps = [primary_ys[i + 1] - primary_ys[i] for i in range(len(primary_ys) - 1)]
        default_h = sorted(ygaps)[len(ygaps) // 2]
    else:
        default_h = 12

    xgaps = []
    for y in primary_ys:
        row = rows[y]
        for i in range(len(row) - 1):
            xgaps.append(row[i + 1]["x"] - row[i]["x"])
    default_w = sorted(xgaps)[len(xgaps) // 2] if xgaps else 12
    return default_w, default_h


def compute_cap_layout(keys, default_w, default_h):
    """For every key return (cap_x, cap_y, cap_w, cap_h). K70_OVERRIDES
    win where present; otherwise we use captured x/y as the cap top-left
    and infer the width from row-neighbor distance, clamping any large
    gap to default_w (treated as a cluster separator)."""
    rows = {}
    for k in keys:
        rows.setdefault(k["y"], []).append(k)
    for y in rows:
        rows[y].sort(key=lambda k: k["x"])

    layout = {}
    for k in keys:
        name = k["name"]
        if name in K70_OVERRIDES:
            layout[name] = K70_OVERRIDES[name]
            continue
        row = rows[k["y"]]
        i = row.index(k)
        if i + 1 < len(row):
            gap = row[i + 1]["x"] - k["x"]
            w = default_w if gap > 1.2 * default_w else gap
        else:
            w = default_w
        layout[name] = (k["x"], k["y"], w, default_h)
    return layout


# --- ckb-info parsing -----------------------------------------------------

def url_decode(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(s[i])
        i += 1
    return "".join(out)


# How many trailing whitespace-separated tokens are data (default + min/max)
# vs. label/units, by ckb-next param type. Whitespace splitting collapses
# empty UNITS, so we count from the right to recover field positions.
# `label` declares a UI-only label with no value; the rest follow ckb-anim.h
# CKB_PARAM_* macros byte-for-byte.
_PARAM_TRAILING = {
    "double":    3,  # default min max
    "long":      3,  # default min max
    "bool":      1,  # default (0 or 1)
    "argb":      1,  # 8-hex default
    "rgb":       1,  # 6-hex default
    "string":    1,  # url-encoded default
    "angle":     1,  # default (degrees, [0..359])
    "gradient":  1,  # url-encoded gradient default ("0:rrggbb 100:rrggbb")
    "agradient": 1,  # url-encoded gradient default ("0:aarrggbb 100:aarrggbb")
    "label":     0,  # display-only, no value to send back
}


def _parse_param(tokens):
    """tokens is a whitespace-split line beginning with 'param'. Returns
    a {type, name, label, units, default[, min, max]} dict, or None for
    a malformed/unknown-type line."""
    if len(tokens) < 4 or tokens[0] != "param":
        return None
    ptype, name = tokens[1], tokens[2]
    trailing_n = _PARAM_TRAILING.get(ptype)
    if trailing_n is None or len(tokens) < 3 + trailing_n + 1:
        return None
    if trailing_n > 0:
        trailing = tokens[-trailing_n:]
        text = tokens[3:-trailing_n]
    else:
        trailing = []
        text = tokens[3:]
    info = {
        "type": ptype, "name": name,
        "label": url_decode(text[0]) if text else "",
        "units": url_decode(text[1]) if len(text) > 1 else "",
        "default": trailing[0] if trailing else "",
    }
    if ptype in ("double", "long"):
        info["min"], info["max"] = trailing[1], trailing[2]
    return info


def discover_animation_info(exe):
    """Run `exe --ckb-info`, parse name/params/presets/harness. Values in
    presets are kept as raw strings (matching what the daemon would send
    back via `param NAME VALUE`).

    `harness FEATURE k=v ...` lines are an opt-in extension that lets an
    animation tell the test harness which non-standard controls it
    supports. ckb-next itself ignores unknown lines, so the declaration
    is harmless under the real daemon."""
    result = subprocess.run([exe, "--ckb-info"], capture_output=True,
                            text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(
            "{} --ckb-info failed (rc={}): {}".format(
                exe, result.returncode, result.stderr.strip()))
    # `time` defaults to "duration" in ckb-anim.h (CKB_TIMEMODE docs say the
    # default is DURATION); `parammode` defaults to "static". Both keys are
    # always present in the result, even if the animation never declares them.
    info = {"name": os.path.basename(exe), "params": [], "presets": {},
            "harness": {}, "harness_params": [],
            "parammode": "static", "timemode": "duration"}
    for raw_line in result.stdout.splitlines():
        tokens = raw_line.split()
        if not tokens:
            continue
        head = tokens[0]
        if head == "name" and len(tokens) > 1:
            info["name"] = url_decode(tokens[1])
        elif head == "parammode" and len(tokens) > 1:
            info["parammode"] = tokens[1]
        elif head == "time" and len(tokens) > 1:
            info["timemode"] = tokens[1]
        elif head == "param":
            p = _parse_param(tokens)
            if p is not None:
                info["params"].append(p)
        elif head == "preset" and len(tokens) >= 2:
            kvs = {}
            for tok in tokens[2:]:
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    # Preset values come URL-encoded over the wire (e.g.
                    # `color=0%3Affff0000%2017%3A...` for gradients);
                    # keep them human-readable in the editor and re-encode
                    # only when we ship them back to the animation.
                    kvs[k] = url_decode(v)
            info["presets"][url_decode(tokens[1])] = kvs
        elif head == "harness" and len(tokens) >= 2:
            feature = tokens[1]
            if feature == "param" and len(tokens) >= 3:
                # `harness param NAME k=v k=v ...` declares a hidden
                # tunable that the harness exposes alongside the regular
                # params, but which the standard ckb-next GUI doesn't see.
                # Useful for animation-internal knobs (random seeds, AI
                # difficulty, etc.) where a value of `auto` keeps the
                # animation's normal behaviour and any other value forces
                # a specific override.
                kvs = {"name": tokens[2], "type": "double",
                       "default": "auto", "label": "", "harness": True}
                for tok in tokens[3:]:
                    if "=" in tok:
                        k, _, v = tok.partition("=")
                        kvs[k] = url_decode(v) if k == "label" else v
                info["harness_params"].append(kvs)
            else:
                kvs = {}
                for tok in tokens[2:]:
                    if "=" in tok:
                        k, _, v = tok.partition("=")
                        kvs[k] = v
                info["harness"][feature] = kvs
    return info


def format_param_listing(info):
    out = ["Animation: {}".format(info["name"]),
           "Parameters:"]
    if not info["params"]:
        out.append("  (none)")
    for p in info["params"]:
        bits = ["{}={}".format(p["type"], p["default"])]
        if "min" in p:
            bits.append("range {}..{}".format(p["min"], p["max"]))
        if p["units"]:
            bits.append("unit: {}".format(p["units"]))
        label = " ({})".format(p["label"]) if p["label"] else ""
        out.append("  {:18s} {}{}".format(p["name"], "  ".join(bits), label))
    if info["presets"]:
        out.append("Presets:")
        for pname, kvs in info["presets"].items():
            out.append("  {:18s} {}".format(
                pname, " ".join("{}={}".format(k, v) for k, v in kvs.items())))
    if info["harness"]:
        out.append("Harness extras (test_visual.py opt-ins):")
        for feature, kvs in info["harness"].items():
            kv = " ".join("{}={}".format(k, v) for k, v in kvs.items())
            out.append("  {:18s} {}".format(feature, kv))
    if info["harness_params"]:
        out.append("Harness-tunable hidden params:")
        for hp in info["harness_params"]:
            bits = ["{}={}".format(hp.get("type", "double"), hp.get("default", "auto"))]
            if "min" in hp and "max" in hp:
                bits.append("range {}..{}".format(hp["min"], hp["max"]))
            label = " ({})".format(hp["label"]) if hp.get("label") else ""
            out.append("  {:18s} {}{}".format(hp["name"], "  ".join(bits), label))
    return "\n".join(out)


# --- Animation subprocess driver -----------------------------------------

class Animation:
    """Runs an arbitrary ckb-next animation as a child process and
    shuffles the ckb-next stdin/stdout protocol over its pipes — i.e.
    we act as the daemon. Works with any animation that implements
    --ckb-info and --ckb-run (the standard ckb-next plugin interface)."""

    def __init__(self, exe, keys, params=None):
        self.exe = exe
        self.keys = keys
        self.params = params or {}
        self.colors = {k["name"]: (0, 0, 0, 0) for k in keys}
        self.proc = subprocess.Popen(
            [exe, "--ckb-run"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=1,
            text=True,
        )
        self._handshake()

    def _send(self, line):
        self.proc.stdin.write(line + "\n")

    def _handshake(self):
        self._send("begin keymap")
        self._send("keycount {}".format(len(self.keys)))
        for k in self.keys:
            self._send("key {} {},{}".format(url_encode(k["name"]), k["x"], k["y"]))
        self._send("end keymap")
        self._send("begin params")
        for name, value in self.params.items():
            self._send("param {} {}".format(url_encode(name), url_encode(str(value))))
        self._send("end params")
        self._send("begin run")
        # `start` (state=1) puts kpmode=none animations like wave/snake/
        # rain into their running state so they actually emit color frames.
        # The ckb-next C animation framework parses just the bare command
        # (trailing tokens are ignored), so this is also the same form the
        # real daemon emits at mode-start. brickbreaker / medical-monitor
        # accept it too and treat it as a no-op since they auto-run from
        # ckb_init.
        self._send("start")
        self.proc.stdin.flush()
        ack = self.proc.stdout.readline().strip()
        if not ack.startswith("begin run"):
            raise RuntimeError(
                "{}: animation did not start (got {!r})".format(self.exe, ack))

    def step(self, dt):
        """Advance dt seconds of animation time, read back one frame of colors."""
        if self.proc.poll() is not None:
            return False
        self._send("time {:.6f}".format(dt))
        self._send("frame")
        self.proc.stdin.flush()
        out = self.proc.stdout
        while True:
            line = out.readline()
            if not line:
                return False
            line = line.rstrip("\n")
            if line == "end frame":
                return True
            if line.startswith("argb "):
                parts = line.split(None, 2)
                if len(parts) == 3:
                    self.colors[parts[1]] = parse_argb(parts[2])

    def push_params(self, kvs):
        """Send a live `begin params...end params` block. Animations with
        `parammode live` apply the values immediately; the rest treat the
        block as a no-op until we restart them with the new params baked
        into the next handshake."""
        if self.proc.poll() is not None:
            return
        self._send("begin params")
        for k, v in kvs.items():
            self._send("param {} {}".format(url_encode(k), url_encode(str(v))))
        self._send("end params")
        self.proc.stdin.flush()
        self.params.update(kvs)

    def keypress(self, name, down):
        """Forward a keypress event to the animation. Used by the
        harness's interactive mode for animations whose kpmode reacts to
        physical typing (heat, ripple, medical-monitor, etc.)."""
        if self.proc.poll() is not None:
            return
        self._send("key {} {}".format(url_encode(name), "down" if down else "up"))
        self.proc.stdin.flush()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self._send("end run")
                self.proc.stdin.flush()
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# Map pygame.key.name() output to the corresponding ckb-next key name
# used in the keymap protocol. pygame uses verbose human-readable names
# (e.g. "left ctrl") while ckb-next uses short names (e.g. "lctrl");
# letters/digits/F-keys/arrows already match between the two systems.
PYGAME_TO_CKB = {
    "left ctrl": "lctrl",   "right ctrl": "rctrl",
    "left shift": "lshift", "right shift": "rshift",
    "left alt": "lalt",     "right alt": "ralt",
    "left meta": "lwin",    "right meta": "rwin",
    "left super": "lwin",   "right super": "rwin",
    "menu": "rmenu",
    "caps lock": "caps", "scroll lock": "scroll",
    "tab": "tab", "return": "enter", "backspace": "bspace",
    "escape": "esc", "pause": "pause", "print screen": "prtscn",
    "page up": "pgup", "page down": "pgdn",
    "insert": "ins", "delete": "del",
    "`": "grave", "-": "minus", "=": "equal",
    "[": "lbrace", "]": "rbrace", "\\": "bslash",
    ";": "colon", "'": "quote", ",": "comma", ".": "dot", "/": "slash",
    "numlock": "numlock", "num lock": "numlock",
    "[/]": "numslash", "[*]": "numstar",
    "[-]": "numminus", "[+]": "numplus",
    "[.]": "numdot", "[,]": "numdot",
}
PYGAME_TO_CKB.update({"[{}]".format(n): "num{}".format(n) for n in range(10)})


def pygame_to_ckb(pname):
    """Single-char names ('a', '1') match ckb directly; anything else
    falls through PYGAME_TO_CKB. Returns None for keys without a
    corresponding ckb name we should forward."""
    if pname in PYGAME_TO_CKB:
        return PYGAME_TO_CKB[pname]
    if len(pname) == 1:
        return pname
    if pname.startswith("f") and pname[1:].isdigit():  # f1..f12
        return pname
    if pname in ("up", "down", "left", "right", "home", "end", "space"):
        return pname
    return None


# === Layout =================================================================

def fit_keys_to_window(keys, layout, window_size, margin=30):
    """Lay out caps inside the window using a precomputed cap layout
    (name -> (cap_x, cap_y, cap_w, cap_h) in keymap units). The LED dot
    is drawn at the cap's center — purely visual; the captured x/y is
    only used by brickbreaker for collision physics."""
    rects = [layout[k["name"]] for k in keys]
    minx = min(r[0] for r in rects)
    miny = min(r[1] for r in rects)
    maxx = max(r[0] + r[2] for r in rects)
    maxy = max(r[1] + r[3] for r in rects)
    spanx = maxx - minx
    spany = maxy - miny

    win_w, win_h = window_size
    s = min((win_w - 2 * margin) / spanx, (win_h - 2 * margin) / spany)
    ox = (win_w - spanx * s) / 2
    oy = (win_h - spany * s) / 2

    inset = max(1.0, s * 0.5)  # gap between adjacent caps

    laid = []
    for k in keys:
        name = k["name"]
        cx, cy, cw, ch = layout[name]
        cap_x = ox + (cx - minx) * s
        cap_y = oy + (cy - miny) * s
        cap_w_px = cw * s
        cap_h_px = ch * s
        cap_rect = pygame.Rect(
            int(cap_x + inset), int(cap_y + inset),
            max(4, int(cap_w_px - 2 * inset)),
            max(4, int(cap_h_px - 2 * inset)))
        led_size = max(4, int(min(cap_w_px, cap_h_px) * 0.40))
        led_rect = pygame.Rect(
            cap_rect.centerx - led_size // 2,
            cap_rect.centery - led_size // 2,
            led_size, led_size)
        font_size = max(9, min(20, int(min(cap_w_px, cap_h_px) * 0.34)))
        laid.append({
            "name": name,
            "shape": "round" if name in ROUND_KEYS else "rect",
            "cap_rect": cap_rect,
            "led_rect": led_rect,
            "font_size": font_size,
            "label": label_for(name),
        })
    return laid


class FontCache:
    """SysFont calls aren't free — cache one font per pixel size. We
    explicitly request DejaVu Sans Mono (and Liberation Mono) ahead of
    the generic 'monospace' alias, because both ship with broad Unicode
    coverage (filled triangles, ☀, ⊘) while a system's default
    'monospace' alias may resolve to something stripped down."""
    def __init__(self, family="dejavusansmono,liberationmono,monospace", bold=True):
        self.family, self.bold = family, bold
        self._cache = {}

    def get(self, size):
        f = self._cache.get(size)
        if f is None:
            f = pygame.font.SysFont(self.family, size, bold=self.bold)
            self._cache[size] = f
        return f


# === Rendering =============================================================

DEFAULT_BG = (10, 10, 14)      # window bg if the user hasn't picked one
CAP_BASE = (26, 26, 32)        # cap fill when LED is off
LABEL_COLOR = (185, 185, 195)  # cap legend, kept constant for readability


def render_key(screen, k, led_color):
    """Draws one key (cap fill + outline + LED dot + label) onto `screen`.
    `led_color` is the flattened RGB after applying the LED's alpha."""
    cap_fill = (
        min(255, CAP_BASE[0] + led_color[0] // 4),
        min(255, CAP_BASE[1] + led_color[1] // 4),
        min(255, CAP_BASE[2] + led_color[2] // 4),
    )
    outline = led_color if max(led_color) > 8 else (60, 60, 70)
    cap_rect = k["cap_rect"]
    led_rect = k["led_rect"]
    if k["shape"] == "round":
        center = cap_rect.center
        radius = min(cap_rect.width, cap_rect.height) // 2
        pygame.draw.circle(screen, cap_fill, center, radius)
        pygame.draw.circle(screen, outline, center, radius, width=2)
        pygame.draw.circle(screen, led_color, led_rect.center, led_rect.width // 2)
    else:
        pygame.draw.rect(screen, cap_fill, cap_rect, border_radius=4)
        pygame.draw.rect(screen, outline, cap_rect, width=2, border_radius=4)
        pygame.draw.rect(screen, led_color, led_rect, border_radius=2)


# --- Gradient parsing -----------------------------------------------------

# ckb gradient wire format (per ckb_scan_grad in ckb-anim.h):
#   "0:aarrggbb 17:aarrggbb 50:aarrggbb 100:aarrggbb"
# - Stops are whitespace-separated `pos:hex` tokens.
# - `pos` is 0..100, strictly ascending across stops, no duplicates.
# - `hex` is always 8 hex chars (alpha+rgb). For non-alpha gradients the
#   C parser overwrites alpha to 255, but the wire string still includes it.
# - The parser also accepts a bare 8-hex constant (no stops) as shorthand
#   for a single solid color, which it expands to a 0/100 pair.
# - A valid gradient MUST have stops at 0 and 100.

def _parse_gradient_stops(val_str):
    """Parse a wire-format gradient string into [{'pos': int, 'color': hex}, ...].
    Always returns a list with stops at 0 and 100 (synthesized from the
    nearest neighbor if the string didn't include them)."""
    stops = []
    for tok in val_str.split():
        if ":" not in tok:
            continue
        pos_str, _, color = tok.partition(":")
        try:
            pos = int(pos_str)
        except ValueError:
            continue
        if not 0 <= pos <= 100:
            continue
        color = color.lower()
        if len(color) != 8 or any(c not in "0123456789abcdef" for c in color):
            continue
        stops.append({"pos": pos, "color": color})
    if not stops:
        # Solid-color shorthand fallback (matches ckb_scan_grad's else-branch).
        bare = val_str.strip().lower()
        if len(bare) == 8 and all(c in "0123456789abcdef" for c in bare):
            stops = [{"pos": 0, "color": bare}, {"pos": 100, "color": bare}]
    if not stops:
        stops = [{"pos": 0, "color": "ff000000"},
                 {"pos": 100, "color": "ff000000"}]
    stops.sort(key=lambda s: s["pos"])
    if stops[0]["pos"] != 0:
        stops.insert(0, {"pos": 0, "color": stops[0]["color"]})
    if stops[-1]["pos"] != 100:
        stops.append({"pos": 100, "color": stops[-1]["color"]})
    # Drop duplicate-position stops (keep the first); the C parser refuses
    # them outright. This can happen if a preset hand-rolled a degenerate
    # value, or after we synthesised an endpoint that collided with an
    # existing one.
    deduped = [stops[0]]
    for s in stops[1:]:
        if s["pos"] != deduped[-1]["pos"]:
            deduped.append(s)
    return deduped


def _compose_gradient(stops):
    """Inverse of _parse_gradient_stops; produces a string the C parser
    in ckb-anim.h accepts verbatim."""
    return " ".join("{}:{}".format(s["pos"], s["color"]) for s in stops)


# === In-app parameter editor ===============================================

class ParamPanel:
    """Tab-toggled overlay that lets the user inspect and tune the
    animation's declared parameters and presets at runtime. The panel
    keeps a local mirror of each value, applies arrow-key adjustments
    and text edits, and pushes changes to the running animation via
    `Animation.push_params` when the animation's parammode is live;
    otherwise edits are queued and take effect on the next `r` restart."""

    PANEL_W = 360
    LINE_H = 22
    MARGIN = 14

    def __init__(self, info, animation, font_cache,
                 harness_state=None, on_setting_changed=None,
                 initial_values=None):
        self.info = info
        self.animation = animation
        self.fonts = font_cache
        # Standard params first, then any harness-only "hidden" tunables,
        # then the harness's own UI settings (base/bg colors). All three
        # categories are edited the same way; the categories are
        # distinguished only by where edits go: regular & harness-param
        # edits flow back to the animation, while `_setting` rows update
        # the harness's local render state and persist to the JSON file.
        self.harness_state = harness_state or {}
        self.on_setting_changed = on_setting_changed or (lambda *_: None)
        setting_rows = [
            {"name": "base_color", "type": "rgb", "harness": True,
             "_setting": True, "label": "Idle / base key color",
             "default": _rgb_to_hex(self.harness_state.get("base_color", (0, 0, 0)))},
            {"name": "bg_color", "type": "rgb", "harness": True,
             "_setting": True, "label": "Window background",
             "default": _rgb_to_hex(self.harness_state.get("bg_color", DEFAULT_BG))},
        ]
        # Note: color_format isn't a row — it's a UI affordance, not a
        # tunable that lives next to animation params. It's bound to a
        # hotkey shown in the footer instead (see handle_keydown).
        self._raw_params = (list(info["params"])
                            + list(info["harness_params"])
                            + setting_rows)
        self.values = {p["name"]: p["default"] for p in self._raw_params}
        # Layer in any extra params the caller seeded (e.g. the Default
        # preset's daemon-level trigger=1/duration=N values that aren't
        # part of the declared param list but determine whether the
        # animation actually runs).
        if initial_values:
            self.values.update(initial_values)
        # Gradient/agradient values are stored doubly: the wire string in
        # self.values (so push_params/restart sees the canonical form), and
        # a parsed stop list in self.gradient_stops (so per-stop edits in
        # the UI are cheap). Keep them in sync via _sync_gradient_value.
        self.gradient_stops = {}
        for p in self._raw_params:
            if p["type"] in ("gradient", "agradient"):
                self.gradient_stops[p["name"]] = _parse_gradient_stops(
                    self.values[p["name"]])
                self.values[p["name"]] = _compose_gradient(
                    self.gradient_stops[p["name"]])
        self.params = self._expand_params()
        self.preset_names = list(info["presets"].keys())
        self.preset_idx = 0
        self.selected_idx = 0
        self.scroll_top = 0  # index of the first row currently rendered
        self.editing = False
        self.edit_buffer = ""
        # Cursor position only matters in overwrite-mode (hex colors,
        # fixed-length); append-mode treats it as always being at the end.
        self.edit_cursor = 0
        # Edit modes:
        #   "append"      — single buffer, cursor at end (numerics, etc.)
        #   "overwrite"   — single buffer, cursor moves within (hex colors)
        #   "multi_field" — N labeled sub-buffers (decimal colors)
        self.edit_mode = "append"
        self.edit_auto = False        # auto-capable field showing placeholder?
        # Multi-field state (only used when edit_mode == "multi_field"):
        self.edit_fields = []
        self.edit_field_idx = 0
        # Indices of fields the user has actively touched this session.
        # Untouched fields show their original value but the next
        # keystroke replaces it wholesale, mirroring spreadsheet behavior.
        self.edit_touched = set()
        self.live = info.get("parammode") == "live"

    # --- Gradient stop expansion ------------------------------------------

    def _expand_params(self):
        """Flatten raw params into per-row dicts, expanding gradient and
        agradient params into one synthetic row per stop. The synthetic
        row is typed as `argb` so the existing color-edit machinery
        applies; the parent gradient is referenced by `_grad_param_name`
        and the stop index by `_grad_stop_idx`. Endpoints carry
        `_grad_endpoint=True` so navigation knows their position is
        locked."""
        out = []
        for p in self._raw_params:
            if p["type"] in ("gradient", "agradient"):
                stops = self.gradient_stops[p["name"]]
                last = len(stops) - 1
                for i in range(len(stops)):
                    out.append({
                        "name": p["name"],
                        # Wire format is always 8 hex regardless of alpha
                        # support, so render every stop as argb. Alpha is
                        # ignored client-side for "gradient" but kept so
                        # the round-trip stays canonical.
                        "type": "argb",
                        "label": p.get("label", ""),
                        "default": stops[i]["color"],
                        "_grad_param_name": p["name"],
                        "_grad_stop_idx": i,
                        "_grad_endpoint": (i == 0 or i == last),
                        "_grad_first_visual": (i == 0),
                        "_grad_alpha": (p["type"] == "agradient"),
                    })
            else:
                out.append(p)
        return out

    def _is_grad_stop(self, p):
        return p is not None and "_grad_stop_idx" in p

    def _stop(self, p):
        return self.gradient_stops[p["_grad_param_name"]][p["_grad_stop_idx"]]

    def _value_for_display(self, p):
        """Live value to render for this row. Stop rows pull from the
        parsed stop list; everything else from self.values."""
        if self._is_grad_stop(p):
            return self._stop(p)["color"]
        return self.values.get(p["name"], p["default"])

    def _sync_gradient_value(self, name, push=True):
        """Recompose the wire string for gradient `name`, mirror it into
        self.values, and (optionally) push it to the running animation if
        live. Call after any stop add/remove/edit."""
        wire = _compose_gradient(self.gradient_stops[name])
        self.values[name] = wire
        if push and self.live:
            self.animation.push_params({name: wire})

    def _rebuild_after_stop_change(self, name, target_stop_idx):
        """Re-expand self.params after stops were inserted/removed and
        move selection onto the indicated stop of `name`. Falls back to
        the nearest valid index if the target was the last one removed."""
        self.params = self._expand_params()
        for i, p in enumerate(self.params):
            if (p.get("_grad_param_name") == name
                    and p.get("_grad_stop_idx") == target_stop_idx):
                self.selected_idx = i
                return
        # Target gone (e.g. removed final middle stop): land on the new
        # last stop of this gradient, or stay put if nothing matches.
        candidates = [i for i, p in enumerate(self.params)
                      if p.get("_grad_param_name") == name]
        if candidates:
            self.selected_idx = candidates[min(target_stop_idx,
                                               len(candidates) - 1)]

    def _insert_grad_stop(self, p):
        """Insert a new stop after the focused one, color copied, position
        at the midpoint to its neighbor. Lands the user on the new stop
        with the position editor open."""
        name = p["_grad_param_name"]
        stops = self.gradient_stops[name]
        idx = p["_grad_stop_idx"]
        cur = stops[idx]
        if idx == len(stops) - 1:
            # Focused on the 100% endpoint: insert before it instead.
            prev = stops[idx - 1]
            new_pos = (prev["pos"] + cur["pos"]) // 2
            if new_pos <= prev["pos"] or new_pos >= cur["pos"]:
                return  # no integer slot available
            new_stop = {"pos": new_pos, "color": cur["color"]}
            stops.insert(idx, new_stop)
            new_idx = idx
        else:
            nxt = stops[idx + 1]
            new_pos = (cur["pos"] + nxt["pos"]) // 2
            if new_pos <= cur["pos"] or new_pos >= nxt["pos"]:
                return
            new_stop = {"pos": new_pos, "color": cur["color"]}
            stops.insert(idx + 1, new_stop)
            new_idx = idx + 1
        self._sync_gradient_value(name)
        self._rebuild_after_stop_change(name, new_idx)
        # Drop straight into the position editor on the new stop so the
        # user can dial in a specific value immediately.
        self._activate_grad_pos_edit(self.params[self.selected_idx])

    def _rebalance_grad_stops(self, p):
        """Redistribute every stop's position to evenly span 0..100.
        Endpoints stay locked at 0 and 100; middle stops snap to
        i * 100 / (N-1) (rounded). Colors are preserved — only
        positions move. No-op for the degenerate 2-stop case (already
        evenly spaced)."""
        name = p["_grad_param_name"]
        stops = self.gradient_stops[name]
        n = len(stops)
        if n <= 2:
            return
        for i in range(n):
            stops[i]["pos"] = int(round(i * 100 / (n - 1)))
        self._sync_gradient_value(name)

    def _remove_grad_stop(self, p):
        """Remove the focused stop. Endpoints (first/last, locked at
        0% and 100%) and minimum two-stop gradients are no-ops."""
        name = p["_grad_param_name"]
        stops = self.gradient_stops[name]
        if p["_grad_endpoint"] or len(stops) <= 2:
            return
        idx = p["_grad_stop_idx"]
        del stops[idx]
        self._sync_gradient_value(name)
        # Aim selection at the stop now occupying this index (or its
        # predecessor if we just removed the last middle stop).
        target = min(idx, len(stops) - 1)
        self._rebuild_after_stop_change(name, target)

    @property
    def n_param_rows(self):
        return len(self.params)

    @property
    def has_preset_row(self):
        return bool(self.preset_names)

    @property
    def n_rows(self):
        return self.n_param_rows + (1 if self.has_preset_row else 0)

    def is_preset_row(self):
        return self.has_preset_row and self.selected_idx == self.n_param_rows

    def selected_param(self):
        if self.is_preset_row():
            return None
        return self.params[self.selected_idx]

    # --- Event handling ----------------------------------------------------

    def handle_keydown(self, ev):
        """Returns True if the panel consumed the event."""
        if self.editing:
            return self._handle_edit_key(ev)
        if ev.key == pygame.K_UP:
            self.selected_idx = (self.selected_idx - 1) % max(1, self.n_rows)
            return True
        if ev.key == pygame.K_DOWN:
            self.selected_idx = (self.selected_idx + 1) % max(1, self.n_rows)
            return True
        if ev.key == pygame.K_LEFT:
            self._adjust(-1)
            return True
        if ev.key == pygame.K_RIGHT:
            self._adjust(1)
            return True
        if ev.key == pygame.K_RETURN:
            self._activate()
            return True
        # Gradient stop hotkeys take precedence over the harness-auto
        # reset below: `+` / Insert adds a new stop after the focused
        # one, `-` / Delete removes it (no-op on the 0% / 100% endpoints).
        p = self.selected_param()
        if self._is_grad_stop(p):
            if ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS,
                          pygame.K_INSERT):
                self._insert_grad_stop(p)
                return True
            if ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_DELETE):
                self._remove_grad_stop(p)
                return True
            if ev.key == pygame.K_b:
                self._rebalance_grad_stops(p)
                return True
            # Typing a digit on a stop row jumps straight into the
            # position editor with that digit pre-filled — gives the user
            # an explicit numeric entry path without hijacking another
            # hotkey, mirroring spreadsheet "type-to-edit" behaviour.
            ch = ev.unicode
            if ch and ch.isdigit() and not p["_grad_endpoint"]:
                self._activate_grad_pos_edit(p, prefill=ch)
                return True
        # Backspace on a harness-only param resets it to "auto"
        # (= let the animation decide). Setting rows (base/bg color) have
        # no auto state, so they're skipped. Delete is also bound here for
        # symmetry, but only fires when no gradient stop owned it above.
        if ev.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            if p is not None and p.get("harness") and not p.get("_setting"):
                self.values[p["name"]] = "auto"
                if self.live:
                    self.animation.push_params({p["name"]: "auto"})
            return True
        # Panel-wide toggle (footer hint): flip color display between
        # hex (RRGGBB) and decimal (R,G,B). Affects every color row's
        # rendering and edit buffer at once, so it doesn't belong as a
        # row beside the per-animation params.
        if ev.key == pygame.K_c:
            cur = self.harness_state.get("color_format", "hex")
            new = "decimal" if cur == "hex" else "hex"
            self.on_setting_changed("color_format", new)
            return True
        return False

    def _adjust(self, direction):
        if self.is_preset_row():
            n = len(self.preset_names)
            if n:
                self.preset_idx = (self.preset_idx + direction) % n
            return
        p = self.selected_param()
        if p is None:
            return
        # Gradient stops use ←/→ to nudge position by ±1 (clamped to the
        # neighboring stops' positions). Endpoints stay locked at 0/100.
        # Larger jumps come from typing digits to enter the position
        # editor — see handle_keydown's digit-prefix path.
        if self._is_grad_stop(p):
            if p["_grad_endpoint"]:
                return
            self._nudge_grad_pos(p, direction)
            return
        cur = self.values.get(p["name"], p["default"])
        new = self._step_value(p, cur, direction)
        if new is not None and new != cur:
            self.values[p["name"]] = new
            self._apply(p, new)

    def _nudge_grad_pos(self, p, direction):
        name = p["_grad_param_name"]
        stops = self.gradient_stops[name]
        idx = p["_grad_stop_idx"]
        prev_pos = stops[idx - 1]["pos"]
        next_pos = stops[idx + 1]["pos"]
        new_pos = stops[idx]["pos"] + direction
        if new_pos <= prev_pos or new_pos >= next_pos:
            return  # would collide with a neighbor; refuse
        stops[idx]["pos"] = new_pos
        self._sync_gradient_value(name)

    def _apply(self, p, value):
        """Route a committed value to the right destination: harness
        UI settings go to the on_setting_changed callback (which updates
        live render state and persists), everything else goes to the
        animation as a param update (only when parammode is live)."""
        if p.get("_setting"):
            if p["name"] in ("base_color", "bg_color"):
                try:
                    self.on_setting_changed(p["name"], _hex_to_rgb(value))
                except ValueError:
                    pass
            else:
                # Plain string-valued settings (color_format etc.).
                self.on_setting_changed(p["name"], value)
        elif self.live:
            self.animation.push_params({p["name"]: value})

    def _step_value(self, p, cur, direction):
        # Choices-style settings (color_format) cycle their fixed list.
        if p.get("_choices"):
            choices = p["_choices"]
            try:
                idx = choices.index(cur)
            except ValueError:
                idx = 0
            return choices[(idx + direction) % len(choices)]
        ptype = p["type"]
        cur_str = str(cur)
        # "auto" is the harness-extras sentinel for "let the animation
        # decide". An arrow press from auto seeds the midpoint of the
        # range so subsequent arrows have something to step from.
        is_auto = cur_str.lower() == "auto"
        if ptype == "bool":
            return "1" if cur_str in ("0", "False") else "0"
        if ptype == "long":
            if is_auto and "min" in p:
                cur_i = (int(float(p["min"])) + int(float(p["max"]))) // 2
            else:
                try:
                    cur_i = int(float(cur))
                except (TypeError, ValueError):
                    cur_i = 0
            new = cur_i + direction
            if "min" in p:
                new = max(int(float(p["min"])), min(int(float(p["max"])), new))
            return str(new)
        if ptype == "double":
            if is_auto and "min" in p:
                cur_f = (float(p["min"]) + float(p["max"])) / 2.0
            else:
                try:
                    cur_f = float(cur)
                except (TypeError, ValueError):
                    cur_f = 0.0
            if "min" in p:
                step = (float(p["max"]) - float(p["min"])) / 100.0
            else:
                step = max(0.01, abs(cur_f) * 0.1)
            new_f = cur_f + direction * step
            if "min" in p:
                new_f = max(float(p["min"]), min(float(p["max"]), new_f))
            return _format_float(new_f)
        return None  # argb / rgb / string: arrows don't adjust, use Enter→edit

    def _color_format(self):
        return self.harness_state.get("color_format", "hex")

    def _activate(self):
        if self.is_preset_row():
            if not self.preset_names:
                return
            kvs = self.info["presets"][self.preset_names[self.preset_idx]]
            self.values.update(kvs)
            # If the preset overrode any gradient/agradient param, re-parse
            # its wire string so the stop list (and hence the rendered
            # rows) match the new value. Then re-expand params and
            # recompose the canonical wire string back into self.values.
            grad_changed = False
            for p in self._raw_params:
                if (p["type"] in ("gradient", "agradient")
                        and p["name"] in kvs):
                    self.gradient_stops[p["name"]] = _parse_gradient_stops(
                        kvs[p["name"]])
                    self.values[p["name"]] = _compose_gradient(
                        self.gradient_stops[p["name"]])
                    grad_changed = True
            if grad_changed:
                self.params = self._expand_params()
                # Selection may now point past the end if a gradient grew
                # shorter; clamp it.
                self.selected_idx = min(self.selected_idx,
                                        max(0, self.n_rows - 1))
            # Push only the keys the preset touched, but use the
            # recomposed wire form for any gradients we just re-parsed
            # so the animation receives a canonical string.
            if self.live:
                push_kvs = {k: self.values.get(k, v) for k, v in kvs.items()}
                self.animation.push_params(push_kvs)
            return
        # Param rows: enter text-edit mode for the current value.
        p = self.selected_param()
        if p is None:
            return
        # Choices-style settings (color_format) don't enter edit mode —
        # they cycle via ←/→ only.
        if p.get("_choices"):
            return
        self.editing = True
        self.edit_cursor = 0
        self.edit_auto = False
        ptype = p["type"]
        # Stop rows store their live color in gradient_stops, not values
        # (which holds the recomposed wire string for the parent param).
        if self._is_grad_stop(p):
            cur = self._stop(p)["color"]
        else:
            cur = str(self.values.get(p["name"], p["default"]))
        if ptype in ("argb", "rgb"):
            want_len = 8 if ptype == "argb" else 6
            if len(cur) != want_len:
                cur = "0" * want_len
            if self._color_format() == "decimal":
                # Multi-field per-channel editing: parse the hex into
                # one labeled field per channel; arrows move between
                # fields, digits append to the current field.
                self.edit_fields = [str(int(cur[i:i+2], 16))
                                    for i in range(0, want_len, 2)]
                self.edit_field_idx = 0
                self.edit_mode = "multi_field"
                self.edit_touched = set()
            else:
                # Fixed-position overwrite editing of the canonical hex.
                self.edit_buffer = cur
                self.edit_mode = "overwrite"
                self.edit_cursor = 0
        elif ptype == "bool":
            # Show the readable form so the user has context. Typing 't'
            # or 'f' fully replaces the buffer (see _handle_append_key).
            self.edit_buffer = "true" if cur in ("1", "true", "True") else "false"
            self.edit_mode = "append"
        elif p.get("harness") and not p.get("_setting"):
            # Auto-capable harness param: start blank with a placeholder.
            self.edit_buffer = ""
            self.edit_mode = "append"
            self.edit_auto = True
        else:
            self.edit_buffer = cur
            self.edit_mode = "append"

    def _activate_grad_pos_edit(self, p, prefill=""):
        """Open a numeric editor targeting the focused stop's position.
        `prefill` lets the user start from a typed digit so a single
        keystroke transitions cleanly into the editor (spreadsheet
        behaviour — typing 7 on a stop row immediately starts entering
        a new position beginning with 7)."""
        if p["_grad_endpoint"]:
            return  # endpoints are position-locked
        self.editing = True
        self.edit_cursor = 0
        self.edit_auto = False
        self.edit_mode = "grad_pos"
        self.edit_buffer = prefill

    def _handle_edit_key(self, ev):
        p = self.selected_param()
        if p is None:
            self.editing = False
            return True

        if ev.key == pygame.K_ESCAPE:
            self.editing = False
            return True

        if ev.key == pygame.K_RETURN:
            self._commit_edit(p)
            return True

        if self.edit_mode == "overwrite":
            return self._handle_overwrite_key(ev, p)
        if self.edit_mode == "multi_field":
            return self._handle_multi_field_key(ev, p)
        if self.edit_mode == "grad_pos":
            return self._handle_grad_pos_key(ev, p)
        return self._handle_append_key(ev, p)

    def _commit_edit(self, p):
        raw = self.edit_buffer
        # Position-edit on a gradient stop has its own commit path: we
        # parse one integer, validate against the neighbors, write back
        # to the parsed stop list, and recompose the wire string.
        if self.edit_mode == "grad_pos":
            self._commit_grad_pos(p, raw)
            self.editing = False
            return
        # Empty + auto-capable = "auto" sentinel.
        if self.edit_auto and raw == "":
            canonical = "auto"
        elif self.edit_mode == "multi_field":
            # Compose hex from the per-channel decimal fields. Empty
            # fields default to 0 so the user can leave channels blank.
            try:
                vals = [int(f or "0") for f in self.edit_fields]
                if any(v < 0 or v > 255 for v in vals):
                    raise ValueError
                canonical = "".join("{:02x}".format(v) for v in vals)
            except (ValueError, TypeError):
                self.editing = False
                return
        elif p["type"] == "bool":
            # Accept the human-readable forms, store the protocol form.
            low = raw.strip().lower()
            if low in ("true", "1", "t"):
                canonical = "1"
            elif low in ("false", "0", "f", ""):
                canonical = "0"
            else:
                self.editing = False
                return
        else:
            canonical = raw
        if _validate_value(p, canonical):
            if self._is_grad_stop(p):
                # Stop color edit: write into the parsed list and recompose
                # the parent gradient. self.values[name] holds the wire
                # string for the whole gradient, not this individual stop.
                self._stop(p)["color"] = canonical
                self._sync_gradient_value(p["_grad_param_name"])
            else:
                self.values[p["name"]] = canonical
                self._apply(p, canonical)
        self.editing = False

    def _commit_grad_pos(self, p, raw):
        if not self._is_grad_stop(p) or p["_grad_endpoint"]:
            return
        try:
            new_pos = int(raw)
        except (TypeError, ValueError):
            return
        name = p["_grad_param_name"]
        stops = self.gradient_stops[name]
        idx = p["_grad_stop_idx"]
        prev_pos = stops[idx - 1]["pos"]
        next_pos = stops[idx + 1]["pos"]
        if not (prev_pos < new_pos < next_pos):
            return  # would collide with a neighbor — refuse silently
        stops[idx]["pos"] = new_pos
        self._sync_gradient_value(name)

    def _handle_grad_pos_key(self, ev, p):
        if ev.key == pygame.K_BACKSPACE:
            self.edit_buffer = self.edit_buffer[:-1]
            return True
        ch = ev.unicode
        if ch and ch.isdigit() and len(self.edit_buffer) < 3:
            # 1..3 digits is enough to express any 1..99 (or even 100, but
            # endpoints are excluded above so values cap at 99). Reject
            # anything that would obviously overshoot 100.
            new = self.edit_buffer + ch
            if int(new) <= 100:
                self.edit_buffer = new
        return True

    def _handle_append_key(self, ev, p):
        if ev.key == pygame.K_BACKSPACE:
            self.edit_buffer = self.edit_buffer[:-1]
            return True
        ch = ev.unicode
        if not ch:
            return True
        if not _can_type(p, self.edit_buffer, ch, "append", self._color_format()):
            return True
        # Bool keystrokes auto-expand to the full word so the user sees
        # the intent immediately and the value is unambiguous on commit.
        if p["type"] == "bool" and ch in "tTfF":
            self.edit_buffer = "true" if ch in "tT" else "false"
        else:
            self.edit_buffer += ch
        return True

    def _handle_multi_field_key(self, ev, p):
        # Decimal color editing across N labeled sub-fields. Left/Right
        # navigate between fields (values are append-only, no within-
        # field cursor motion). Digits append to the current field;
        # backspace removes the last digit, or jumps left when empty.
        # Each field is "pristine" until it's been touched: the first
        # keystroke (digit or backspace) replaces the existing value
        # wholesale, so the user can simply type a new number over a
        # selected field without backspacing first.
        idx = self.edit_field_idx
        if ev.key == pygame.K_LEFT:
            self.edit_field_idx = max(0, idx - 1)
            return True
        if ev.key == pygame.K_RIGHT:
            self.edit_field_idx = min(len(self.edit_fields) - 1, idx + 1)
            return True
        if ev.key == pygame.K_BACKSPACE:
            if idx not in self.edit_touched:
                self.edit_fields[idx] = ""
                self.edit_touched.add(idx)
            else:
                cur = self.edit_fields[idx]
                if cur:
                    self.edit_fields[idx] = cur[:-1]
                elif idx > 0:
                    self.edit_field_idx -= 1
            return True
        ch = ev.unicode
        if ch and ch.isdigit():
            if idx not in self.edit_touched:
                cur = ""
                self.edit_touched.add(idx)
            else:
                cur = self.edit_fields[idx]
            new = cur + ch
            # Reject anything that pushes the channel out of range or
            # past 3 digits — keeps every keystroke representable as a
            # single 0..255 byte.
            if len(new) <= 3 and int(new) <= 255:
                self.edit_fields[idx] = new
                # Auto-advance once the field is full so the user can
                # type a continuous "255 0 128" without arrow keys.
                if len(new) == 3 and idx < len(self.edit_fields) - 1:
                    self.edit_field_idx = idx + 1
        return True

    def _handle_overwrite_key(self, ev, p):
        # Hex fields are fixed-length; ←/→/Home/End move within them and
        # backspace/delete are no-ops (the user is overwriting characters,
        # not deleting them).
        if ev.key == pygame.K_LEFT:
            self.edit_cursor = max(0, self.edit_cursor - 1)
            return True
        if ev.key == pygame.K_RIGHT:
            self.edit_cursor = min(len(self.edit_buffer) - 1, self.edit_cursor + 1)
            return True
        if ev.key == pygame.K_HOME:
            self.edit_cursor = 0
            return True
        if ev.key == pygame.K_END:
            self.edit_cursor = len(self.edit_buffer) - 1
            return True
        if ev.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            return True
        ch = ev.unicode
        if ch and _can_type(p, self.edit_buffer, ch, "overwrite", self._color_format()):
            buf = list(self.edit_buffer)
            if self.edit_cursor < len(buf):
                buf[self.edit_cursor] = ch
                self.edit_buffer = "".join(buf)
                self.edit_cursor = min(len(self.edit_buffer) - 1, self.edit_cursor + 1)
        return True

    def on_animation_restart(self, animation):
        """Called when the user presses `r`. We re-bind to the new
        subprocess and immediately push our local values so the restart
        starts from the edits the user already made (important for
        animations with parammode static)."""
        self.animation = animation
        if self.values:
            animation.push_params(self.values)

    # --- Rendering ---------------------------------------------------------

    def render(self, screen, panel_rect):
        # Fully opaque so text stays readable regardless of what the
        # animation is doing underneath.
        pygame.draw.rect(screen, (10, 10, 14), panel_rect)
        pygame.draw.line(screen, (60, 60, 70),
                         (panel_rect.left, panel_rect.top),
                         (panel_rect.left, panel_rect.bottom), 1)

        title_font = self.fonts.get(15)
        row_font = self.fonts.get(13)
        help_font = self.fonts.get(11)

        x = panel_rect.left + self.MARGIN

        # --- Header (fixed at the top) ----------------------------------
        y = panel_rect.top + self.MARGIN
        title = self.info["name"]
        mode = "live" if self.live else "static (restart with r)"
        screen.blit(title_font.render(title, True, (220, 220, 240)), (x, y))
        y += title_font.get_height()
        screen.blit(help_font.render(mode, True, (130, 130, 150)), (x, y))
        y += help_font.get_height() + 6
        content_top = y

        # --- Footer text (computed now, drawn last) ---------------------
        # Reserving its space up-front lets us figure out how many param
        # rows fit in the middle and scroll the rest.
        if self.editing:
            p = self.selected_param()
            if self.edit_mode == "grad_pos":
                footer_lines = ["EDITING stop position (1..99)",
                                "type digits, enter commits"]
            else:
                footer_lines = ["EDITING — type new value"]
                if p is not None and p["type"] in ("argb", "rgb"):
                    if self.edit_mode == "multi_field":
                        footer_lines.append("←→  move between channels")
                    else:
                        footer_lines.append("({} hex format)".format(
                            "ARGB" if p["type"] == "argb" else "RGB"))
                        footer_lines.append("←→  move within field")
            footer_lines += ["enter  commit",
                             "esc    cancel",
                             "(• rows accept the literal 'auto')"]
        else:
            color_fmt = self.harness_state.get("color_format", "hex")
            p = self.selected_param()
            if self._is_grad_stop(p):
                footer_lines = ["↑↓  select stop"]
                if not p["_grad_endpoint"]:
                    footer_lines += ["←→  nudge position ±1",
                                     "0-9  enter position",
                                     "+    insert stop after",
                                     "-    remove stop"]
                else:
                    footer_lines += ["+    insert stop near here",
                                     "(endpoint position locked)"]
                footer_lines += ["b    rebalance positions",
                                 "enter  edit color",
                                 "c    color format ({})".format(color_fmt),
                                 "tab  close"]
            else:
                footer_lines = ["↑↓  select",
                                "←→  adjust  /  cycle preset",
                                "enter  edit value  /  apply preset",
                                "del  reset • row to auto",
                                "c    color format ({})".format(color_fmt),
                                "tab  close"]
        footer_h = len(footer_lines) * (help_font.get_height() + 2) + self.MARGIN
        content_bottom = panel_rect.bottom - footer_h

        # --- Scroll bookkeeping -----------------------------------------
        content_h = max(0, content_bottom - content_top)
        visible_count = max(1, content_h // self.LINE_H)
        # Keep the selected row in view: snap to its position if it has
        # drifted off either edge.
        if self.selected_idx < self.scroll_top:
            self.scroll_top = self.selected_idx
        elif self.selected_idx >= self.scroll_top + visible_count:
            self.scroll_top = self.selected_idx - visible_count + 1
        max_scroll = max(0, self.n_rows - visible_count)
        self.scroll_top = max(0, min(self.scroll_top, max_scroll))

        # --- Visible rows -----------------------------------------------
        y = content_top
        end = min(self.n_rows, self.scroll_top + visible_count)
        for i in range(self.scroll_top, end):
            if i < self.n_param_rows:
                self._render_param_row(screen, panel_rect, x, y, i,
                                       self.params[i], row_font)
            else:
                self._render_preset_row(screen, panel_rect, x, y, row_font)
            y += self.LINE_H

        # --- Scrollbar (only when there's something off-screen) ---------
        if self.n_rows > visible_count:
            self._render_scrollbar(screen, panel_rect,
                                   content_top, content_bottom, visible_count)

        # --- Footer (fixed at the bottom) -------------------------------
        footer_y = content_bottom + 4
        for line in footer_lines:
            screen.blit(help_font.render(line, True, (140, 140, 160)),
                        (x, footer_y))
            footer_y += help_font.get_height() + 2

    def _render_scrollbar(self, screen, panel_rect, top, bottom, visible_count):
        bar_x = panel_rect.right - 5
        bar_w = 3
        bar_h = bottom - top
        if bar_h <= 0:
            return
        pygame.draw.rect(screen, (40, 40, 50),
                         (bar_x, top, bar_w, bar_h), border_radius=1)
        # Thumb height proportional to the fraction of rows visible.
        thumb_h = max(16, int(bar_h * visible_count / self.n_rows))
        denom = max(1, self.n_rows - visible_count)
        thumb_y = top + int((bar_h - thumb_h) * self.scroll_top / denom)
        pygame.draw.rect(screen, (140, 140, 160),
                         (bar_x, thumb_y, bar_w, thumb_h),
                         border_radius=1)

    def _format_for_display(self, p, val):
        s = str(val)
        if s.lower() == "auto":
            return "auto"
        if p["type"] == "bool":
            return "true" if s in ("1", "true", "True") else "false"
        if p["type"] in ("argb", "rgb") and self._color_format() == "decimal":
            # Display is plain CSV (`255,128,0`); the per-channel labels
            # only appear while editing, where the user actually needs
            # to know which slot the cursor is in.
            try:
                want_len = 8 if p["type"] == "argb" else 6
                return _hex_to_csv(s, want_len)
            except (ValueError, TypeError):
                pass
        return s

    def _render_param_row(self, screen, panel_rect, x, y, i, p, font):
        selected = (i == self.selected_idx and not self.is_preset_row())
        if selected:
            row = pygame.Rect(panel_rect.left + 4, y - 2,
                              panel_rect.width - 8, self.LINE_H)
            pygame.draw.rect(screen, (40, 70, 110), row, border_radius=3)
        if self._is_grad_stop(p):
            self._render_grad_stop_row(screen, panel_rect, x, y, p, font, selected)
            return
        # Harness-only params get a leading dot marker so the user can tell
        # at a glance which knobs are hidden tunables vs. regular ckb-next
        # params shared with the GUI.
        prefix = "• " if p.get("harness") else "  "
        name_color = (180, 220, 255) if p.get("harness") else (200, 200, 220)
        screen.blit(font.render(prefix + p["name"], True, name_color), (x, y))

        right_edge = panel_rect.right - self.MARGIN
        if self.editing and selected:
            self._render_edit_value(screen, font, right_edge, y)
        else:
            val = self.values.get(p["name"], p["default"])
            text = self._format_for_display(p, val)
            color = (140, 160, 200) if str(val).lower() == "auto" else (255, 255, 255)
            max_chars = 18
            if len(text) > max_chars:
                text = "…" + text[-(max_chars - 1):]
            surf = font.render(text, True, color)
            screen.blit(surf, (right_edge - surf.get_width(), y))

    def _render_edit_value(self, screen, font, right_edge, y):
        """Right-aligned editor with mode-specific cursor.

        - append:      buffer + vertical caret at the end (or faint 'auto'
                       placeholder + caret-at-zero for auto-capable rows).
        - overwrite:   full hex buffer with an underscore beneath the
                       character at edit_cursor.
        - multi_field: labeled per-channel fields, current one in the
                       edit color with a caret at its end.
        """
        edit_color = (255, 240, 130)
        placeholder_color = (110, 110, 130)
        dim = (170, 170, 190)

        if self.edit_mode == "multi_field":
            p = self.selected_param()
            labels = (["A", "R", "G", "B"] if p["type"] == "argb"
                      else ["R", "G", "B"])
            label_w = font.size("R")[0]
            digit_w = font.size("0")[0]
            gap = font.size(" ")[0] * 2
            # Reserve max width per slot ("Lxxx") so adjacent fields don't
            # shift around as the user types — feels stable.
            slot_w = label_w + 1 + 3 * digit_w
            total_w = slot_w * len(labels) + gap * (len(labels) - 1)
            x = right_edge - total_w
            for i, label in enumerate(labels):
                val = self.edit_fields[i] or ""
                is_sel = (i == self.edit_field_idx)
                screen.blit(font.render(label, True, dim), (x, y))
                vx = x + label_w + 1
                val_color = edit_color if is_sel else (255, 255, 255)
                screen.blit(font.render(val, True, val_color), (vx, y))
                if is_sel:
                    cur_x = vx + font.size(val)[0]
                    pygame.draw.line(screen, edit_color,
                                     (cur_x, y), (cur_x, y + font.get_height()), 1)
                x += slot_w + gap
            return

        if self.edit_auto and self.edit_buffer == "":
            text_surf = font.render("auto", True, placeholder_color)
            text_x = right_edge - text_surf.get_width()
            screen.blit(text_surf, (text_x, y))
            pygame.draw.line(screen, edit_color,
                             (text_x, y), (text_x, y + font.get_height()), 1)
            return

        text = self.edit_buffer or " "
        text_surf = font.render(text, True, edit_color)
        text_x = right_edge - text_surf.get_width()
        screen.blit(text_surf, (text_x, y))

        if self.edit_mode == "overwrite" and self.edit_buffer:
            cur = self.edit_cursor
            ch_x = text_x + font.size(self.edit_buffer[:cur])[0]
            ch_w = font.size(self.edit_buffer[cur])[0]
            ul_y = y + font.get_height() - 2
            pygame.draw.line(screen, edit_color,
                             (ch_x, ul_y), (ch_x + ch_w, ul_y), 2)
        else:
            cursor_x = text_x + font.size(self.edit_buffer)[0]
            pygame.draw.line(screen, edit_color,
                             (cursor_x, y), (cursor_x, y + font.get_height()), 1)

    # Fixed widths used for stop-row layout. Derived from font metrics in
    # `_grad_layout_widths` so the position-bracket column lands at the
    # same x for every stop regardless of the value to its right.

    def _grad_layout_widths(self, font, has_alpha):
        # Color slot is wide enough for the worst case across both
        # display formats AND the multi-field editor:
        #   - decimal display: '255,255,255,255' (15 chars argb)
        #   - hex display:     'ffffffff' (8)
        #   - multi-field edit: 4 × (label + 3 digits) + 3 gaps
        # Taking the max keeps the position column from shifting when
        # the row enters edit mode.
        decimal_w = font.size("255,255,255,255" if has_alpha
                              else "255,255,255")[0]
        label_w = font.size("R")[0]
        digit_w = font.size("0")[0]
        gap_w = font.size("  ")[0]
        n_chan = 4 if has_alpha else 3
        slot_w = label_w + 1 + 3 * digit_w
        multi_w = slot_w * n_chan + gap_w * (n_chan - 1)
        color_slot = max(decimal_w, multi_w)
        pos_slot = font.size("[100%]")[0]
        return color_slot, pos_slot, gap_w

    def _render_grad_stop_row(self, screen, panel_rect, x, y, p, font, selected):
        """Stop row: param name on the first stop, then a fixed-column
        `[pos%]  color` block. The position-bracket column is pinned via
        the reserved color slot width so brackets align vertically across
        stops even as values change width (hex vs. decimal CSV)."""
        right_edge = panel_rect.right - self.MARGIN
        color_slot_w, pos_slot_w, gap_w = self._grad_layout_widths(
            font, p["_grad_alpha"])
        pos_col_x = right_edge - color_slot_w - gap_w - pos_slot_w
        if p["_grad_first_visual"]:
            # Bare param name on the first stop row is the only header —
            # the stop count is implicit in the rows below. Keeping it
            # short avoids overlapping the pinned position column on
            # narrow panels.
            screen.blit(font.render(p["name"], True, (200, 200, 220)),
                        (x, y))

        # Position-edit mode: the bracket itself is the editor; skip the
        # static position render and pass the pinned column to the
        # specialized renderer (which still shows the color dimmed
        # alongside).
        if self.editing and selected and self.edit_mode == "grad_pos":
            self._render_grad_pos_edit(screen, font, p, pos_col_x,
                                       right_edge, y)
            return

        pos = self._stop(p)["pos"]
        pos_text = "[{:>3d}%]".format(pos)
        pos_color = (160, 200, 220) if p["_grad_endpoint"] else (220, 220, 230)
        screen.blit(font.render(pos_text, True, pos_color), (pos_col_x, y))

        # While editing the color, suppress the static color value and
        # let the existing edit renderer (overwrite hex / multi-field
        # decimal) own the slot. The pinned position bracket above
        # stays visible so the user still knows which stop they're on.
        if self.editing and selected:
            self._render_edit_value(screen, font, right_edge, y)
            return

        color_val = self._stop(p)["color"]
        # Non-alpha "gradient" types still have 8 hex chars on the wire,
        # but the C parser overwrites alpha to 255 — so show the user-
        # meaningful 6 hex (or RGB decimal) only.
        display_val = color_val if p["_grad_alpha"] else color_val[2:]
        text = self._format_for_display(
            {"type": "argb" if p["_grad_alpha"] else "rgb"}, display_val)
        color_surf = font.render(text, True, (255, 255, 255))
        screen.blit(color_surf, (right_edge - color_surf.get_width(), y))

    def _render_grad_pos_edit(self, screen, font, p, pos_col_x, right_edge, y):
        """Position editor: typed digits appear inside the bracket at the
        pinned position column; the stop's color stays visible (dimmed)
        on the right so the user keeps context for which stop they're
        editing."""
        edit_color = (255, 240, 130)
        text = "[{:>3s}%]".format(self.edit_buffer or "")
        screen.blit(font.render(text, True, edit_color), (pos_col_x, y))
        # Caret follows the typed digits inside the bracket.
        caret_x = pos_col_x + font.size("[" + " " * (3 - len(self.edit_buffer))
                                        + (self.edit_buffer or ""))[0]
        pygame.draw.line(screen, edit_color,
                         (caret_x, y), (caret_x, y + font.get_height()), 1)
        # Color stays visible (dimmed) at the right slot.
        color_val = self._stop(p)["color"]
        if not p["_grad_alpha"]:
            color_val = color_val[2:]
        color_text = self._format_for_display(
            {"type": "argb" if p["_grad_alpha"] else "rgb"}, color_val)
        color_surf = font.render(color_text, True, (170, 170, 190))
        screen.blit(color_surf, (right_edge - color_surf.get_width(), y))

    def _render_preset_row(self, screen, panel_rect, x, y, font):
        selected = self.is_preset_row()
        if selected:
            row = pygame.Rect(panel_rect.left + 4, y - 2,
                              panel_rect.width - 8, self.LINE_H)
            pygame.draw.rect(screen, (40, 70, 110), row, border_radius=3)
        screen.blit(font.render("preset", True, (200, 200, 220)), (x, y))
        cur = self.preset_names[self.preset_idx] if self.preset_names else "—"
        text = "◀ {} ▶".format(cur) if selected else cur
        surf = font.render(text, True, (255, 255, 255))
        screen.blit(surf, (panel_rect.right - self.MARGIN - surf.get_width(), y))


def _format_float(v):
    """Compact representation that round-trips to the same float."""
    s = "{:.4g}".format(v)
    # Avoid losing the decimal point for whole values; keeps types
    # visually consistent in the panel.
    if "." not in s and "e" not in s:
        s += ".0"
    return s


def _rgb_to_hex(rgb):
    return "{:02x}{:02x}{:02x}".format(*rgb)


def _hex_to_rgb(s):
    """Accepts the same forms as parse_base_color."""
    return parse_base_color(s)


def _hex_to_csv(hex_str, expected_len):
    """'ff8800' (rgb) → '255,136,0'; 'ffaabbcc' (argb) → '255,170,187,204'."""
    s = hex_str.lstrip("#")
    if len(s) != expected_len:
        raise ValueError("expected {} hex chars".format(expected_len))
    chunks = [int(s[i:i+2], 16) for i in range(0, len(s), 2)]
    return ",".join(str(v) for v in chunks)


def _csv_to_hex(csv_str, expected_count):
    """'255,136,0' (3) → 'ff8800'. Components must be 0..255."""
    parts = [int(x.strip()) for x in csv_str.split(",")]
    if len(parts) != expected_count:
        raise ValueError("expected {} components".format(expected_count))
    if any(not 0 <= v <= 255 for v in parts):
        raise ValueError("components must be 0..255")
    return "".join("{:02x}".format(v) for v in parts)


# Context-aware input filtering. We don't just check whether `ch` is in
# a per-type allow-list — we also consider what's already in the buffer
# so the user can never construct a malformed value mid-edit ("--", "1.2.3",
# "1e2e3", etc.). The mode argument distinguishes overwrite (hex fields,
# fixed-length, each position independent) from append (numerics and
# decimal-CSV colors, suffix-only typing).

def _can_type(p, buffer, ch, mode, color_format):
    """True if `ch` may be inserted into the editor for this field.
    Append-mode rejects any character that would make `buffer + ch` an
    impossible-to-complete value; overwrite-mode just checks the
    character is type-valid (each cell is independent)."""
    ptype = p["type"]
    if ptype == "string":
        return ch.isprintable()

    if mode == "overwrite":
        if ptype in ("rgb", "argb"):
            return ch in "0123456789abcdefABCDEF"
        return False

    # Append mode below — buffer is the prefix typed so far.
    if ptype in ("rgb", "argb") and p.get("_setting") and color_format == "decimal":
        if ch.isdigit():
            # Optionally cap the current segment at 3 chars (255 has 3
            # digits) to keep values in range and avoid 12345 nonsense.
            seg_start = buffer.rfind(",") + 1
            return len(buffer) - seg_start < 3
        if ch == ",":
            max_commas = 3 if ptype == "argb" else 2
            # Don't allow leading comma, trailing comma after another, or
            # more separators than the format takes.
            if not buffer or buffer.endswith(","):
                return False
            return buffer.count(",") < max_commas
        return False

    if ptype == "bool":
        # 't'/'f' (case-insensitive) always replace the buffer, no matter
        # what's already in it; the editor expands the keystroke to the
        # full word "true"/"false".
        return ch in "tTfF"

    if ptype == "long":
        if ch.isdigit():
            return True
        if ch == "-":
            return len(buffer) == 0
        return False

    if ptype == "double":
        if ch.isdigit():
            return True
        if ch == ".":
            # Only one decimal point, and only in the current mantissa
            # segment (i.e. before any 'e').
            if "." in buffer:
                return False
            mantissa = buffer.split("e")[0].split("E")[0]
            return "." not in mantissa
        if ch in "-+":
            # Leading sign, or sign immediately after the exponent marker.
            if len(buffer) == 0:
                return True
            if buffer[-1] in "eE":
                return True
            return False
        if ch in "eE":
            # Single exponent, and only after at least one digit so far.
            if "e" in buffer.lower():
                return False
            return any(c.isdigit() for c in buffer)
        return False

    return False


def _validate_value(p, value):
    """Final sanity check at commit. `value` is the canonical form
    (hex for color fields, plain for everything else)."""
    if p.get("harness") and not p.get("_setting") and value.lower() == "auto":
        return True
    if p.get("_choices"):
        return value in p["_choices"]
    ptype = p["type"]
    try:
        if ptype == "double":
            v = float(value)
            if "min" in p and v < float(p["min"]):
                return False
            if "max" in p and v > float(p["max"]):
                return False
        elif ptype == "long":
            v = int(value)
            if "min" in p and v < int(float(p["min"])):
                return False
            if "max" in p and v > int(float(p["max"])):
                return False
        elif ptype == "bool":
            if value not in ("0", "1", "true", "false", "True", "False"):
                return False
        elif ptype in ("argb", "rgb"):
            int(value, 16)
            if len(value) != (8 if ptype == "argb" else 6):
                return False
        # string: anything goes
    except (TypeError, ValueError):
        return False
    return True


# === Main ==================================================================

def _resolve_animation(arg):
    """Resolves a bare animation name against the standard ckb-next
    install dirs, so users can say `random` instead of a full path.
    Anything containing a path separator or starting with '.' / '/'
    is returned untouched."""
    if os.sep in arg or arg.startswith("."):
        return os.path.abspath(arg)
    for base in ("/usr/lib/ckb-next-animations",
                 os.path.expanduser("~/.local/share/ckb-next/animations")):
        cand = os.path.join(base, arg)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return os.path.abspath(arg)  # let the caller error out cleanly


def main():
    ap = argparse.ArgumentParser(
        description="Run any ckb-next animation offline and render its "
                    "output to a pygame window. Drives the animation as a "
                    "subprocess over the standard ckb-next stdin/stdout "
                    "protocol. Parameters and presets are tuned in-app — "
                    "press Tab once running.")
    ap.add_argument("animation",
                    help="path or name of the animation. Bare names are "
                         "resolved against /usr/lib/ckb-next-animations/ "
                         "and ~/.local/share/ckb-next/animations/.")
    ap.add_argument("--keymap", default=keymap_path(),
                    help="JSON keymap captured by tools/dump_keymap "
                         "(default: ~/.cache/ckb-next/keymap.json)")
    ap.add_argument("--base", default=None, metavar="R,G,B|RRGGBB",
                    help="base ('idle') key color the animation composites "
                         "over. Persisted to ~/.cache/ckb-next/test_visual.json "
                         "so you only need to pass it once.")
    ap.add_argument("--bg", default=None, metavar="R,G,B|RRGGBB",
                    help="window background color drawn around the keyboard. "
                         "Persisted alongside --base.")
    ap.add_argument("--list-params", action="store_true",
                    help="print the animation's parameters / presets / "
                         "harness extras and exit, without launching the "
                         "window.")
    ap.add_argument("--window", default="1400x460",
                    help="window size as WxH (default 1400x460)")
    args = ap.parse_args()

    exe = _resolve_animation(args.animation)
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        sys.stderr.write("animation not found or not executable: {}\n".format(exe))
        return 2

    info = discover_animation_info(exe)

    if args.list_params:
        print(format_param_listing(info))
        return 0

    if not os.path.exists(args.keymap):
        sys.stderr.write(
            "No keymap at {}.\n"
            "Run tools/dump_keymap once from the ckb-next GUI to capture "
            "your layout (see README).\n".format(args.keymap))
        sys.stderr.write(
            "(Set XDG_CACHE_HOME or pass --keymap to use a different path.)\n")
        return 2

    settings = load_settings()
    dirty = False
    if args.base:
        try:
            base_color = parse_base_color(args.base)
        except ValueError as e:
            sys.stderr.write("--base: {}\n".format(e))
            return 2
        settings["base_color"] = list(base_color)
        dirty = True
    else:
        base_color = tuple(settings.get("base_color", [0, 0, 0]))
    if args.bg:
        try:
            bg_color = parse_base_color(args.bg)
        except ValueError as e:
            sys.stderr.write("--bg: {}\n".format(e))
            return 2
        settings["bg_color"] = list(bg_color)
        dirty = True
    else:
        bg_color = tuple(settings.get("bg_color", list(DEFAULT_BG)))
    if dirty:
        save_settings(settings)

    keys = load_keymap(args.keymap)
    win_w, win_h = (int(x) for x in args.window.lower().split("x"))

    pygame.init()
    # Standard typematic-repeat: 400ms delay, then a fresh KEYDOWN every
    # 40ms while the key is held. Lets ↑/↓ in the param panel, +/-
    # speed scrubbing, and key forwarding in interactive mode all behave
    # like a normal keyboard — hold to repeat instead of pressing once
    # per row.
    pygame.key.set_repeat(400, 40)
    pygame.display.set_caption("ckb-next visual harness — {}".format(info["name"]))
    screen = pygame.display.set_mode((win_w, win_h))
    clock = pygame.time.Clock()
    fonts = FontCache()
    hud_font = fonts.get(14)

    default_w, default_h = compute_defaults(keys)
    layout = compute_cap_layout(keys, default_w, default_h)
    laid = fit_keys_to_window(keys, layout, (win_w, win_h))

    # Speed control is exposed only when the animation opts in through a
    # `harness speed default=...` line in its --ckb-info.
    speed_decl = info["harness"].get("speed")
    speed_enabled = speed_decl is not None
    initial_speed = float(speed_decl["default"]) if speed_enabled and "default" in speed_decl else 1.0
    speed = initial_speed

    # Bundled ckb-next animations expect the daemon to feed every declared
    # param back during the params block — the C framework only computes
    # derived state inside the param-handler callbacks. Wave, for instance,
    # only knows the keyboard's width/animation start point after its
    # `angle` param has been parsed, so skipping any default leaves the
    # animation in a half-initialised state that emits nothing.
    #
    # Build the initial param set from declared defaults first, then layer
    # the chosen preset on top (presets often omit values they don't need
    # to override). Some animations also rely on daemon-level knobs that
    # only appear in a preset — e.g. rain's `trigger=1 kptrigger=1` makes
    # it run continuously — so we still need the preset values; we just
    # don't want to lose the rest of the defaults.
    default_preset = {p["name"]: url_decode(p["default"])
                      for p in info["params"] if p["type"] != "label"}
    if "Default" in info["presets"]:
        default_preset.update(info["presets"]["Default"])
    elif info["presets"]:
        default_preset.update(next(iter(info["presets"].values())))

    anim = Animation(exe, keys, params=default_preset)

    # Mutable state shared with the panel: edits to base/bg/color_format
    # from the in-app editor land here and are picked up on the next
    # frame's render. The panel also persists changes via the callback.
    color_format = settings.get("color_format", "hex")
    harness_state = {
        "base_color": base_color,
        "bg_color": bg_color,
        "color_format": color_format,
    }

    def on_setting_changed(name, value):
        if name in ("base_color", "bg_color"):
            harness_state[name] = tuple(value)
            settings[name] = list(value)
        else:
            harness_state[name] = value
            settings[name] = value
        save_settings(settings)

    panel = ParamPanel(info, anim, fonts,
                       harness_state=harness_state,
                       on_setting_changed=on_setting_changed,
                       initial_values=default_preset)
    panel_open = False
    paused = False
    interactive = False
    running = True

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
                continue

            # Mouse click (any button — but not scroll-wheel events,
            # which pygame also reports as MOUSEBUTTONDOWN with button 4/5)
            # toggles interactive mode: while on, every keystroke is
            # forwarded to the animation as `key NAME down/up`.
            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button in (1, 2, 3):
                interactive = not interactive
                continue

            if ev.type == pygame.KEYUP:
                if interactive:
                    name = pygame_to_ckb(pygame.key.name(ev.key))
                    if name:
                        anim.keypress(name, down=False)
                continue

            if ev.type != pygame.KEYDOWN:
                continue

            # In interactive mode all keys are forwarded — even Esc — so
            # the only way out is another mouse click. The harness's own
            # controls (tab, space, r, q, +/-) are unavailable while it's
            # on, by design.
            if interactive:
                name = pygame_to_ckb(pygame.key.name(ev.key))
                if name:
                    anim.keypress(name, down=True)
                continue

            # Tab toggles the panel.
            if ev.key == pygame.K_TAB:
                panel_open = not panel_open
                continue
            # When the panel is open, it gets first dibs on input.
            if panel_open and panel.handle_keydown(ev):
                continue
            # Global controls.
            if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                if panel_open:
                    panel_open = False  # Esc closes panel rather than quitting
                else:
                    running = False
            elif ev.key == pygame.K_SPACE:
                paused = not paused
            elif ev.key == pygame.K_r:
                anim.stop()
                # Restart with whatever the user has set in the panel
                # (panel.values starts seeded from the Default preset and
                # accumulates tweaks/preset switches as they edit), so a
                # restart preserves their adjustments instead of snapping
                # back to the animation's compiled defaults.
                anim = Animation(exe, keys, params=panel.values)
                panel.on_animation_restart(anim)
            elif speed_enabled and ev.key in (
                    pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                speed = min(speed * 1.25, 200.0)
            elif speed_enabled and ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                speed = max(speed / 1.25, 0.05)
            elif speed_enabled and ev.key == pygame.K_1:
                speed = initial_speed

        wall_dt = clock.tick(60) / 1000.0
        if not paused:
            # `time absolute` animations (brickbreaker, medical-monitor)
            # expect delta in real seconds. `time duration` animations
            # (wave, snake, ripple, pinwheel, …) expect delta as a fraction
            # of one duration cycle, so scale by the active `duration`
            # param. Without this, snake's 40s preset cycle finishes in 1s
            # of wall time — the animation runs 40× too fast.
            tick = wall_dt * speed
            if info["timemode"] == "duration":
                try:
                    duration_s = float(anim.params.get("duration", "1.0"))
                except (TypeError, ValueError):
                    duration_s = 1.0
                if duration_s > 0:
                    tick = tick / duration_s
            if not anim.step(tick):
                sys.stderr.write("{} subprocess exited\n".format(info["name"]))
                running = False

        screen.fill(harness_state["bg_color"])
        for k in laid:
            argb = anim.colors.get(k["name"], (0, 0, 0, 0))
            led = blend_argb(argb, harness_state["base_color"])
            render_key(screen, k, led)
            if k["label"]:
                surf = fonts.get(k["font_size"]).render(k["label"], True, LABEL_COLOR)
                tw, th = surf.get_size()
                screen.blit(surf, (k["cap_rect"].centerx - tw // 2,
                                   k["cap_rect"].centery - th // 2))

        # HUD: speed/fps on the left, control hints on the right. In
        # interactive mode the right block is replaced by a single
        # red banner — every other binding is forwarded to the animation.
        hud_left = "{}   fps {:>3.0f}{}".format(
            "speed {:>6.2f}x".format(speed) if speed_enabled else "real time",
            clock.get_fps(),
            "   PAUSED" if paused else "")
        screen.blit(hud_font.render(hud_left, True, (200, 200, 220)),
                    (12, win_h - 22))
        if interactive:
            help_text = "INTERACTIVE — keys → animation   click to exit"
            help_color = (240, 110, 110)
        else:
            bits = ["click interactive", "tab params", "space pause",
                    "r restart", "q quit"]
            if speed_enabled:
                bits = ["+/- speed", "1 reset"] + bits
            help_text = "   ".join(bits)
            help_color = (110, 110, 130)
        screen.blit(hud_font.render(help_text, True, help_color),
                    (win_w - 12 - hud_font.size(help_text)[0], win_h - 22))

        if panel_open and not interactive:
            panel_rect = pygame.Rect(win_w - ParamPanel.PANEL_W, 0,
                                     ParamPanel.PANEL_W, win_h - 28)
            panel.render(screen, panel_rect)

        pygame.display.flip()

    anim.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
