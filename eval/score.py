"""Scores an evaluation run against the labels in cases.json, and the motion baseline against the same labels.

Usage: python3 eval/score.py run.json
run.json holds what one run of the evaluation server saw: {"t0": first frame time, "journal": [rows of
GET /api/journal], "timeline": {"gate": {...}, "hand": {...}}, "cost_usd": spend of the run, "motion": {...}}.
Each camera played its evaluation video once, cases one after another with 6 s of a plain picture between.
An alert belongs to the case whose window holds its event's video time. A case that should alert is
correct when its first alert comes at or after the labelled moment (2 s of slack for the clock); an
alert before it, a second alert in the same case, or any alert in a case that should not alert is false.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLACK_S = 2.0
GAP_S = 6.0
FIRST_ALERT = {"gate": ("Gate Open Alert", None), "hand": ("Hand Raised Alert", "Waiter")}


def case_of(timeline, video_t):
    for case, (start, length) in timeline.items():
        if float(start) <= video_t < float(start) + float(length) + GAP_S:
            return case, video_t - float(start)
    return None, None


def score(run):
    labels = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())["cases"]}
    t0, rows = run["t0"], sorted(run["journal"], key=lambda r: r["t"])
    found = {case: [] for case in labels}
    events, outside = {}, {}
    for row in rows:
        values = row.get("values") or {}
        for kind, (rule, to) in FIRST_ALERT.items():
            if row["rule"] != rule:
                continue
            if row["kind"] == "event":
                events.setdefault(rule, []).append(row["t"])
            # The first alert only: the hand rule's escalation says "No one came within 1 minute".
            if row["kind"] == "notify" and not (row.get("text") or "").startswith("No one came"):
                # The alert belongs to the case of the newest event before it, by video time.
                before = [t for t in events.get(rule, []) if t <= row["t"]]
                seen = before[-1] if before else row["t"]
                case, at = case_of(run["timeline"][kind], seen - t0)
                if case:
                    found[case].append({"event_s": round(at, 1), "delay_s": round(row["t"] - seen, 1),
                                        "notify_t": row["t"], "text": row.get("text")})
                else:
                    # An alert outside this rule's cases, such as the hand rule firing on the gate camera.
                    outside.setdefault(kind, []).append({"video_s": round(seen - t0, 1), "text": row.get("text")})
    results = []
    for case, label in labels.items():
        alerts = found[case]
        start = float(run["timeline"][label["rule"]][case][0])
        motion = run["motion"].get(case, [])
        if label["alert"]:
            good = [a for a in alerts if a["event_s"] >= label["event_s"] - SLACK_S]
            first = good[0] if good else None
            delay = round(first["notify_t"] - (t0 + start + label["event_s"]), 1) if first else None
            results.append({"case": case, "rule": label["rule"], "expected": "alert", "alerts": len(alerts),
                            "correct": bool(first), "missed": not first,
                            "false": len(alerts) - (1 if first else 0), "delay_s": delay,
                            "motion_alerts": len(motion), "motion_before_event": sum(1 for m in motion if m < label["event_s"] - SLACK_S)})
        else:
            results.append({"case": case, "rule": label["rule"], "expected": "no alert", "alerts": len(alerts),
                            "correct": not alerts, "missed": False, "false": len(alerts), "delay_s": None,
                            "motion_alerts": len(motion), "motion_before_event": 0})
    for kind, extra in outside.items():
        results.append({"case": f"outside {kind} cases", "rule": kind, "expected": "no alert", "alerts": len(extra),
                        "correct": False, "missed": False, "false": len(extra), "delay_s": None,
                        "motion_alerts": 0, "motion_before_event": 0, "detail": extra})
    return results


def summary(results):
    lines = []
    for rule in ("gate", "hand", "all"):
        part = [r for r in results if rule == "all" or r["rule"] == rule]
        positive = [r for r in part if r["expected"] == "alert"]
        negative = [r for r in part if r["expected"] == "no alert" and not r["case"].startswith("outside")]
        delays = [r["delay_s"] for r in positive if r["delay_s"] is not None]
        motion_false = sum(r["motion_alerts"] for r in negative) + sum(r["motion_before_event"] for r in positive)
        lines.append({
            "rule": rule, "cases": len(positive) + len(negative), "should_alert": len(positive), "should_not": len(negative),
            "correct_alerts": sum(r["correct"] for r in positive), "missed": sum(r["missed"] for r in positive),
            "false_alerts": sum(r["false"] for r in part),
            "correct_silences": sum(1 for r in negative if r["correct"]),
            "delay_median_s": sorted(delays)[len(delays) // 2] if delays else None,
            "delay_max_s": max(delays) if delays else None,
            "motion_alerts": sum(r["motion_alerts"] for r in part), "motion_false_alerts": motion_false,
            "motion_cases_alerting_when_nothing_happened": sum(1 for r in negative if r["motion_alerts"]),
        })
    return lines


if __name__ == "__main__":
    run = json.loads(Path(sys.argv[1]).read_text())
    results = score(run)
    print(json.dumps({"cases": results, "summary": summary(results), "cost_usd": run.get("cost_usd")}, indent=1))
