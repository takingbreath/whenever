"""Acceptance test for "When a guest in the cafe raises a hand to call for service, alert the Waiter at
once. If no staff member reaches that table within 1 minute, alert the Manager."

Written from the request alone, not from rule.py. What the words ask for:

1. A raised hand alerts the Waiter at once, while the rule handles the event, and says which table.
2. With no staff at that table, the Manager is alerted when 1 minute has passed: not before 60 s, and
   within 2 s after, as the server ticks once a second.
3. Staff who reach that table in time stop the Manager alert: at 40 s, and also for a short visit of 6 s.
4. Only staff count. A tagged regular or an untagged person who stands at the table does not.
5. Only that table counts. Staff at another table do not stop the Manager alert.
6. Staff who come after the minute do not undo the Manager alert, and it goes out once only.
7. Two tables that call have their own timers.
8. A new call after service starts a new Waiter alert and a new minute.
9. A lowered hand does not cancel the call.

The recorded cafe has three tables. People are tagged as the operator would tag them, and a fake
identity service answers ctx.who from those tags. The sensor's name comes from rule.json.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import FakeServer, Run, Thing, expect, load_plan, load_rule, note, run_cases  # noqa: E402

rule = load_rule(HERE)
SENSOR = load_plan(HERE)["sensors"][0]["id"]
CAFE = "Cafe"
TABLES = [
    {"name": "table 1", "camera": CAFE, "box": [0.05, 0.55, 0.30, 0.80]},
    {"name": "table 2", "camera": CAFE, "box": [0.40, 0.55, 0.65, 0.80]},
    {"name": "table 3", "camera": CAFE, "box": [0.72, 0.55, 0.97, 0.80]},
]
STAFF = {"id": "p1", "name": "Waiter Asha", "role": "staff", "score": 0.52, "sure": 0.9, "by": "face"}
REGULAR = {"id": "p2", "name": "Regular 3", "role": "regular", "score": 0.5, "sure": 0.9, "by": "face"}


def guest(table, hand_up=()):
    """A seated guest at table 1, 2 or 3, there all through the test."""
    x = {1: 0.12, 2: 0.45, 3: 0.78}[table]
    return Thing(CAFE, (x, 0.35, x + 0.10, 0.70), 0, 10_000, hand_up=hand_up, sitting=True)


def standing(table, start, end, who=None):
    """A person who stands at the edge of table 1, 2 or 3 from start until end."""
    x = {1: 0.23, 2: 0.57, 3: 0.89}[table]
    return Thing(CAFE, (x, 0.20, x + 0.07, 0.75), start, end, who=who, sitting=False)


def cafe(*things):
    return FakeServer(things, places=TABLES, every=1 / 3)


def raise_hand(run, server, t):
    """The sensor's event: 3 frames over the last second, at 3 frames a second (rule.json)."""
    return run.on(SENSOR, t, server.event(SENSOR, t, count=3, every=1 / 3))


def waiter(run):
    return run.alerts(to="Waiter")


def manager(run):
    return run.alerts(to="Manager")


def expect_manager_on_time(run, raised, table):
    alerts = manager(run)
    expect(len(alerts) == 1, f"the Manager must be alerted once for {table}, got {len(alerts)}: {alerts}")
    when, alert = alerts[0]
    expect(raised + 60 <= when <= raised + 62,
           f"the Manager alert must come when 1 minute has passed, at {raised + 60:.0f} to {raised + 62:.0f}, "
           f"not at {when:.0f}")
    expect(table in alert["text"], f"the Manager alert must name {table}: {alert['text']!r}")


def case_waiter_at_once_then_manager_after_one_minute():
    hand = guest(2, hand_up=[(98.5, 104)])
    server = cafe(hand, guest(1), guest(3))
    run = Run(rule, server)
    run.ticks(90, 99)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)          # the hand went down at 104, so the state ends 4 s later
    run.ticks(108, 200)

    alerts = waiter(run)
    expect(len(alerts) == 1, f"one raised hand must alert the Waiter once, got {len(alerts)}: {alerts}")
    when, alert = alerts[0]
    expect(when == 100.0, f"the Waiter must be alerted at once, at 100, not at {when}")
    expect("table 2" in alert["text"], f"the Waiter alert must name the table: {alert['text']!r}")
    subject = alert.get("subject")
    if subject:
        expect(tuple(subject["box"]) == tuple(round(v, 4) for v in hand.box),
               f"the alert picture must outline the guest who raised the hand, got {subject}")
    expect_manager_on_time(run, 100.0, "table 2")
    note(f"{server.paid_count()} paid Spark or SAM calls in the rule's code; "
         f"{sum(1 for c in server.calls if c[1] == 'places')} table lookups, which SAM answers once and the "
         f"rule's folder keeps; {sum(1 for c in server.calls if c[1] == 'detect')} free detector looks")


def case_staff_at_40_s_stops_the_manager_alert():
    server = cafe(guest(2, hand_up=[(98.5, 104)]), standing(2, 140, 170, STAFF))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 240)
    expect(len(waiter(run)) == 1, f"the Waiter must be alerted once: {waiter(run)}")
    expect(manager(run) == [], f"staff reached table 2 at 40 s, so no Manager alert: {manager(run)}")


def case_a_6_s_staff_visit_still_counts():
    # The same short visit, starting at each of four seconds, so the test does not depend on when the
    # rule happens to look.
    for start in (138.0, 139.0, 140.0, 141.0):
        server = cafe(guest(2, hand_up=[(98.5, 104)]), standing(2, start, start + 6, STAFF))
        run = Run(rule, server)
        raise_hand(run, server, 100.0)
        run.ticks(101, 107)
        run.off(SENSOR, 108.0)
        run.ticks(108, 240)
        expect(manager(run) == [], f"staff stood at table 2 from {start - 100:.0f} s to {start - 94:.0f} s, "
                                   f"so no Manager alert: {manager(run)}")


def case_people_who_are_not_staff_do_not_count():
    server = cafe(guest(2, hand_up=[(98.5, 104)]), standing(2, 125, 240, REGULAR), standing(2, 130, 240))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 200)
    expect_manager_on_time(run, 100.0, "table 2")


def case_staff_at_another_table_do_not_count():
    server = cafe(guest(2, hand_up=[(98.5, 104)]), guest(3), standing(3, 130, 240, STAFF))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 200)
    expect_manager_on_time(run, 100.0, "table 2")


def case_staff_after_the_minute_get_one_manager_alert_only():
    server = cafe(guest(2, hand_up=[(98.5, 104)]), standing(2, 170, 240, STAFF))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 300)
    expect_manager_on_time(run, 100.0, "table 2")


def case_two_tables_have_their_own_timers():
    server = cafe(guest(2, hand_up=[(98.5, 104)]), guest(3, hand_up=[(118.5, 124)]),
                  standing(2, 130, 150, STAFF))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 119)
    raise_hand(run, server, 120.0)
    run.ticks(120, 127)
    run.off(SENSOR, 128.0)
    run.ticks(128, 260)

    calls = waiter(run)
    expect([w for w, _ in calls] == [100.0, 120.0], f"each table's call must alert the Waiter at once: {calls}")
    expect("table 2" in calls[0][1]["text"] and "table 3" in calls[1][1]["text"],
           f"each Waiter alert must name its own table: {[a['text'] for _, a in calls]}")
    expect_manager_on_time(run, 120.0, "table 3")


def case_a_new_call_after_service_starts_again():
    server = cafe(guest(2, hand_up=[(98.5, 104), (298.5, 304)]), standing(2, 130, 150, STAFF))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 299)
    expect(manager(run) == [], f"the first call was served, so no Manager alert yet: {manager(run)}")
    raise_hand(run, server, 300.0)
    run.ticks(300, 307)
    run.off(SENSOR, 308.0)
    run.ticks(308, 400)
    expect([w for w, _ in waiter(run)] == [100.0, 300.0], f"a new call must alert the Waiter again: {waiter(run)}")
    expect_manager_on_time(run, 300.0, "table 2")


def case_raising_again_while_waiting_keeps_the_first_minute():
    server = cafe(guest(2, hand_up=[(98.5, 104), (118.5, 124)]))
    run = Run(rule, server)
    raise_hand(run, server, 100.0)
    run.ticks(101, 107)
    run.off(SENSOR, 108.0)
    run.ticks(108, 119)
    raise_hand(run, server, 120.0)
    run.ticks(120, 127)
    run.off(SENSOR, 128.0)
    run.ticks(128, 200)
    expect_manager_on_time(run, 100.0, "table 2")
    note(f"{len(waiter(run))} Waiter alert(s) for one guest who raised a hand twice in the same minute")


if __name__ == "__main__":
    run_cases(globals())
