# Lock-mode switching (`ckb-mode-on-lock.service`)

A tiny user-level systemd service that listens for the desktop
session's lock/unlock signal on D-Bus and switches the active ckb-next
mode (and optionally profile). Use it to run a calmer (or no)
animation while you're away from the keyboard, and snap back to the
active mode on unlock.

**Cross-DE.** Listens on both
`org.freedesktop.ScreenSaver.ActiveChanged` (KDE Plasma, XFCE,
Cinnamon, MATE, …) and `org.gnome.ScreenSaver.ActiveChanged` (GNOME),
so a single config covers the common Linux desktops. No DE-specific
shim needed.

## Files

- `ckb-mode-on-lock` — Python script. Watches D-Bus and shells out to
  `ckb-next --mode <name>` (and `--profile <name>` if configured) on
  each lock/unlock event.
- `ckb-mode-on-lock.service` — systemd **user** unit that runs the
  script and restarts it on failure.
- `lock.conf.example` — sample config to copy into
  `~/.config/ckb-next/lock.conf` (alongside ckb-next-gui's own
  `ckb-next.conf`).

## Configuration

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
that exists in your ckb-next-gui setup. Set a value to `none` (or
leave it unset) to skip that switch.

## One-time setup

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

## Stop / disable

```sh
systemctl --user disable --now ckb-mode-on-lock.service
```

## Requirements

- `python3`
- `dbus-monitor` (from the `dbus` / `dbus-tools` package — already on
  any system with a working desktop)
- `ckb-next` on `$PATH` and a running ckb-next instance (the GUI, or
  `ckb-next --background`) — that's how mode/profile switches are
  applied
- `ckb-next-daemon` running (the standard ckb-next install handles
  this)

## Testing without locking your screen

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

## Troubleshooting

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
