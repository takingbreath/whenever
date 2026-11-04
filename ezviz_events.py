"""Prints each new motion alarm from EZVIZ's cloud, as one JSON line: {"serial", "channel", "t"}.

camera.py uses an alarm only to wake full decoding sooner. It never decides anything with it, because
a camera's own motion alarms miss some events and report others that did not happen.

Usage: .ezviz/bin/python ezviz_events.py SERIAL[,SERIAL...]
"""

import json
import sys
import time

POLL_S = 10
FRESH_S = 60


def new_alarms(data, seen, now):
    """The alarms in one answer that are new and recent, oldest first."""
    found = []
    for item in reversed(data.get("message") or []):
        key = item.get("msgId")
        t = (item.get("time") or 0) / 1000
        if not key or key in seen or now - t > FRESH_S:
            continue
        seen.add(key)
        channel = str(item.get("channel") or "1")
        found.append({"serial": item.get("deviceSerial", ""), "channel": channel, "t": t})
    return found


def main():
    from pyezvizapi import EzvizClient  # in .ezviz, the library's own Python

    serials = [s for s in sys.argv[1].split(",") if s]
    client = EzvizClient(token=json.loads(open("ezviz_token.json").read()))
    client.login()
    seen = set()
    first = True
    while True:
        for serial in serials:
            try:
                data = client.get_device_messages_list(serials=serial, limit=10)
            except Exception:  # noqa: BLE001 - the cloud can fail for a moment; the next poll tries again
                continue
            alarms = new_alarms(data, seen, time.time())
            # The alarms that were already there at the start are old news.
            if not first:
                for alarm in alarms:
                    print(json.dumps(alarm), flush=True)
        first = False
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
