# lan-jukebox

Play a home music library in lossless FLAC from anywhere, with one command.

`lan-jukebox` connects a homelab MPD server to any Linux client over Tailscale or LAN. You control playback with `ncmpcpp` (keyboard-driven library browser) and hear audio locally through `mpv`. Same address at home and away, no transcoding, no cloud, no web UI.

Ideal for: a FLAC collection on a home server, listened to from any device, anywhere.

## Features

- **Lossless by default:** MPD `httpd` output with FLAC encoder, tags preserved.
- **One stable address:** Tailscale IPv4 works everywhere; direct LAN path when local, seamless roaming when away.
- **LAN fallback:** if Tailscale is down at startup, raw LAN is used automatically.
- **Efficient idle:** blocks on `mpc idle` while paused instead of polling.
- **Safe single instance:** `flock` guard plus stale `mpv`/supervisor cleanup.
- **Diagnostics built in:** `music --check` tests deps, routes, MPD, and stream.
- **Optional server reliability layer:** watchdog, output repair, and crash recovery keep a headless server playing unattended. See [Reliability (optional)](#reliability-optional).

## Requirements

**Server (the machine with the music files):**

- Linux with `mpd` ≥ 0.23 and `mpc`
- Music library on local disk (FLAC, MP3, Ogg, etc. — MPD streams all as FLAC)
- Tailscale installed and logged in (for remote access), LAN access for home use

**Client (any laptop/desktop you listen from):**

- Linux with `mpv`, `ncmpcpp`, `mpc`, `iproute2`, `util-linux`, `bash`
- Tailscale installed and on the same tailnet as the server (for roaming; raw LAN alone also works at home)

> Jukebox model: MPD has one shared playback state. Multiple clients can connect and control the same queue — they do not get independent streams. This is a shared jukebox, not per-room audio.

## Quickstart

Server:

```sh
mkdir -p ~/.config/mpd ~/.local/state/mpd
cp server/mpd.conf.example ~/.config/mpd/mpd.conf
# edit music_directory and user paths
systemctl --user restart mpd
mpc -h localhost -p 6600 outputs   # expect: Output 1 (FLAC LAN Stream) is enabled
tailscale ip -4                    # note this IPv4 for clients
```

Client:

```sh
install -Dm755 client/music ~/.local/bin/music
mkdir -p ~/.config/lan-jukebox ~/.config/ncmpcpp
cp client/config.example ~/.config/lan-jukebox/config
cp client/ncmpcpp-config.example ~/.config/ncmpcpp/config
# edit both configs (see below)
music --check
music
```

Detailed steps follow.

## Repo layout

```text
lan-jukebox/
  client/
    music                    # launcher: install to ~/.local/bin/music
    config.example           # copy to ~/.config/lan-jukebox/config
    ncmpcpp-config.example   # copy to ~/.config/ncmpcpp/config
  server/
    mpd.conf.example         # copy to ~/.config/mpd/mpd.conf on the server
    mpd-system.conf.example  # optional system-service profile (RELIABILITY.md)
    server.env.example       # optional overrides for the reliability layer
    scripts/                 # optional watchdog + crash-recovery helpers
    systemd/                 # optional system units + drop-ins (RELIABILITY.md)
    tests/                   # unit tests (no MPD) + container integration test
    RELIABILITY.md           # optional server reliability layer
```

All addresses and paths in this repo are examples. Replace `100.64.0.10` with your server's tailnet IP, `192.168.50.10` with its LAN IP, `/srv/music` with your library, and `/home/USERNAME` with your server user.

## Server setup

1. **Install MPD and Tailscale.**

   ```sh
   # Debian/Ubuntu
   sudo apt install mpd mpc
   # Arch
   sudo pacman -S mpd mpc
   # Fedora
   sudo dnf install mpd mpc
   ```

   Install Tailscale from <https://tailscale.com/download> and run `sudo tailscale up`.

2. **Install the example config.**

   ```sh
   mkdir -p ~/.config/mpd ~/.local/state/mpd
   cp server/mpd.conf.example ~/.config/mpd/mpd.conf
   ```

   Edit `~/.config/mpd/mpd.conf`:

   - `music_directory` → directory with your audio files, e.g. `/srv/music`.
   - `playlist_directory`, `db_file`, `log_file`, `pid_file`, `state_file` → valid writable paths for your user.
   - `port` (`6600`) and `audio_output port` (`8000`) and `audio_output name` (`FLAC LAN Stream`) must match what clients configure.
   - `bind_to_address "0.0.0.0"` exposes MPD to LAN + tailnet. For Tailscale-only, set both occurrences to your tailnet IP (see [Security](#security)).

3. **Set permissions and start MPD.**

   The MPD user must read your library:

   ```sh
   chmod -R a+rX /srv/music
   systemctl --user enable --now mpd
   # system-wide installs instead: sudo systemctl enable --now mpd
   ```

4. **Build the library and verify.**

   ```sh
   mpc -h localhost -p 6600 update
   mpc -h localhost -p 6600 outputs
   # Output 1 (FLAC LAN Stream) is enabled
   ```

   `mpc outputs` is the primary check and works while paused. To verify audio
   bytes, start playback first, then in another terminal:

   ```sh
   mpc -h localhost -p 6600 play
   curl -s --max-time 5 http://localhost:8000 -o /tmp/stream-test.flac
   ls -l /tmp/stream-test.flac   # should grow while playing
   file /tmp/stream-test.flac    # FLAC audio data
   mpc -h localhost -p 6600 pause
   ```

   Play a test file locally if desired, then leave MPD running — clients control it remotely.

5. **Note the two addresses clients need.**

   ```sh
   tailscale ip -4                  # e.g. 100.64.0.10
   hostname -I | awk '{print $1}'   # e.g. 192.168.50.10
   ```

## Client setup

1. **Install Tailscale** (same tailnet as the server) and client tools:

   ```sh
   # Debian/Ubuntu
   sudo apt install mpv ncmpcpp mpc iproute2 util-linux
   # Arch
   sudo pacman -S mpv ncmpcpp mpc iproute2 util-linux
   # Fedora
   sudo dnf install mpv ncmpcpp mpc iproute2 util-linux
   ```

2. **Install the launcher.**

   ```sh
   install -Dm755 client/music ~/.local/bin/music
   ```

   Ensure `~/.local/bin` is on `PATH` (add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` or `~/.zshrc` if needed, then reload the shell).

3. **Install and edit configs.**

   ```sh
   mkdir -p ~/.config/lan-jukebox ~/.config/ncmpcpp
   cp client/config.example ~/.config/lan-jukebox/config
   cp client/ncmpcpp-config.example ~/.config/ncmpcpp/config
   ```

   In `~/.config/lan-jukebox/config`, set:

   ```sh
   TAILSCALE_HOST="100.64.0.10"    # output of `tailscale ip -4` on the server
   LAN_HOST="192.168.50.10"        # server LAN IP
   MPD_PORT="6600"
   STREAM_PORT="8000"
   MPD_OUTPUT="FLAC LAN Stream"
   ```

   In `~/.config/ncmpcpp/config`, set `mpd_host` to the same tailnet IP. The `music` script passes `--host/--port` explicitly, so this file mainly matters when running `ncmpcpp` standalone.

4. **Verify.**

   ```sh
   music --check
   ```

   Expected:

   ```text
   Tailscale  OK  100.64.0.10     ...
   LAN        OK  192.168.50.10   ...
   ncmpcpp   ncmpcpp 0.10.x
   mpv       mpv v0.3x.x
   default    Tailscale roaming endpoint (LAN-direct when local)
   music client: OK
   ```

   If one path shows `FAIL`, the routing line next to it (`ip route get`) tells you whether traffic would leave via `tailscale0`, LAN, or nowhere.

## Usage

```sh
music                          # auto: Tailscale first, LAN fallback
music --host lan               # force raw LAN
music --host tailscale         # force Tailscale
music --host 192.168.50.10     # any custom address
music --check                  # full diagnostics
music --check --host lan       # diagnose one path
music --help                   # usage
```

Environment overrides (useful for testing without editing files):

```sh
MUSIC_HOST=lan music
MUSIC_TAILSCALE_HOST=100.64.0.10 MUSIC_LAN_HOST=192.168.50.10 music --check
MUSIC_MUTE=1 music             # start muted
MUSIC_CACHE_SECS=5 music       # larger roaming buffer (default 3)
MUSIC_CONFIG=/path/to/config music
```

Daily use:

1. Run `music`.
2. In `ncmpcpp`, browse (`2` library, `3` playlist), add with `a`/`Enter`, play/pause with `p`, seek with `f`/`b`.
3. `mpv` starts automatically when MPD shows `[playing]` and stops when paused/stopped.
4. Quit `ncmpcpp` with `q` — this stops the supervisor and cleans up the local `mpv`.

Works in a plain TTY, any terminal emulator, and over SSH (audio plays where `music` runs).

## Configuration reference

Precedence: built-in example defaults → config file (`$MUSIC_CONFIG` or `~/.config/lan-jukebox/config`) → environment → CLI.

| Setting | File key | Env var | Default | Notes |
|---|---|---|---|---|
| Tailnet IP | `TAILSCALE_HOST` | `MUSIC_TAILSCALE_HOST` | `100.64.0.10` | `tailscale ip -4` on server |
| LAN IP | `LAN_HOST` | `MUSIC_LAN_HOST` | `192.168.50.10` | Server LAN address |
| MPD port | `MPD_PORT` | `MUSIC_MPD_PORT` | `6600` | Must match server `port` |
| Stream port | `STREAM_PORT` | `MUSIC_STREAM_PORT` | `8000` | Must match server `audio_output port` |
| Output name | `MPD_OUTPUT` | `MUSIC_MPD_OUTPUT` | `FLAC LAN Stream` | Must match server `audio_output name` |
| Host select | — | `MUSIC_HOST` | `auto` | `auto\|lan\|tailscale\|IP`, or `--host` |
| Cache (s) | — | `MUSIC_CACHE_SECS` | `3` | `mpv --cache-secs` + readahead |
| Start muted | — | `MUSIC_MUTE` | `0` | Set `1` to start muted |
| Config path | — | `MUSIC_CONFIG` | `~/.config/lan-jukebox/config` | Alt config location |

State and logs (local only, never committed):

- Log: `~/.local/state/lan-jukebox/mpv.log`
- Runtime: `${XDG_RUNTIME_DIR:-/tmp/lan-jukebox-$UID}/lan-jukebox/` (`mpv.pid`, `supervisor.pid`, `music.lock`)

## Reliability (optional)

For a permanently-on server, an optional reliability layer keeps the jukebox playing without manual fixes:

- **Watchdog / healthcheck** — checks the MPD control port, stream port, and output state every 2 min; restarts the service once if it is truly unhealthy. It never resurrects an intentionally stopped server and never restarts for an empty queue.
- **Output repair** — re-enables a disabled FLAC output and restarts playback when the stream port has not bound after a restart.
- **Crash recovery** — snapshots the queue, position, and pause state every 10 s; after an unexpected exit, playback resumes where it stopped (only if the queue is unchanged).
- **Hardened service** — restart-always systemd unit with library-mount dependencies, sandboxing, and opt-in drop-ins (io_uring workaround, Tailscale ordering).

The basic per-user setup needs none of this. Install, configuration, tests, and limitations: [server/RELIABILITY.md](server/RELIABILITY.md).

## Security

The examples prioritise a trusted home network: no MPD password, binds on `0.0.0.0`. Harden to taste:

- **Tailscale-only (simplest lockdown):** set both `bind_to_address` lines in `mpd.conf` to your tailnet IP. Raw LAN stops working; tailnet keeps working everywhere.
- **Firewall (keep both paths):**

  ```sh
  sudo ufw allow from 192.168.50.0/24 to any port 6600,8000  # replace with your LAN subnet
  sudo ufw allow from 100.64.0.0/10 to any port 6600,8000
  ```

- **Password (optional):** add `password "..."` to `mpd.conf`, then use `mpc --password` / `ncmpcpp -P` or extend the script. Left unwired here to keep the example auditable.
- **Encryption:** MPD's HTTP stream is unencrypted. On trusted LAN this is fine; on untrusted networks use the Tailscale path (WireGuard-encrypted), never raw port-forwarding of `6600`/`8000` to the internet.

## License

MIT — see [LICENSE](LICENSE).

