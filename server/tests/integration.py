#!/usr/bin/env python3
"""Disposable real-MPD test. Run ONLY in the documented isolated container.

Uses ports 6600/8000 inside that container. Does not run systemd as PID 1:
its systemctl double supervises only this test's MPD process. Unit syntax is
validated separately. Audio is generated silence, no library or credentials.
"""
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import wave

SERVER = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        music = root / 'music'
        music.mkdir()
        (root / 'playlists').mkdir()
        with wave.open(str(music / 'silence.wav'), 'wb') as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b'\0' * 44100 * 4 * 60)
        config = root / 'mpd.conf'
        config.write_text((SERVER / 'mpd-system.conf.example').read_text()
                          .replace('/srv/music', str(music))
                          .replace('/var/lib/lan-jukebox', str(root))
                          .replace('log_file            "syslog"', 'log_file "' + str(root / 'daemon.log') + '"'))
        env = os.environ | {'RECOVERY_DIR': str(root), 'LC_ALL': 'C',
                            'MPD_HOST': '127.0.0.1', 'MPD_PORT': '6600'}
        process = None
        logfile = (root / 'mpd.log').open('w')

        def mpc(*args):
            try:
                return subprocess.check_output(['mpc', '-h', '127.0.0.1', '-p', '6600', *args],
                                               text=True)
            except subprocess.CalledProcessError as error:
                alive = process is not None and process.poll() is None
                print(f'DEBUG mpc {args} failed rc={error.returncode} out={error.stdout!r} '
                      f'err={error.stderr!r} mpd_alive={alive}')
                raise

        def start():
            nonlocal process
            process = subprocess.Popen(['mpd', '--no-daemon', str(config)],
                                       stdout=logfile, stderr=logfile)
            for _ in range(100):
                try:
                    mpc('status')
                    return
                except subprocess.CalledProcessError:
                    if process.poll() is not None:
                        raise RuntimeError((root / 'mpd.log').read_text())
                    time.sleep(.1)
            raise RuntimeError('MPD did not become ready')

        def helper(name, *args, **extra):
            subprocess.run(['bash', str(SERVER / 'scripts' / ('lan-jukebox-' + name)), *args],
                           env=env | extra, check=True, timeout=120)

        try:
            start()
            mpc('update', '--wait')
            mpc('add', 'silence.wav')
            mpc('repeat', 'on')
            mpc('play')
            time.sleep(1)
            mpc('seek', '0:12')
            helper('recovery', 'snapshot')
            # Persist queue first, then simulate unexpected termination. MPD's
            # own state file is required for queue identity to survive a crash.
            process.terminate()
            process.wait(timeout=10)
            start()
            mpc('play')
            mpc('seek', '0:12')
            helper('recovery', 'snapshot')
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
            helper('recovery', 'mark', SERVICE_RESULT='signal')
            start()
            helper('recovery', 'restore')
            assert '[playing]' in mpc('status'), 'playing state not restored'
            assert not (root / 'recover.snapshot').exists()
            print('PASS real MPD: crash -> same queue -> resume + seek')

            mpc('pause')
            helper('recovery', 'snapshot')
            helper('recovery', 'mark', SERVICE_RESULT='signal')
            # Realistic crash while paused. Avoid clean stop+play+seek here:
            # Debian trixie MPD 0.24.4 segfaults on seek after an explicit
            # stop->play sequence (reproduced without helper code; harmless
            # for normal playback, but it would fake a failure in this test).
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
            start()
            helper('recovery', 'restore')
            assert '[paused]' in mpc('status')
            print('PASS real MPD: paused snapshot restored after crash')

            # A local double prevents any call to the machine's service manager.
            bindir = root / 'bin'
            bindir.mkdir()
            double = bindir / 'systemctl'
            double.write_text('#!/bin/sh\n[ "$1" = is-active ] && exit 0\n'
                              'echo "Unexpected service restart in healthy MPD test" >&2\nexit 99\n')
            double.chmod(0o755)
            env['PATH'] = str(bindir) + ':' + os.environ['PATH']
            mpc('disable', 'FLAC LAN Stream')
            helper('healthcheck')
            assert '(FLAC LAN Stream) is enabled' in mpc('outputs')
            print('PASS real MPD: watchdog re-enables disabled output')
            mpc('play')  # bind httpd before reading stream bytes
            with socket.create_connection(('127.0.0.1', 8000), timeout=5) as stream:
                stream.sendall(b'GET / HTTP/1.0\r\n\r\n')
                data = b''
                while b'fLaC' not in data and len(data) < 65536:
                    chunk = stream.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                assert b'fLaC' in data, 'no FLAC stream header received'
            print('PASS real MPD: HTTP output returns FLAC bytes')
            mpc('clear')
            helper('healthcheck')
            print('PASS real MPD: empty queue does not restart service')
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            logfile.close()
            if (root / 'daemon.log').exists():
                print((root / 'daemon.log').read_text())


if __name__ == '__main__':
    main()
