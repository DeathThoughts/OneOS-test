#!/usr/bin/env python3
"""guest_props.sh puts the guest at log level W when Full session logs is off.

Galaxy S23 ColorOS 17 X9U IN session cba68d07 (11-15): logd at 55% of a core,
three quarters of the lines I, D or V. One UI sets no log.tag, so with the
capture off the guest still logged everything. Runs the real block with
stand-ins for getprop and resetprop; the oneos_switch.sh side (knob check,
persist.sys.assert.panic) is checked on its text.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROPS = (ROOT / 'switcher/assets/guest_props.sh').read_text()
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()


def block():
    start = PROPS.index('    # Quiet guest logs when Full session logs is off')
    end = PROPS.index('\n    fi\n', PROPS.index('log level W for the session (Full session logs off)"', start)) + len('\n    fi\n')
    return PROPS[start:end]


class QuietLogsTest(unittest.TestCase):
    def run_block(self, env, host=None, new='ro.build.type=userdebug\n', rp_ok=True):
        host = host or {}
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            hostf = r / 'host'
            hostf.write_text(''.join(k + '\n' for k in host) or 'ro.other\n')
            vals = r / 'vals'
            vals.write_text(''.join(f'{k}={v}\n' for k, v in host.items()))
            newf = r / 'new'
            newf.write_text(new)
            ovr = r / 'ovr'
            ovr.write_text('')
            rp = r / 'rp'
            rp.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {r}/rp.log\nexit {0 if rp_ok else 1}\n')
            os.chmod(rp, 0o755)
            script = (f'getprop() {{ sed -n "s/^$1=//p" {vals} | head -n 1; }}\n'
                      f'HOST={hostf}; NEW={newf}; OVR={ovr}; RP={rp}\n' + block())
            e = {k: v for k, v in os.environ.items() if not k.startswith('ONEOS_')}
            e.update(env)
            done = subprocess.run(['sh', '-c', script], capture_output=True, text=True, env=e)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual('', done.stderr)
            rp_log = (r / 'rp.log').read_text() if (r / 'rp.log').exists() else ''
            self.assertFalse((r / 'new.q').exists())
            return done.stdout, newf.read_text(), ovr.read_text(), rp_log

    def test_full_logs_off_puts_the_guest_at_w(self):
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'})
        self.assertIn('log.tag=W\n', new.splitlines(keepends=True))
        self.assertIn('guest_props: log level W for the session (Full session logs off)', out)
        # In the added list (remove blanks it), nothing of One UI's touched.
        self.assertEqual('', ovr)
        self.assertEqual('', rp)
        self.assertIn('ro.build.type=userdebug\n', new)

    def test_the_tags_oneos_reads_keep_their_level(self):
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'})
        lines = new.splitlines()
        for kv in ('log.tag.boot_progress_enable_screen=I', 'log.tag.am_proc_start=I',
                   'log.tag.power_screen_state=I', 'log.tag.AHAL_SecModulePrimary=V',
                   'log.tag.AHAL_StreamBluetooth=D', 'log.tag.Telecom=I', 'log.tag.OneOS=V',
                   'log.tag.thermal_core=V', 'log.tag.OneOSComfortView=V'):
            self.assertIn(kv, lines)
        self.assertNotIn('log.tag.RILJ', new)
        # Every tag sound_watch.log asks for below W has an exception.
        sw = SWITCH[SWITCH.index('-f "$STAGE/sound_watch.log"'):]
        sw = sw[:sw.index('</dev/null')]
        for tok in sw.replace('\\', ' ').split():
            if ':' in tok and tok.rsplit(':', 1)[1] in ('V', 'D', 'I'):
                tag, lvl = tok.rsplit(':', 1)
                self.assertTrue(any(l.startswith(f'log.tag.{tag}=') for l in lines), tag)

    def test_one_ui_s_own_log_tag_is_overridden_and_recorded(self):
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'}, host={'log.tag': 'D'})
        self.assertEqual('log.tag=D\n', ovr)
        self.assertEqual('-n log.tag W\n', rp)
        self.assertNotIn('log.tag=W', new)
        self.assertIn("overridden to W", out)

    def test_one_ui_s_own_quieter_log_tag_is_kept(self):
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'}, host={'log.tag': 'E'})
        self.assertEqual('', ovr)
        self.assertEqual('', rp)
        self.assertNotIn('log.tag=', new.replace('log.tag.', 'x'))
        self.assertIn("is One UI's E already, kept", out)

    def test_one_ui_s_tag_keys_are_left_alone(self):
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'}, host={'log.tag.Telecom': 'D'})
        self.assertNotIn('log.tag.Telecom=', new)
        self.assertEqual('', rp)

    def test_full_logs_on_is_untouched(self):
        new_in = 'ro.x=1\nlog.tag.Foo=D\npersist.logd.logpersistd=logcatd\n'
        for env in ({'ONEOS_FULL_LOGS': '1'}, {}):
            out, new, ovr, rp = self.run_block(env, new=new_in)
            self.assertEqual(new_in, new)
            self.assertEqual('', out)
            self.assertEqual('', rp)

    def test_log_info_wins(self):
        new_in = 'ro.x=1\nlog.tag.Foo=D\n'
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0', 'ONEOS_GUEST_LOG_INFO': '1'}, new=new_in)
        self.assertEqual(new_in, new)
        self.assertEqual('', rp)
        self.assertIn('left to ONEOS_GUEST_LOG_INFO', out)
        # The LOG_INFO block comes after this one, so its log.tag=I is the last word.
        self.assertLess(PROPS.index('# Quiet guest logs when Full session logs is off'),
                        PROPS.index('echo "log.tag=I" >> "$NEW"'))

    def test_knob_off_is_untouched(self):
        new_in = 'ro.x=1\nlog.tag.Foo=D\npersist.logd.logpersistd=logcatd\n'
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0', 'ONEOS_GUEST_QUIET_LOGS': '0'}, new=new_in)
        self.assertEqual(new_in, new)
        self.assertEqual('', out)

    def test_logpersist_and_image_debug_levels_are_left_out(self):
        new_in = ('ro.x=1\npersist.logd.logpersistd=logcatd\npersist.logd.logpersistd.buffer=all\n'
                  'logd.logpersistd=logcatd\nlog.tag.Foo=D\npersist.log.tag.Bar=VERBOSE\n'
                  'log.tag.Quiet=S\nlog.tag.Warn=W\npersist.log.tag=I\npersist.logd.size=16M\n')
        out, new, ovr, rp = self.run_block({'ONEOS_FULL_LOGS': '0'}, new=new_in)
        lines = new.splitlines()
        for k in ('persist.logd.logpersistd=', 'persist.logd.logpersistd.buffer=', 'logd.logpersistd=',
                  'log.tag.Foo=', 'persist.log.tag.Bar=', 'persist.log.tag='):
            self.assertFalse(any(l.startswith(k) for l in lines), k)
        for kv in ('ro.x=1', 'log.tag.Quiet=S', 'log.tag.Warn=W', 'persist.logd.size=16M', 'log.tag=W'):
            self.assertIn(kv, lines)
        self.assertIn('3 of the image', out)
        self.assertIn('persist.logd.logpersistd* left out', out)

    def test_switch_validates_the_knob_and_gates_assert_panic(self):
        self.assertIn('case "${ONEOS_GUEST_QUIET_LOGS:-1}" in 0|1) ;; *) refuse', SWITCH)
        i = SWITCH.index('setprop persist.sys.assert.panic true')
        gate = SWITCH[SWITCH.rindex('\nif ', 0, i):i]
        self.assertIn('"${ONEOS_FULL_LOGS:-1}" = 1', gate)
        self.assertIn('"${ONEOS_GUEST_QUIET_LOGS:-1}" != 1', gate)
        # The knob check runs before guest_props.sh add.
        self.assertLess(SWITCH.index('ONEOS_GUEST_QUIET_LOGS=$ONEOS_GUEST_QUIET_LOGS'),
                        SWITCH.index('/system/bin/sh "$STAGE/guest_props.sh" add'))


if __name__ == '__main__':
    unittest.main()
