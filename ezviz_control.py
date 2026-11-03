"""Moves an EZVIZ camera's lens and works its light. The server runs this with the .ezviz Python.

Usage: ezviz_control.py aim SERIAL X Y
       ezviz_control.py move SERIAL left|right|up|down SECONDS
       ezviz_control.py light SERIAL on SECONDS
       ezviz_control.py light SERIAL off

X and Y are a point in the fixed lens's picture, from 0 to 1. The moving lens turns to centre on it.
This process keeps the time for the light, so the light turns off even if the server stops.
"""

import json
import sys
import time
from pathlib import Path

from pyezvizapi import EzvizClient

ROOT = Path(__file__).resolve().parent
MOVING_LENS = "2"
SPEED = 3
ALARM_LIGHT = 303
LIGHT_CHANNEL = 1


def move(client, serial, direction, seconds):
    client.ptz_control(direction.upper(), serial, "START", SPEED)
    try:
        time.sleep(seconds)
    finally:
        client.ptz_control(direction.upper(), serial, "STOP", SPEED)


def light(client, serial, state, seconds):
    client.switch_status(serial, ALARM_LIGHT, 1 if state == "on" else 0, LIGHT_CHANNEL)
    if state == "on":
        print("on", flush=True)
        try:
            time.sleep(seconds)
        finally:
            client.switch_status(serial, ALARM_LIGHT, 0, LIGHT_CHANNEL)


def main():
    action, serial, *rest = sys.argv[1:]
    client = EzvizClient(token=json.loads((ROOT / "ezviz_token.json").read_text()))
    client.login()
    try:
        if action == "aim":
            client.ptz_control_coordinates(serial, float(rest[0]), float(rest[1]), local_index=MOVING_LENS)
        elif action == "move":
            move(client, serial, rest[0], float(rest[1]))
        elif action == "light":
            light(client, serial, rest[0], float(rest[1]) if len(rest) > 1 else 0)
        else:
            sys.exit("Unknown action.")
    finally:
        client.close_session()


if __name__ == "__main__":
    main()
