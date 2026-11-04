from datetime import datetime

import rule
from rule_runtime import Context, replay


def ts(hour, minute=0, second=0):
    return datetime(2026, 1, 15, hour, minute, second).timestamp()


def notifies(ctx):
    return [a for a in ctx.actions if a.get("do") == "notify"]


# 1. Must act: car driving in at 23:00, and one event frame passes the
# exact-frame picture check -> one notify WITH that checked picture.
ctx = Context()
ctx.checks = [(True, 0.9), (False, 0.5), (False, 0.5)]
ctx = replay(rule, [("on", "car_driving_in", ts(23))], ctx)
assert len(notifies(ctx)) == 1, ctx.actions
assert "driving into the driveway" in notifies(ctx)[0]["text"]
assert notifies(ctx)[0].get("picture"), ctx.actions  # checked frame attached
assert ctx.state.get("arrivals_tonight_count") == 1

# 2. Must act, but with NO picture when no frame passes the check:
# an unchecked frame must never be presented as the arrival picture.
ctx = Context()
ctx.checks = [(False, 0.9), (False, 0.8), (False, 0.7)]
ctx = replay(rule, [("on", "car_driving_in", ts(23))], ctx)
assert len(notifies(ctx)) == 1, ctx.actions
assert not notifies(ctx)[0].get("picture"), ctx.actions

# 3. Must not act in daytime.
ctx = Context()
ctx.checks = [(True, 0.95), (True, 0.95), (True, 0.95)]  # should stay unused
ctx = replay(rule, [("on", "car_driving_in", ts(14))], ctx)
assert len(notifies(ctx)) == 0, ctx.actions

# 4. Edge: a second car drives in while the first is still parked.
# There is no presence/on-off pair to wait for and no 'active' flag:
# two moment events minutes apart are two arrivals and two alerts.
ctx = Context()
ctx.checks = [(True, 0.9), (False, 0.5), (False, 0.5),
              (True, 0.85), (False, 0.5), (False, 0.5)]
t0 = ts(23)
ctx = replay(
    rule,
    [
        ("on", "car_driving_in", t0),
        ("off", "car_driving_in", t0),          # moment ends at once
        ("on", "car_driving_in", t0 + 1800),    # second car, first still parked
        ("off", "car_driving_in", t0 + 1800),
    ],
    ctx,
)
assert len(notifies(ctx)) == 2, ctx.actions
assert ctx.state.get("arrivals_tonight_count") == 2

# 5. Edge: the same arrival re-reported a couple of seconds later (late
# review of the same frames) is de-duplicated, not a second alert.
ctx = Context()
ctx.checks = [(True, 0.9), (False, 0.5), (False, 0.5)]
t0 = ts(2)
ctx = replay(
    rule,
    [("on", "car_driving_in", t0), ("on", "car_driving_in", t0 + 1)],
    ctx,
)
assert len(notifies(ctx)) == 1, ctx.actions
assert ctx.state.get("arrivals_tonight_count") == 1

# 6. Edge: a second car following ~6s behind the first (past the
# sensor's 4s clear_after_s re-fire gap) is a new arrival, not a
# duplicate, and must alert again.
ctx = Context()
ctx.checks = [(True, 0.9), (False, 0.5), (False, 0.5),
              (True, 0.85), (False, 0.5), (False, 0.5)]
t0 = ts(23, 30)
ctx = replay(
    rule,
    [("on", "car_driving_in", t0), ("on", "car_driving_in", t0 + 6)],
    ctx,
)
assert len(notifies(ctx)) == 2, ctx.actions
assert ctx.state.get("arrivals_tonight_count") == 2

print("all tests passed")
