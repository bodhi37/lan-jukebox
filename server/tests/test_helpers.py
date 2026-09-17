"""No daemon/root needed: exercise helper control flow with command doubles."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
DOUBLE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
rc = 0
if name == 'timeout':
    args = args[1:]
    if args[0] == 'bash':
        sys.exit(0 if s.get('stream') else 1)
    os.execvp(args[0], args)
elif name == 'mpc':
    args = args[4:]  # -h host -p port
    cmd = args[0]
    s.setdefault('calls', []).append(args)
    if cmd == 'status':
        if s.get('status_fail'): rc = 1
        else:
            print('volume: 100%')
            if s.get('playing', 'stopped') != 'stopped':
                print('[' + s['playing'] + '] #1/1 0:12/3:00 (6%)')
    elif cmd == 'current': print('1')
    elif cmd == 'playlist':
        if s.get('playlist_fail'): rc = 1
        else: print(s.get('queue', 'track.flac'), end='')
    elif cmd == 'outputs':
        print('Output 1 (' + os.environ['STREAM_OUTPUT'] + ') is ' +
              ('enabled' if s.get('enabled', True) else 'disabled'))
    elif cmd == 'enable': s['enabled'] = True
    elif cmd == 'play':
        s['playing'] = 'playing'
        if not s.get('broken'): s['stream'] = True
    elif cmd == 'pause': s['playing'] = 'paused'
    elif cmd == 'seek': pass
    else: rc = 2
elif name == 'systemctl':
    s.setdefault('systemctl', []).append(args)
    if args[0] == 'is-active': rc = 0 if s.get('active', True) else 1
    elif args[0] == 'restart':
        if s.get('restart_fail'): rc = 1
        else:
            s['stream'] = True
            s['status_fail'] = False
            s['active'] = True
p.write_text(json.dumps(s))
sys.exit(rc)
'''


class Helpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state.json'
        self.state.write_text('{}')
        bindir = self.root / 'bin'
        bindir.mkdir()
        for name in ('mpc', 'systemctl', 'timeout', 'sleep', 'logger'):
            p = bindir / name
            p.write_text(DOUBLE)
            p.chmod(0o755)
        self.env = os.environ | {
            'PATH': str(bindir) + ':' + os.environ['PATH'],
            'TEST_STATE': str(self.state), 'RECOVERY_DIR': str(self.root),
            'STREAM_OUTPUT': 'FLAC [test].* Stream', 'SERVICE_RESULT': 'success',
        }

    def set_state(self, **values):
        self.state.write_text(json.dumps(values))

    def get_state(self):
        return json.loads(self.state.read_text())

    def run_helper(self, name, *args, **env):
        return subprocess.run(['/bin/bash', str(SCRIPTS / ('lan-jukebox-' + name)), *args],
                              env=self.env | env, capture_output=True, text=True, timeout=10)

    def calls(self, command):
        return [x for x in self.get_state().get('calls', []) if x[0] == command]

    def prepare_marker(self, playing='playing'):
        self.set_state(playing=playing)
        self.assertEqual(self.run_helper('recovery', 'snapshot').returncode, 0)
        self.assertEqual((self.root / 'playback.snapshot').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_helper('recovery', 'mark', SERVICE_RESULT='signal').returncode, 0)
        self.assertTrue((self.root / 'recover.snapshot').exists())

    def test_restore_playing_and_paused(self):
        for state in ('playing', 'paused'):
            with self.subTest(state=state):
                self.prepare_marker(state)
                self.assertEqual(self.run_helper('recovery', 'restore').returncode, 0)
                self.assertEqual(self.calls('play'), [['play', '1']])
                self.assertEqual(self.calls('seek'), [['seek', '0:12']])
                self.assertEqual(bool(self.calls('pause')), state == 'paused')
                self.assertFalse((self.root / 'recover.snapshot').exists())

    def test_clean_stop_clears_marker(self):
        self.prepare_marker()
        self.run_helper('recovery', 'mark', SERVICE_RESULT='success')
        self.assertFalse((self.root / 'recover.snapshot').exists())

    def test_changed_or_unavailable_queue_does_not_restore(self):
        for override in ({'queue': 'different.flac'}, {'playlist_fail': True}):
            self.prepare_marker()
            self.set_state(**override)
            self.run_helper('recovery', 'restore')
            self.assertFalse(self.calls('play'))

    def test_failed_query_does_not_replace_snapshot(self):
        self.prepare_marker()
        old = (self.root / 'playback.snapshot').read_bytes()
        self.set_state(playlist_fail=True)
        self.run_helper('recovery', 'snapshot')
        self.assertEqual((self.root / 'playback.snapshot').read_bytes(), old)

    def test_malformed_marker_is_not_executed(self):
        self.prepare_marker()
        marker = self.root / 'recover.snapshot'
        marker.write_text(marker.read_text().replace('position=1', 'position=$(touch NEVER)'))
        self.run_helper('recovery', 'restore')
        self.assertFalse(self.calls('play'))

    def test_stopped_snapshot_not_marked(self):
        self.set_state(playing='stopped')
        self.run_helper('recovery', 'snapshot')
        self.run_helper('recovery', 'mark', SERVICE_RESULT='signal')
        self.assertFalse((self.root / 'recover.snapshot').exists())

    def test_healthy_is_noop(self):
        self.set_state(stream=True, playing='playing')
        self.assertEqual(self.run_helper('healthcheck').returncode, 0)
        self.assertFalse(self.calls('play'))
        self.assertNotIn(['restart', 'lan-jukebox-mpd.service'], self.get_state()['systemctl'])

    def test_literal_output_name_repaired(self):
        self.set_state(stream=True, enabled=False)
        self.assertEqual(self.run_helper('healthcheck').returncode, 0)
        self.assertEqual(self.calls('enable'), [['enable', self.env['STREAM_OUTPUT']]])

    def test_stopped_nonempty_queue_starts(self):
        self.set_state(stream=False)
        self.assertEqual(self.run_helper('healthcheck').returncode, 0)
        self.assertEqual(self.calls('play'), [['play']])

    def test_empty_queue_and_opt_out_do_not_restart(self):
        for state, env in (({'queue': ''}, {}), ({}, {'START_STOPPED': '0'})):
            self.set_state(**state)
            self.assertEqual(self.run_helper('healthcheck', **env).returncode, 0)
            self.assertFalse(self.calls('play'))
            self.assertFalse(any(x[0] == 'restart' for x in self.get_state()['systemctl']))

    def test_unhealthy_restarts_once(self):
        self.set_state(playing='playing', broken=True)
        self.assertEqual(self.run_helper('healthcheck').returncode, 0)
        restarts = [x for x in self.get_state()['systemctl'] if x[0] == 'restart']
        self.assertEqual(restarts, [['restart', 'lan-jukebox-mpd.service']])

    def test_restart_failure_reported(self):
        self.set_state(status_fail=True, restart_fail=True)
        self.assertEqual(self.run_helper('healthcheck').returncode, 1)


if __name__ == '__main__':
    unittest.main()
