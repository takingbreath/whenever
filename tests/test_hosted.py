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

    def test_a_server_beside_this_one_shares_the_budget(self):
        other = Path(self.folder) / "other_spend.json"
        app = server.App(object(), self.folder, inline=True)
        other.write_text(json.dumps({"day": str(app.spend_day), "writer_usd": server.SPEND_LIMIT_USD}))
        with mock.patch.object(server, "SPEND_ALSO", (other,)):
            self.assertTrue(app.stopped())
            self.assertEqual(app.status()["spend_usd"], 0)
            self.assertEqual(app.status()["spend_shared_usd"], round(server.SPEND_LIMIT_USD, 4))
        # A demo with a small limit of its own still stops at its own limit.
        with mock.patch.object(server, "SPEND_ALSO", (other,)), mock.patch.object(server, "SPEND_SHARED_USD", 100), \
                mock.patch.object(server, "SPEND_LIMIT_USD", 1):
            app.others_read = (0.0, 0.0)
            self.assertFalse(app.stopped())
            app.writer_usd = 1
            self.assertTrue(app.stopped())
        app.writer_usd = 0
        # A one-day extra for all the servers together moves only the shared limit.
        day = str(app.spend_day)
        with mock.patch.object(server, "SPEND_SHARED_EXTRA", f"{day}:5"), mock.patch.object(server, "SPEND_SHARED_USD", 10):
            self.assertEqual(server.shared_limit(app.spend_day), 15)
            self.assertEqual(server.spend_limit(app.spend_day), server.SPEND_LIMIT_USD)
        with mock.patch.object(server, "SPEND_SHARED_EXTRA", f"{day}:5"):
            self.assertEqual(server.shared_limit(app.spend_day), server.SPEND_LIMIT_USD + 5)
        # Yesterday's spend of the other server does not count.
        other.write_text(json.dumps({"day": "2000-01-01", "writer_usd": server.SPEND_LIMIT_USD}))
        app.others_read = (0.0, 0.0)
        with mock.patch.object(server, "SPEND_ALSO", (other,)):
            self.assertFalse(app.stopped())

    def test_a_used_budget_keeps_the_footage_but_pays_for_no_check(self):
        from io import BytesIO
        from PIL import Image
        jpeg = BytesIO()
        Image.new("RGB", (64, 36)).save(jpeg, "JPEG")
        app = server.App(object(), self.folder, inline=True)
        app.writer_usd = server.SPEND_LIMIT_USD
        self.assertTrue(app.frame(jpeg.getvalue(), 1.0)["stopped"])
        self.assertEqual(len(app.timeline), 1, "the free overlay and the moment index still get the frame")
        with mock.patch.object(server, "IDLE_PAUSE_S", 600):
            app.page_seen -= 601
            app.frame(jpeg.getvalue(), 2.0)
        self.assertEqual(len(app.timeline), 1, "a demo that nobody watches keeps nothing")

    def test_a_zero_limit_reads_as_paid_checks_off_not_as_a_day_that_ran_out(self):
        app = server.App(object(), self.folder, inline=True)
        with mock.patch.object(server, "SPEND_LIMIT_USD", 0), mock.patch.object(server, "SPEND_EXTRA", ""):
            self.assertTrue(app.stopped())
            self.assertTrue(app.status()["paid_off"])
            groups = app.problems.public()["groups"]
            self.assertIn(("Paid checks are off on this server", "note"), [(g["title"], g["level"]) for g in groups])
            self.assertFalse(any("midnight" in g["title"] for g in groups))
            self.assertEqual(app.status()["problems"], [], "a choice of the owner is no warning on the page")
        self.assertFalse(app.paid_off())

    def test_a_demo_sleeps_while_nobody_has_it_open(self):
        app = server.App(object(), self.folder, inline=True)
        with mock.patch.object(server, "IDLE_PAUSE_S", 600):
            self.assertFalse(app.stopped())
            app.page_seen -= 601
            self.assertTrue(app.stopped())
            self.assertFalse(app.status()["stopped"], "a page that asks for the status wakes it")
        # A real site never sleeps.
        app.page_seen -= 10_000
        self.assertFalse(app.stopped())


class ShortNote(unittest.TestCase):
    def test_a_model_note_becomes_one_plain_line(self):
        self.assertEqual(server.short_note("Frame 7 top-left shows left gate panel swung outward; Fram"),
                         "Left gate panel swung outward")
        self.assertEqual(server.short_note("man in white traditional clothing; Frame 8 top-left shows man"),
                         "Man in white traditional clothing")
        self.assertEqual(server.short_note("In frames 3-5, a car turns in"), "A car turns in")
        self.assertTrue(server.short_note("word " * 60).endswith("…"))


class CameraSites(unittest.TestCase):
    def test_sites_keep_only_cameras_that_exist_in_wall_order(self):
        sites = server.camera_sites("Home: Driveway, Gate ; Cafe: Cafe; Empty: Nowhere", ["Gate", "Cafe", "Driveway"])
        self.assertEqual(sites, {"Home": ["Gate", "Driveway"], "Cafe": ["Cafe"]})
        self.assertEqual(server.camera_sites("", ["Gate"]), {})


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

    @unittest.skipUnless(sys.platform == "linux", "Landlock and seccomp exist only on Linux.")
    def test_the_jail_lets_a_rule_make_no_links(self):
        # The server reads and writes in the folder, so a link could lead it to the server's own keys.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        script = ("import os\ntry:\n    os.symlink('/etc/hostname', 'rule.py')\n    print('made')\n"
                  "except PermissionError:\n    print('refused')\n")
        python = str(Path(sys.base_prefix) / "bin" / "python3")
        done = subprocess.run(sandbox.wrap(folder, [python, "-c", script]), capture_output=True, text=True)
        self.assertEqual(done.stdout.split(), ["refused"], done.stderr)
        self.assertFalse((folder / "rule.py").is_symlink())

    def test_installs_take_only_built_wheels_and_no_pip_options(self):
        with mock.patch.object(sandbox, "run", return_value=(0, "")) as run:
            sandbox.install("/srv/rule", ["numpy"])
            self.assertIn("--only-binary=:all:", run.call_args.args[1])
            self.assertEqual(sandbox.install("/srv/rule", ["--index-url=http://127.0.0.1:9997/"])[0], 2)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
