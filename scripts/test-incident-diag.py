#!/usr/bin/env python3
"""The display diagnostics round records who asks for incident reports (oneos_switch.sh, incident.txt).

S25 Ultra, coloros17-findx9u-in-1105, session db475ad5017133c4: One UI's incidentd (the host's,
used by the guest) logged 95 "Starting incident report" in about 16 minutes, each followed by
send_report_ready_broadcasts for com.oplus.obrain/com.oplus.ocenter.module.IncidentReceiver, and
incident_helper reached 50% CPU. Nothing said who requested them: com.oplus.obrain through
IncidentManager, or a statsd config with an incidentd subscription. Each round now writes
$D/incident.txt: dumpsys incidentcompanion (pending reports, requesting and receiving package),
a separator, then the first 120 lines of cmd stats print-stats (statsd's configs and owner uids).

The round is run the way test-diag-round-light.py runs it (its helpers are loaded from that file):
the real block, unquoted, in the foreground with $BB and the rest stubbed. The app's log
collection (collect.list, logs.diag) takes every .txt of $S/diag_*/, so nothing else lists it.
"""
from pathlib import Path
import importlib.util
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('diag_round_light', ROOT / 'scripts/test-diag-round-light.py')
light = importlib.util.module_from_spec(spec)
spec.loader.exec_module(light)
light.SOURCE = light.SWITCH.read_text()
SOURCE = light.SOURCE
COLLECT = (ROOT / 'app-android/app/src/full/assets/logs/collect.list').read_text()

GENV = ' -m /system/bin/sh /data/oneos_genv.sh /system/bin/'


class IncidentDiag(light.Round):
    def test_round_writes_incident_txt(self):
        for dname in ('20', '40', 'final'):
            with self.subTest(dname=dname):
                _, diag, stage, (_, _, sf) = self.run_round('0', '1', dname=dname)
                calls = (stage.parent / 'calls').read_text().split('\n')
                comp = 'bb nsenter -t %s%sdumpsys incidentcompanion' % (sf, GENV)
                stats = 'bb nsenter -t %s%scmd stats print-stats' % (sf, GENV)
                self.assertIn(comp, calls)
                self.assertIn(stats, calls)
                self.assertLess(calls.index(comp), calls.index(stats))
                self.assertEqual('---\n', (diag / 'incident.txt').read_text())

    def test_source_form(self):
        rnd = light.round_text(SOURCE)
        self.assertEqual(1, rnd.count('> \\$D/incident.txt 2>&1'))
        self.assertIn('{ timeout 15 $BB nsenter -t \\$SFP' + GENV + 'dumpsys incidentcompanion\n', rnd)
        self.assertIn('timeout 15 $BB nsenter -t \\$SFP' + GENV + 'cmd stats print-stats 2>&1 | head -n 120; }'
                      ' > \\$D/incident.txt 2>&1\n', rnd)
        self.assertIn('db475ad5017133c4', rnd)

    def test_collected_by_the_diag_glob(self):
        self.assertIn('for f in "$S"/diag_*/*; do', COLLECT)
        self.assertIn('for e in .txt .log .xml .err; do', COLLECT)


# Only this file's tests: the inherited ones run in test-diag-round-light.py.
for _n in [n for n in dir(light.Round) if n.startswith('test_')]:
    if _n not in IncidentDiag.__dict__:
        setattr(IncidentDiag, _n, None)


if __name__ == '__main__':
    unittest.main()
