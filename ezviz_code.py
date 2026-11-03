"""Gets a camera's verification code from your EZVIZ account, for a label you cannot reach.

Run this yourself in a terminal, because it asks for your password and prints the code:
    .ezviz/bin/python ezviz_code.py K12345678 192.168.1.20
It saves a login token, so later runs need no password. It also saves the stream address in
camera.env. Git ignores both files.
"""

import getpass
import json
import sys
from pathlib import Path

TOKEN = Path("ezviz_token.json")
CONFIG = Path("camera.env")


def log_in(new_client, needs_one_time_code, ask=input, ask_hidden=getpass.getpass):
    """Uses the saved login if there is one. Otherwise it asks, and the password stays in this process."""
    if TOKEN.exists():
        client = new_client(token=json.loads(TOKEN.read_text()))
        client.login()
        return client
    client = new_client(ask("EZVIZ email or phone: ").strip(), ask_hidden("EZVIZ password (it stays hidden): "))
    try:
        client.login()
    except needs_one_time_code:
        client.login(sms_code=int(ask("One-time code from EZVIZ: ").strip()))
    return client


def fetch_code(client, serial, ask, needs_one_time_code):
    """EZVIZ can ask for a one-time code first. It sends that code to the account's email or phone.

    It needs the name that you log in with. The saved token holds only an internal id, which it refuses.
    """
    try:
        return client.get_cam_auth_code(serial)
    except needs_one_time_code:
        client.get_2fa_check_code("DEVICE_AUTH_CODE", username=ask("The email or phone you log in with: ").strip())
        return client.get_cam_auth_code(serial, msg_auth_code=ask("One-time code from EZVIZ: ").strip(), sender_type=3)


def save_token(client, path=TOKEN):
    path.write_text(json.dumps(client.export_token()))
    path.chmod(0o600)


def save_address(code, ip, path=CONFIG):
    path.write_text(f"rtsp://admin:{code}@{ip}:554/H.264\n")
    path.chmod(0o600)


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: .ezviz/bin/python ezviz_code.py <camera serial> <camera IP address>")
    from pyezvizapi import EzvizAuthVerificationCode, EzvizClient, PyEzvizError

    serial, ip = sys.argv[1:]
    client = None
    try:
        client = log_in(EzvizClient, EzvizAuthVerificationCode)
        save_token(client)
        code = fetch_code(client, serial, input, EzvizAuthVerificationCode)
    except (PyEzvizError, ValueError) as e:
        sys.exit(f"EZVIZ refused: {str(e)[:300]}")
    finally:
        if client:
            client.close_session()
    save_address(code, ip)
    print(f"\nVerification code: {code}")
    print(f"Saved the stream address in {CONFIG}. Do not paste the code in the chat.")


if __name__ == "__main__":
    main()
