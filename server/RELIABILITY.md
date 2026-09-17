# Server reliability layer (optional)

This directory's `mpd.conf.example` + `client/music` alone give you a working
jukebox. The files here add what a permanently-on, headless server wants:
automatic output repair, an unattended restart watchdog, and crash recovery
that resumes the queue where the music stopped. Everything is optional and
independent of the client; nothing here is required for the basic setup.

The scripts are portable bash (`mpc`, `flock`, `logger`, coreutils) and work
with per-user or system MPD; the shipped units assume a system service.

## What you get

| File | Purpose |
|---|---|
| `scripts/lan-jukebox-healthcheck` | Verify control port + stream port + output enabled. Repairs a disabled output or an unbound listener (MPD httpd only binds after playback starts), then restarts the MPD service if still unhealthy. |
| `scripts/lan-jukebox-recovery` | `snapshot` playback state every few seconds; `mark` on unexpected exit; `restore` resume (same queue, position, elapsed, pause state) after a crash. |
| `systemd/lan-jukebox-mpd.service` | System MPD unit: restart-always, mount dependencies, sandboxing, snapshot/restore hooks. |
| `systemd/lan-jukebox-{healthcheck,snapshot}.{service,timer}` | Watchdog every 2 min; playback snapshot every 10 s. |
| `systemd/optional/*.conf` | Drop-ins: io_uring workaround (MPD 0.24.x on some kernels), Tailscale ordering. |
| `mpd-system.conf.example` | Server profile matching the live-tested setup (sticker persistence, connection limits, software mixing, DSCP EF-class streaming). |
| `server.env.example` | Optional `/etc/lan-jukebox/server.env` overrides (ports, output name, `START_STOPPED`). |
| `tests/` | `test_helpers.py` (no MPD needed) and `integration.py` (real MPD, isolated container only — see its header). |

Design notes:

- **Restore is conservative.** Recovery only proceeds if the restored queue
  hashes identically to the snapshot; markers are consumed on first use and
  never interpreted as anything but data.
- **The watchdog does not resurrect intentionally stopped MPD** (`ExecCondition`
  on unit state; `START_STOPPED=0` additionally stops it from auto-playing a
  stopped-but-loaded queue to rebind the listener).
- **An empty queue is never a failure** — the watchdog does not restart for it.
- Scripts log to syslog with `lan-jukebox-*` tags and take no secrets.

## Install (system service, Debian/Ubuntu-style paths)

```sh
sudo install -d /etc/lan-jukebox /usr/local/libexec
sudo install -m0644 server/mpd-system.conf.example /etc/lan-jukebox/mpd.conf
sudo install -m0644 server/server.env.example /etc/lan-jukebox/server.env   # optional
sudo install -m0755 server/scripts/lan-jukebox-healthcheck \
                   server/scripts/lan-jukebox-recovery /usr/local/libexec/
sudo install -m0644 server/systemd/lan-jukebox-*.service \
                   server/systemd/lan-jukebox-*.timer /etc/systemd/system/
```

1. Edit `/etc/lan-jukebox/mpd.conf` (`music_directory`) and, if you use
   Tailscale, `sudo install -m0644
   server/systemd/optional/30-tailscale.conf
   /etc/systemd/system/lan-jukebox-mpd.service.d/`. The io_uring drop-in is
   opt-in the same way — install it only if you see the idle/CPU issue.
2. `sudo systemctl daemon-reload`
3. `sudo systemctl enable --now lan-jukebox-mpd.service
   lan-jukebox-healthcheck.timer lan-jukebox-snapshot.timer`
4. Verify:

```sh
mpc -h localhost outputs                 # output enabled
systemctl status lan-jukebox-mpd         # active
journalctl -u lan-jukebox-mpd -f         # logs instead of mpd.log
sudo journalctl -t lan-jukebox-healthcheck -t lan-jukebox-recovery
```

Notes:

- The units expect the `mpd` user/group and writable `/var/lib/lan-jukebox`
  (created via `StateDirectory`); adjust `User=`, `RequiresMountsFor=`, and
  the `*.conf` paths for other distributions or a dedicated user.
- `RequiresMountsFor=/srv/music` makes boot wait for the library mount; edit
  it to match your `music_directory`.
- The healthcheck unit needs `systemctl` authority to restart MPD — the
  shipped unit runs as root with `NoNewPrivileges`. Constrain it further
  (e.g. `polkit` rules + `DynamicUser`) if you prefer; MPD itself stays
  unprivileged.

## Testing

```sh
python3 -m unittest discover -s server/tests   # fast, no MPD needed
```

`server/tests/integration.py` drives real MPD through crash/restore/repair
scenarios. Run it only inside a disposable container (see its docstring);
the network-isolated invocation used during development:

```sh
docker run --rm --network none -v "$PWD":/repo:ro debian:trixie-slim \
  bash -c 'apt-get update -qq && apt-get install -y -qq mpd mpc python3 >/dev/null \
  && python3 /repo/server/tests/integration.py'
```

## Limitations

- Recovery is best-effort. MPD's own `state_file` already restores the queue
  on restart; this layer adds position/elapsed/pause resumption and marks
  failures, but a snapshot is at most 10 s old and restore skips if the queue
  changed.
- The io_uring drop-in documents a workaround observed with MPD 0.24.6 on one
  kernel; it is optional and harmless to omit elsewhere.
- `integration.py` is written for the isolated container above (ports 6600 /
  8000, temp dirs, no host services). Do not run it on your MPD host.
