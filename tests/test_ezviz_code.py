import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ezviz_code


class NeedsCode(Exception):
    pass


class FakeClient:
    def __init__(self, ask_first=False):
        self.ask_first = ask_first
        self.requested = []
        self.calls = []

    def get_cam_auth_code(self, serial, msg_auth_code=None, sender_type=0):
        self.calls.append((serial, msg_auth_code, sender_type))
        if self.ask_first and msg_auth_code is None:
            raise NeedsCode()
        return "ABCDEF"

    def get_2fa_check_code(self, biz_type, username=None):
        self.requested.append((biz_type, username))


class Fetch(unittest.TestCase):
    def test_a_plain_account_gets_the_code_at_once(self):
        client = FakeClient()
        code = ezviz_code.fetch_code(client, "K1", lambda _: self.fail("asked"), NeedsCode)
        self.assertEqual(code, "ABCDEF")
        self.assertEqual(client.requested, [])

    def test_a_protected_code_asks_for_the_login_name_and_a_one_time_code(self):
        client = FakeClient(ask_first=True)
        answers = iter([" me@example.com ", " 123456 "])
        code = ezviz_code.fetch_code(client, "K1", lambda _: next(answers), NeedsCode)
        self.assertEqual(code, "ABCDEF")
        self.assertEqual(client.requested, [("DEVICE_AUTH_CODE", "me@example.com")])
        self.assertEqual(client.calls[-1], ("K1", "123456", 3))


class LogIn(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = mock.patch.object(ezviz_code, "TOKEN", Path(self.folder.name) / "token.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def account(self, needs_code=False):
        made = []

        class Client:
            def __init__(self, account=None, password=None, token=None):
                self.account, self.password, self.token = account, password, token
                self.codes = []
                made.append(self)

            def login(self, sms_code=None):
                self.codes.append(sms_code)
                if needs_code and sms_code is None:
                    raise NeedsCode()
        return Client, made

    def test_a_saved_login_asks_for_nothing(self):
        ezviz_code.TOKEN.write_text('{"session_id": "x"}')
        client_type, made = self.account()
        ezviz_code.log_in(client_type, NeedsCode, lambda _: self.fail("asked"), lambda _: self.fail("asked"))
        self.assertEqual(made[0].token, {"session_id": "x"})

    def test_a_new_login_asks_and_hides_the_password(self):
        client_type, made = self.account()
        ezviz_code.log_in(client_type, NeedsCode, lambda _: " me ", lambda _: "secret")
        self.assertEqual((made[0].account, made[0].password), ("me", "secret"))

    def test_a_login_can_ask_for_a_one_time_code(self):
        client_type, made = self.account(needs_code=True)
        answers = iter(["me", " 123456 "])
        ezviz_code.log_in(client_type, NeedsCode, lambda _: next(answers), lambda _: "secret")
        self.assertEqual(made[0].codes, [None, 123456])


class Save(unittest.TestCase):
    def test_the_address_is_private_and_reads_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.env"
            ezviz_code.save_address("ABCDEF", "192.168.1.20", path)
            self.assertEqual(path.read_text(), "rtsp://admin:ABCDEF@192.168.1.20:554/H.264\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
