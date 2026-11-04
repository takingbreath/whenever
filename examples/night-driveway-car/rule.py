"""At night, tell me when a car drives into the driveway.

The sensor itself is the arrival detector: a *moment* sensor whose facts
must show a car at the driveway entrance and the same car further inside
the driveway in neighbouring frames. It is not a presence sensor, so a
second car driving in while a first car is still parked fires a new
event.

Code does the rest:
- night gating (19:00-05:59 local),
- de-duplicating the same arrival within a few seconds (a late review
  of the same frames can re-fire the same moment),
- verifying, with ctx.check on that exact frame, any single frame
  before it is attached to the alert as a picture. If no frame passes
  the check, the alert is sent with no picture rather than presenting
  an unchecked frame as evidence of an arrival.
"""
from datetime import datetime

try:
    from rule_runtime import ModelError
except Exception:  # pragma: no cover - runtime always provides it
    class ModelError(Exception):
        pass


NIGHT_START_HOUR = 19
NIGHT_END_HOUR = 6  # exclusive
# Same physical arrival re-reported (e.g. by the delayed review of the
# same frames) within a couple of seconds is one arrival. This must stay
# below the sensor's 4s clear_after_s re-fire gap (~6s between following
# cars), so a second car following ~6s behind is never dropped.
DEDUP_S = 2
PICTURE_CLAIM = (
    "A car is visible in the driveway in this frame, at or just inside "
    "the driveway entrance on the street side of the driveway, rather "
    "than parked up against the house or garage."
)


def _is_night(t):
    hour = datetime.fromtimestamp(t).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def _checked_picture(ctx):
    """Return a Frame that ctx.check itself passed for PICTURE_CLAIM, or None.

    The check judges each candidate frame alone; only the exact frame
    that passed (best first) may be attached to the alert. The sensor's
    proof frame is used only as a reference for which car, never
    attached unchecked.
    """
    event = ctx.event
    if event is None:
        return None
    frames = list(getattr(event, "frames", None) or [])
    if not frames:
        return None
    proof = getattr(event, "proof", None)
    try:
        checks = ctx.check(PICTURE_CLAIM, frames, reference=proof)
    except ModelError:
        ctx.log("Picture check failed (model error); alerting without a picture")
        return None
    for check in checks or []:
        if check.passed and check.confidence >= 0.7:
            return check.frame
    return None


def on_event(sensor, t, ctx):
    if sensor != "car_driving_in":
        return

    last = ctx.state.get("last_arrival_t")
    if last is not None and 0 <= t - float(last) < DEDUP_S:
        ctx.log("Same driveway arrival reported again within a couple of seconds; ignoring duplicate")
        return

    if not _is_night(t):
        ctx.log("Car drove into driveway during the day; no night alert")
        ctx.show("Driveway", "Car arrived (daytime)")
        return

    # The moment sensor has already proved the arrival across frames
    # (car at the entrance, same car further inside). Record this
    # arrival even before looking for a presentable picture, so a
    # second car arriving while another is parked still counts.
    ctx.state["last_arrival_t"] = t
    today = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    if ctx.state.get("count_date") != today:
        ctx.state["count_date"] = today
        count = 0
    else:
        count = int(ctx.state.get("arrivals_tonight_count", 0))
    count += 1
    ctx.state["arrivals_tonight_count"] = count

    ctx.record("car_arrival", hour=datetime.fromtimestamp(t).hour, count=count)
    ctx.show("Cars in tonight", count)
    ctx.show("Driveway", "Car driving in")

    picture = _checked_picture(ctx)
    if picture is not None:
        ctx.notify("A car is driving into the driveway", picture=picture, at=t)
    else:
        ctx.log("No single frame passed the picture check; alerting without a picture")
        ctx.notify("A car is driving into the driveway", at=t)


def on_end(sensor, t, duration, ctx):
    # A moment sensor ends at once (duration 0). Nothing to clear: a
    # parked car staying in the driveway must not block, or be mistaken
    # for, the next arrival.
    if sensor == "car_driving_in":
        ctx.log("Driveway arrival moment ended")


def on_tick(t, ctx):
    # No model calls here. Keep the live values honest across dates.
    today = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    if ctx.state.get("count_date") not in (None, today):
        ctx.state["arrivals_tonight_count"] = 0
        ctx.state["count_date"] = today
        ctx.show("Cars in tonight", 0)
    ctx.show("Night watch", "Night" if _is_night(t) else "Day")
