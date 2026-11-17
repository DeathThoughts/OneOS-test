#!/usr/bin/env python3
"""Exercise guest root shell routing with the native broker's helpers."""
import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "switcher/native/guest_root.c"


class GuestRootShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="oneos-root-shell-")
        cls.addClassCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        package_list = directory / "packages.list"
        package_list.write_text(
            "moe.shizuku.privileged.api 15461 0\n"
            "com.termux 15000 0\n"
        )
        source = directory / "probe.c"
        source.write_text(
            "#define main broker_main\n"
            + SOURCE.read_text().replace(
                '"/data/system/packages.list"', f'"{package_list}"'
            )
            + """
#undef main
int main(int argc, char **argv) {
    if (argc == 2) return !needs_guest_namespace_guard(atoi(argv[1]));
    const int fds[] = { STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO };
    return !interactive_stdio(fds);
}
"""
        )
        cls.probe = directory / "probe"
        subprocess.run(["cc", "-std=gnu11", str(source), "-o", str(cls.probe)], check=True)

    def test_guard_is_limited_to_the_guest_shizuku_uid(self):
        for uid, expected in ((15461, 0), (15000, 1), (0, 1)):
            with self.subTest(uid=uid):
                self.assertEqual(subprocess.run([self.probe, str(uid)]).returncode, expected)

    def test_pipes_do_not_get_a_pty(self):
        result = subprocess.run([self.probe], input=b"", capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_single_terminal_is_interactive(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        result = subprocess.run([self.probe], stdin=slave, stdout=slave, stderr=slave)
        self.assertEqual(result.returncode, 0)

    def test_redirected_output_is_preserved(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        result = subprocess.run(
            [self.probe], stdin=slave, stdout=subprocess.PIPE, stderr=slave
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
