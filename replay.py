"""Replays the saved checks of a Whenever through a watch plan, at no model cost.

    python replay.py RULE_ID [--sensor ID] [--watch JSON] [--json]

Set WHENEVER_REPLAY_ROOT to read another app folder's rules, logs and data.

For each saved paid check of the sensor, it rebuilds the watched places from that check's newest frame,
with the server's own code, and decides as the live server would: paid, or held. It reports the paid
checks, the live yes answers that the plan would hold as no (each one is a case to look at), and the
paid checks for each hour of the clock. Wonder, or a person, can try a plan here before the live rule
changes. A place found by name must already be in the rule's data/places.json, because a replay never
calls a model.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import types
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The app whose rules, logs and data a replay reads. It can differ from the code's own folder.
DATA = Path(os.environ.get("WHENEVER_REPLAY_ROOT") or ROOT)
os.environ.setdefault("WHENEVER_TZ", "Asia/Kolkata")
sys.path.insert(0, str(DATA / "vendor"))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import server  # noqa: E402

# What one paid sensor check costs on average: Spark's live spend over its calls on 4 November 2026.
CHECK_USD = 0.0054


class NoModels:
    """A replay pays for nothing. A place that is not found yet stays unknown."""

    def find(self, *args, **kwargs):
        return []

    def __getattr__(self, name):
        raise server.ApiError("A replay calls no model.")


def load_rule(rule_id):
    folder = DATA / "rules" / rule_id
    fields = json.loads((folder / "rule.json").read_text())
    pointed_file = folder / "pointed.json"
    pointed = json.loads(pointed_file.read_text()) if pointed_file.exists() else None
    return types.SimpleNamespace(id=rule_id, name=fields.get("name", rule_id), sentence=fields.get("sentence", ""),
                                 folder=folder, pointed=pointed, places=None,
                                 sensors=[server.Sensor(s) for s in fields.get("sensors") or []])


def replay(rule, sensor, logs=DATA / "logs", check_usd=CHECK_USD):
    """Returns the result of one sensor's watch plan on every saved session, with its cost for each day
    that the saved checks cover."""
    app = server.App(NoModels(), tempfile.mkdtemp(), inline=True)
    kinds = server.CameraKinds(DATA / "data" / "cameras.json")
    app.cameras = list(kinds.items) or list(app.cameras)
    # The wall's layout, as the camera reader makes it: a frame with no full copy is cut from it.
    columns = math.ceil(math.sqrt(max(1, len(app.cameras))))
    app.wall = (columns, math.ceil(max(1, len(app.cameras)) / columns))
    # A replay has no camera controls, which tell the live server that a lens can turn. The rule's pointed
    # thing kept which of its cameras move.
    pointed = rule.pointed or {}
    moving = set(pointed.get("moving") or [])
    moving |= {item.get("camera") for item in [pointed, *(pointed.get("also") or [])] if item.get("kind") == "moving"}
    app.camera_kind = lambda name: "moving" if name in moving else kinds.kind(name) if name in kinds.items else "fixed"
    paid, held, hours = 0, 0, Counter()
    skipped_yes, scores_paid = [], []
    span = [None, None]
    for folder in sorted(Path(logs).iterdir()):
        checks_file = folder / "checks.jsonl"
        if not checks_file.exists():
            continue
        frames = {}
        for line in (folder / "frames.jsonl").open():
            record = json.loads(line)
            frames[record["name"]] = server.Frame(record["t"], b"", record["name"], record.get("motion") or 0)
        pictures = folder / "frames"

        def source(name, tile, wall=None, pictures=pictures):
            full = pictures / "full" / f"{name[:-4]}_{tile}.jpg"
            if full.exists():
                image = Image.open(full)
                image.draft("RGB", server.FOCUS_DRAFT)
                return image.convert("RGB")
            sharp = pictures / "sharp" / name
            plain = sharp if sharp.exists() else pictures / name
            if not plain.exists():
                return None
            whole = Image.open(plain).convert("RGB")
            return whole.crop(app._tile_box(whole.size, tile))

        app._camera_source = source
        checks = []
        for line in checks_file.open():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if c.get("rule") == rule.id and c.get("sensor") == sensor.id and not c.get("review") and c.get("frames"):
                checks.append(c)
        checks.sort(key=lambda c: c["t"])
        sensor.quiet = None
        sensor.last_look = None
        sensor.state = server.SensorState(sensor.pattern, sensor.clear_after_s)
        for c in checks:
            frame = frames.get(c["frames"][-1])
            if frame is None or source(frame.name, 0) is None:
                continue  # its pictures are gone
            span = [min(span[0] or c["t"], c["t"]), max(span[1] or c["t"], c["t"])]
            places = app.place_now(rule, sensor, frame)
            answer, scores = app.held_answer(sensor, places, frame.t)
            hour = datetime.fromtimestamp(c["t"], server.ZONE).strftime("%H")
            if answer is not None:
                held += 1
                if c.get("hit") and not answer[0]:
                    skipped_yes.append({"t": c["t"], "at": datetime.fromtimestamp(c["t"], server.ZONE).strftime("%H:%M:%S"),
                                        "session": folder.name, "check": c["id"],
                                        "note": ((c.get("answers") or [{}])[0].get("note") or "")[:120]})
            else:
                paid += 1
                hours[hour] += 1
                if scores:
                    scores_paid.append(min(scores))
                if places is not None:
                    sensor.quiet = {"places": places, "t": frame.t, "hit": bool(c.get("hit")),
                                    "strong": bool(c.get("strong"))}
            # The live state, as the saved checks recorded it.
            if c.get("event") in ("on", "next"):
                sensor.state.on = True
            elif c.get("event") == "off":
                sensor.state.on = False
    days = max((span[1] - span[0]) / 86400, 1 / 24) if span[0] is not None else None
    return {"rule": rule.id, "sensor": sensor.id, "watch": sensor.watch, "paid": paid, "held": held,
            "share_paid": round(paid / max(1, paid + held), 3), "paid_by_hour": dict(sorted(hours.items())),
            "days": round(days, 2) if days else 0, "usd_per_day": round(paid / days * check_usd, 2) if days else 0,
            "skipped_yes": skipped_yes}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("rule")
    parser.add_argument("--sensor", help="the sensor id; the first one by default")
    parser.add_argument("--watch", help="a watch plan to try, as JSON, in place of the sensor's own")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--price", type=float, default=CHECK_USD, help="the cost of one paid check in USD")
    args = parser.parse_args()
    rule = load_rule(args.rule)
    sensor = next((s for s in rule.sensors if s.id == args.sensor), rule.sensors[0]) if args.sensor else rule.sensors[0]
    if args.watch:
        sensor.watch = server.clean_watch(json.loads(args.watch), sensor.pattern, sensor.target, sensor.armed_until is not None)
    result = replay(rule, sensor, check_usd=args.price)
    if args.json:
        print(json.dumps(result, indent=1))
        return
    print(f"{rule.name}, sensor {sensor.id}: {result['paid']} paid, {result['held']} held "
          f"({result['share_paid']:.0%} paid). Plan: {json.dumps(result['watch'])}")
    print("paid by hour:", " ".join(f"{h}:{n}" for h, n in result["paid_by_hour"].items()))
    print(f"cost: about ${result['usd_per_day']:.2f} a day, from {result['days']} days of saved checks "
          f"at ${args.price} a check")
    print(f"live yes answers held as no: {len(result['skipped_yes'])}")
    for item in result["skipped_yes"]:
        print(f"  {item['at']} {item['check']}: {item['note']}")


if __name__ == "__main__":
    main()
