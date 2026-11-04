"""Acceptance test for "Tell me when someone opens the gate, with the picture."

Written from the request alone, not from rule.py. What the words ask for:

1. When the gate opens, an alert goes out at once, while the rule handles the opening. The sensor's
   own paid check already proved the opening, so no second model call comes before the alert.
2. The alert carries the picture that proved the opening: the event's proof frame.
3. One opening is one alert. The gate that stays open for 40 s and then closes sends nothing more.
4. A later, second opening is a second alert, with its own picture.
5. The gate closing is not an alert.

The sensor's name comes from rule.json, because the request names no sensor.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import FakeServer, Run, expect, load_plan, load_rule, run_cases  # noqa: E402

rule = load_rule(HERE)
SENSOR = load_plan(HERE)["sensors"][0]["id"]


def case_one_opening_sends_one_alert_at_once_with_its_proof():
    server = FakeServer()
    run = Run(rule, server)
    # The proof is the second of four frames, so the test sees whether the rule uses the proof itself.
    opening = server.event(SENSOR, 1000.0, count=4, proof=1)
    run.on(SENSOR, 1000.0, opening)
    run.ticks(1001, 1039)
    run.off(SENSOR, 1040.0)

    alerts = run.alerts()
    expect(len(alerts) == 1, f"one opening must send one alert, got {len(alerts)}: {alerts}")
    t, alert = alerts[0]
    expect(t == 1000.0, f"the alert must go out when the gate opens, at 1000.0, not at {t}")
    expect(alert.get("picture") == opening.proof.path,
           f"the alert must carry the proof frame {opening.proof.path}, got {alert.get('picture')}")
    expect("gate" in alert["text"].lower(), f"the alert must say what happened: {alert['text']!r}")
    expect(server.paid() == [], f"no paid model call may delay the alert, got {server.paid()}")


def case_two_openings_send_two_alerts_each_with_its_own_picture():
    server = FakeServer()
    run = Run(rule, server)
    first = run.on(SENSOR, 1000.0)
    run.ticks(1001, 1019)
    run.off(SENSOR, 1020.0)
    run.ticks(1021, 1199)
    second = run.on(SENSOR, 1200.0)
    run.off(SENSOR, 1230.0)

    alerts = run.alerts()
    expect([t for t, _ in alerts] == [1000.0, 1200.0], f"two openings must send two alerts: {alerts}")
    expect(alerts[0][1].get("picture") == first.proof.path, "the first alert must show the first opening")
    expect(alerts[1][1].get("picture") == second.proof.path, "the second alert must show the second opening")


def case_closing_sends_no_alert():
    server = FakeServer()
    run = Run(rule, server)
    run.on(SENSOR, 1000.0)
    before = len(run.alerts())
    run.off(SENSOR, 1045.0)
    run.ticks(1046, 1100)
    expect(len(run.alerts()) == before == 1, f"closing the gate must not alert: {run.alerts()}")


if __name__ == "__main__":
    run_cases(globals())
