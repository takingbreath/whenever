import rule
from rule_runtime import Context, replay

# Case that must act: gate opens -> one alert with picture, one record.
ctx = Context()
ctx = replay(rule, [("on", "gate_open", 100.0)], ctx)
notifies = [a for a in ctx.actions if a["do"] == "notify"]
records = [a for a in ctx.actions if a["do"] == "record"]
assert len(notifies) == 1, ctx.actions
assert "opened the gate" in notifies[0]["text"]
assert notifies[0].get("picture") is not None, "alert must carry the proof picture"
assert any(r["name"] == "gate_opened" and r["values"]["occurrence"] == 1 for r in records)

# Case that must not act: unrelated sensor fires.
ctx2 = Context()
ctx2 = replay(rule, [("on", "something_else", 200.0)], ctx2)
assert not [a for a in ctx2.actions if a["do"] == "notify"], ctx2.actions

# Edge: gate closes -> duration recorded, no second alert; count increments next opening.
ctx3 = Context()
ctx3 = replay(rule, [("on", "gate_open", 300.0), ("off", "gate_open", 345.0),
                     ("on", "gate_open", 400.0)], ctx3)
notifies3 = [a for a in ctx3.actions if a["do"] == "notify"]
closed = [a for a in ctx3.actions if a["do"] == "record" and a["name"] == "gate_closed"]
assert len(notifies3) == 2, ctx3.actions
assert len(closed) == 1 and closed[0]["values"]["duration_s"] == 45.0, ctx3.actions
assert ctx3.state["count"] == 2

print("all tests passed")
