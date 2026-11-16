#!/usr/bin/env python3
"""The guest baseline's host-framework scan finds the same pids as the old
per-process tr loop (14-16 s of preflight on a Galaxy S25 Ultra with ColorOS 17,
sessions 4d2e95c67f065941 and 3d9814984612c525), and records "pid start"."""
from pathlib import Path
import os, shutil, subprocess, tempfile, time, unittest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=(ROOT/'switcher/assets/guest_runtime.sh').read_text()
FUNC=SCRIPT[SCRIPT.index('host_framework_pids() {'):]
FUNC=FUNC[:FUNC.index('\n}\n')+3]
# The loop as it was before the change, with only the proc root made a variable.
OLD=r'''old_scan() {
    for proc in "$1"/[0-9]*; do
        name=$({ tr '\000' ' ' < "$proc/cmdline"; } 2>/dev/null || true)
        case "$name" in vectord|vectord' '*|lspd|lspd' '*|org.lsposed.lspd|org.lsposed.lspd' '*|org.matrix.vector.daemon|org.matrix.vector.daemon' '*)
            echo "${proc##*/}"
            ;;
        esac
    done
}
'''
CASES={
 '101':b'lspd\0',
 '102':b'vectord\0',
 '103':b'org.lsposed.lspd\0',
 '104':b'org.matrix.vector.daemon\0',
 '105':b'lspd\0--from-service\0x\0',
 '106':b'org.lsposed.lspd --nice-name\0',  # argv[0] rewritten with a space
 '107':b'lspd',                            # no NUL at all
 '108':b'vectord\n\n',                     # $(...) dropped trailing newlines
 '201':b'/system/bin/lspd\0',
 '202':b'lspdx\0',
 '203':b'xlspd\0',
 '204':b'',                                # kernel thread
 '205':b'lspd\n\0',
 '206':b'org.lsposed.lsp\0',               # comm-length cut, not ours
 '207':b' lspd\0',
 '208':b'vectord\tx\0',
 '209':b'org.matrix.vector.daemonx\0',
}
WANT=['101','102','103','104','105','106','107','108']
SHELLS=[s for s in ('mksh','busybox','bash','dash','sh') if shutil.which(s)]
def run(shell,code,*args):
  argv=[shell,'sh'] if shell=='busybox' else [shell]
  return subprocess.run(argv+['-euc',code,'scan']+list(args),capture_output=True,text=True,check=True).stdout.split()
def fixture(d):
  root=Path(d)/'proc'
  for pid,data in CASES.items():
    (root/pid).mkdir(parents=True);(root/pid/'cmdline').write_bytes(data)
  (root/'301').mkdir()                                  # exited: no cmdline
  (root/'302').mkdir();os.symlink(root/'gone',root/'302/cmdline')
  (root/'self').mkdir();(root/'self/cmdline').write_bytes(b'lspd\0')
  return root
class ScanTest(unittest.TestCase):
 def test_fixture_matches_old_loop_in_every_shell(self):
  self.assertIn('mksh',SHELLS,'mksh is the phone\'s /system/bin/sh')
  with tempfile.TemporaryDirectory() as d:
   root=fixture(d)
   old=run('mksh',OLD+'old_scan "$1"',str(root))
   self.assertEqual(old,WANT)
   for sh in SHELLS:
    with self.subTest(shell=sh):
     self.assertEqual(run(sh,FUNC+'host_framework_pids "$1"',str(root)),old)
 def test_mksh_takes_the_fork_free_path(self):
  # A tr that fails the test proves mksh's scan never runs it.
  with tempfile.TemporaryDirectory() as d:
   root=fixture(d);b=Path(d)/'bin';b.mkdir()
   (b/'tr').write_text('#!/bin/sh\necho forked >> "$0.log"\nexit 1\n');(b/'tr').chmod(0o755)
   out=subprocess.run(['mksh','-euc',FUNC+'PATH="$2:$PATH"; host_framework_pids "$1"','scan',str(root),str(b)],capture_output=True,text=True,check=True).stdout.split()
   self.assertEqual(out,WANT);self.assertFalse((b/'tr.log').exists())
 def test_live_proc_matches_old_loop(self):
  bash=shutil.which('bash')
  if not bash: self.skipTest('needs bash for exec -a')
  procs=[subprocess.Popen([bash,'-c','exec -a "$0" sleep 30',n]) for n in ('lspd','org.lsposed.lspd','/system/bin/lspd')]
  try:
   time.sleep(0.3)
   old=run('mksh',OLD+'old_scan /proc');new=run('mksh',FUNC+'host_framework_pids /proc')
   self.assertEqual(new,old)
   self.assertEqual(sorted(new),sorted(str(p.pid) for p in procs[:2]))
  finally:
   for p in procs: p.kill();p.wait()
 def test_baseline_records_pid_and_start_and_stops_each(self):
  body=SCRIPT[SCRIPT.index('    : > "$B/host-frameworks"\n'):SCRIPT.index('    echo "guest baseline: frameworks scanned')]
  self.assertIn('$(host_framework_pids /proc)',body)
  with tempfile.TemporaryDirectory() as d:
   root=fixture(d);B=Path(d)/'b';B.mkdir()
   for pid in CASES:
    (root/pid/'stat').write_text(pid+' (x) S '+' '.join(str(i) for i in range(4,22))+' 9'+pid+' 23 24\n')
   code=FUNC+'kill() { echo "$*" >> "$B/killed"; }\n'+body.replace('/proc','$P')
   subprocess.run(['mksh','-euc',code],env={**os.environ,'B':str(B),'P':str(root)},check=True)
   self.assertEqual((B/'host-frameworks').read_text(),''.join(f'{p} 9{p}\n' for p in WANT))
   self.assertEqual((B/'killed').read_text(),''.join(f'-STOP {p}\n' for p in WANT))
if __name__=='__main__': unittest.main()
