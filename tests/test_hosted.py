"""A hosted server: the access code, the daily spend, and the Linux sandbox."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sandbox
import server

ROOT = Path(__file__).resolve().parent.parent


class Folder(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)


class AccessCode(Folder):
    def access(self, code="tall-river-42"):
        (self.folder / "access.env").write_text(code + "\n")
        return server.Access(self.folder / "access.env", self.folder / "sessions.json")

    def test_no_file_means_no_login(self):
        self.assertFalse(server.Access(self.folder / "none", self.folder / "sessions.json").needed())

    def test_the_right_code_gives_a_session_that_lasts(self):
        access = self.access()
        token = access.login("tall-river-42")
        self.assertTrue(access.valid(token))
        again = server.Access(self.folder / "access.env", self.folder / "sessions.json")
        self.assertTrue(again.valid(token))
        self.assertNotIn(token, (self.folder / "sessions.json").read_text())

    def test_a_wrong_code_gives_nothing(self):
        access = self.access()
        self.assertIsNone(access.login("tall-river-43"))
        self.assertFalse(access.valid("anything"))

    def test_guessing_is_slowed(self):
        access = self.access()
        for _ in range(server.MAX_LOGINS_PER_MINUTE):
            access.login("wrong")
        with self.assertRaises(ValueError):
            access.login("tall-river-42")

    def test_an_old_session_ends(self):
        access = self.access()
        token = access.login("tall-river-42")
        with mock.patch.object(server.time, "time", return_value=time.time() + server.SESSION_S + 1):
            self.assertFalse(access.valid(token))


class DailySpend(Folder):
    def test_the_limit_starts_again_on_a_new_day(self):
        app = server.App(object(), self.folder, inline=True)
        app.writer_usd = server.SPEND_LIMIT_USD
        self.assertTrue(app.stopped())
        app.spend_day = date(2000, 1, 1)
        self.assertFalse(app.stopped())
        self.assertEqual(app.spend(), 0)
        self.assertEqual(app.status()["spend_usd"], 0)


class LinuxSandbox(unittest.TestCase):
    def test_linux_uses_the_jail(self):
        with mock.patch.object(sandbox.sys, "platform", "linux"):
            command = sandbox.wrap("/srv/rule", ["python", "rule_runtime.py"])
        self.assertEqual(command[1:5], [str(ROOT / "jail.py"), str(Path("/srv/rule").resolve()), "0", "--"])
        self.assertEqual(command[5:], ["python", "rule_runtime.py"])

    def test_installs_get_the_network(self):
        with mock.patch.object(sandbox.sys, "platform", "linux"):
            self.assertEqual(sandbox.wrap("/srv/rule", ["pip"], network=True)[3], "1")

    @unittest.skipUnless(sys.platform == "linux", "Landlock and seccomp exist only on Linux.")
    def test_the_jail_keeps_a_rule_in_its_folder_and_offline(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        secret = Path(tempfile.mkdtemp()) / "key.txt"
        self.addCleanup(shutil.rmtree, secret.parent)
        secret.write_text("secret")
        script = ("import socket, pathlib\n"
                  "pathlib.Path('note.txt').write_text('ok')\n"
                  f"for test in (lambda: open({str(secret)!r}).read(), lambda: open('/tmp/x', 'w'),\n"
                  "             lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)):\n"
                  "    try:\n        test()\n        print('open')\n"
                  "    except PermissionError:\n        print('refused')\n")
        # A rule runs its own venv's Python. The system Python stands in for it, because the
        # Python that runs these tests may live in a venv outside the jail.
        python = str(Path(sys.base_prefix) / "bin" / "python3")
        done = subprocess.run(sandbox.wrap(folder, [python, "-c", script]), capture_output=True, text=True)
        self.assertEqual(done.stdout.split(), ["refused", "refused", "refused"], done.stderr)
        self.assertEqual((folder / "note.txt").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
