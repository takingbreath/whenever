"""Acceptance test for "At night, tell me when a car drives into the driveway."

Written from the request alone, not from rule.py. What the words ask for:

1. A car that drives in at night sends one alert, when it drives in.
2. A car that drives in by day sends no alert.
3. Night runs past midnight: 21:30, 23:00 and 02:30 are night. 10:00 and 13:00 are day. Dusk and dawn
   are left out, because the words do not say where night starts.
4. One car is one alert. A car that stays parked afterwards sends nothing more.
5. Two cars that drive in 20 s apart are two alerts.
6. If the alert has a picture, it is a frame of that arrival that shows the car.
7. A failed model call for the picture must not swallow the alert.

The sensor is a moment (rule.json), so each arrival is on_event and then at once on_end. The sensor's
name comes from rule.json, because the request names no sensor.
"""

import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import FakeServer, Run, Thing, expect, load_plan, load_rule, note, run_cases  # noqa: E402

rule = load_rule(HERE)
SENSOR = load_plan(HERE)["sensors"][0]["id"]
CAMERA = "Driveway at night"
CAR_BOX = (0.35, 0.45, 0.70, 0.85)


def at(hour, minute=0, day=4):
    """A local time on a November day, as the rule's own clock reads it."""
    return datetime(2026, 11, day, hour, minute).timestamp()


def driveway(*arrivals):
    """A recorded driveway where each car comes into view 1 s before its arrival event and stays parked.
    A picture check passes on a frame only while a car is in view."""
    cars = [Thing(CAMERA, CAR_BOX, t - 1.0, t + 3600, label="car") for t in arrivals]

    def judge(t, claim):
        seen = any(car.visible(t) for car in cars)
        return seen, 0.9 if seen else 0.8, "a car at the driveway entrance" if seen else "an empty driveway"
    return FakeServer(cars, judge=judge, every=1 / 3), cars


def arrive(run, server, t):
    """One arrival: the sensor's 6 frames over the last 2 s, the newest as its proof."""
    event = server.event(SENSOR, t, count=6, every=1 / 3)
    run.moment(SENSOR, t, event)
    return event


def check_picture(alert, event, cars):
    picture = alert.get("picture")
    if picture is None:
        return
    shown = [f for f in event.frames if f.path == picture]
    expect(shown, f"the alert's picture {picture} must be a frame of this arrival")
    expect(any(car.visible(shown[0].t) for car in cars), f"the alert's picture {picture} must show the car")


def case_night_arrival_sends_one_alert():
    t = at(23, 0)
    server, cars = driveway(t)
    run = Run(rule, server)
    run.ticks(t - 30, t - 1)
    event = arrive(run, server, t)
    run.ticks(t + 1, t + 300)   # the car stays parked for 5 minutes

    alerts = run.alerts()
    expect(len(alerts) == 1, f"one car at night must send one alert, got {len(alerts)}: {alerts}")
    when, alert = alerts[0]
    expect(when == t, f"the alert must go out when the car drives in, not {when - t:+.0f} s later")
    expect("car" in alert["text"].lower(), f"the alert must say a car came: {alert['text']!r}")
    check_picture(alert, event, cars)
    note(f"{server.paid_count(until=when)} paid Spark frame checks ran before this alert went out "
         f"({'with' if alert.get('picture') else 'without'} a picture)")


def case_daytime_cars_send_no_alert():
    times = [at(10, 0), at(13, 0)]
    server, _ = driveway(*times)
    run = Run(rule, server)
    for t in times:
        arrive(run, server, t)
        run.ticks(t + 1, t + 30)
    expect(run.alerts() == [], f"a car by day must not alert: {run.alerts()}")
    note(f"{server.paid_count()} paid calls in the rule's code for the 2 daytime cars")


def case_night_runs_past_midnight():
    times = [at(21, 30), at(2, 30, day=5)]
    server, cars = driveway(*times)
    run = Run(rule, server)
    events = []
    for t in times:
        events.append(arrive(run, server, t))
        run.ticks(t + 1, t + 30)
    alerts = run.alerts()
    expect([w for w, _ in alerts] == times, f"cars at 21:30 and 02:30 must each alert: {alerts}")
    for (_, alert), event in zip(alerts, events):
        check_picture(alert, event, cars)


def case_two_cars_twenty_seconds_apart_send_two_alerts():
    first, second = at(23, 10), at(23, 10) + 20
    server, cars = driveway(first, second)
    run = Run(rule, server)
    one = arrive(run, server, first)
    run.ticks(first + 1, second - 1)
    two = arrive(run, server, second)
    run.ticks(second + 1, second + 60)
    alerts = run.alerts()
    expect([w for w, _ in alerts] == [first, second], f"two cars must send two alerts: {alerts}")
    check_picture(alerts[0][1], one, cars)
    check_picture(alerts[1][1], two, cars)


def case_alert_survives_a_failed_picture_check():
    t = at(0, 40, day=5)
    server, cars = driveway(t)
    server.fail = {"check", "ask"}
    run = Run(rule, server)
    event = arrive(run, server, t)
    alerts = run.alerts()
    expect(len(alerts) == 1, f"the alert must still go out when the picture check fails: {alerts}")
    check_picture(alerts[0][1], event, cars)


if __name__ == "__main__":
    run_cases(globals())
