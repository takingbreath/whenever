from rule_runtime import ModelError


def on_event(sensor, t, ctx):
    if sensor != "gate_open":
        return
    count = int(ctx.state.get("count", 0)) + 1
    ctx.state["count"] = count
    ctx.state["last_open_t"] = t
    ctx.state["last_occurrence"] = count

    ctx.record("gate_opened", at=t, occurrence=count)
    ctx.show("Gate openings", count)
    ctx.show("Gate", "open")

    # ctx.event.proof already passed the sensor's paid check and shows the
    # open gate, so alert at once without a second model check.
    picture = ctx.event.proof if ctx.event is not None else None
    try:
        ctx.notify("Someone opened the gate", picture=picture)
    except ModelError:
        ctx.log("Gate opened, but the alert could not be sent")


def on_end(sensor, t, duration, ctx):
    if sensor != "gate_open":
        return
    occurrence = int(ctx.state.get("last_occurrence", ctx.state.get("count", 0)))
    ctx.record("gate_closed", duration_s=duration, occurrence=occurrence)
    ctx.show("Gate", "closed")
    ctx.log(f"Gate closed after {int(duration)} seconds")
