import rule
from rule_runtime import Context, Found, Place, replay


def guest():
    return Found(camera="Cafe", label="person", box=(0.25, 0.3, 0.35, 0.55),
                 place="table 1", tall=1.8, pose={"hand_up": True, "sitting": True, "lying": False})


def seated_without_raise():
    return Found(camera="Cafe", label="person", box=(0.25, 0.3, 0.35, 0.55),
                 place="table 1", tall=1.8, pose={"hand_up": False, "sitting": True, "lying": False})


def staff():
    return Found(camera="Cafe", label="person", box=(0.36, 0.2, 0.44, 0.6),
                 place="table 1", tall=2.6, stayed=5.0, pose={"hand_up": False, "sitting": False, "lying": False})


def base_ctx(detected):
    ctx = Context()
    ctx.place_list = [Place("table 1", "Cafe", (0.2, 0.3, 0.5, 0.6))]
    ctx.detected = detected
    return ctx


def notifies(ctx):
    return [a for a in ctx.actions if a.get("do") == "notify"]


# 1. Must act: waiter at once, manager after 60s if nobody comes.
ctx = base_ctx([guest()])
ctx = replay(rule, [("on", "hand_raised", 100.0), ("tick", 161.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 2, notes
assert notes[0].get("to") == "Waiter" and "table 1" in notes[0]["text"], notes
assert notes[1].get("to") == "Manager" and "1 minute" in notes[1]["text"], notes
assert any(a.get("do") == "record" and a.get("name") == "escalated" for a in ctx.actions), ctx.actions

# 2. Must not escalate: an identified staff member reaches the table in time.
ctx = base_ctx([guest()])
ctx = replay(rule, [("on", "hand_raised", 100.0)], ctx)
assert len(notifies(ctx)) == 1
ctx.detected = [guest(), staff()]
ctx.matches = [None, {"name": "Waiter Asha", "role": "staff", "sure": 0.9, "by": "face", "id": "w1"}]
ctx = replay(rule, [("tick", 130.0), ("tick", 170.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 1 and notes[0].get("to") == "Waiter", notes
assert not any(a.get("do") == "record" and a.get("name") == "escalated" for a in ctx.actions), ctx.actions

# 3. A standing non-staff visitor at the table does NOT cancel the wait.
ctx = base_ctx([guest()])
ctx = replay(rule, [("on", "hand_raised", 100.0)], ctx)
ctx.detected = [guest(), staff()]  # same standing shape, but identified as a regular, not staff
ctx.matches = [None, {"name": "Regular 1", "role": "regular", "sure": 0.95, "by": "face", "id": "r1"}]
ctx = replay(rule, [("tick", 130.0), ("tick", 161.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 2 and notes[1].get("to") == "Manager", notes
assert any(a.get("do") == "record" and a.get("name") == "escalated" for a in ctx.actions), ctx.actions

# 4. No raised-hand person located: do not guess a seated guest's table.
ctx = base_ctx([seated_without_raise()])
ctx = replay(rule, [("on", "hand_raised", 100.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 1 and notes[0].get("to") == "Waiter", notes
assert "table 1" not in notes[0]["text"] and "could not be identified" in notes[0]["text"], notes

# 5. Edge: repeated raise at same table while waiting does not re-alert the waiter.
ctx = base_ctx([guest()])
ctx = replay(rule, [("on", "hand_raised", 100.0), ("on", "hand_raised", 110.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 1 and notes[0].get("to") == "Waiter", notes

# 6. Edge: nobody detected at all, still alert waiter then manager.
ctx = base_ctx([])
ctx = replay(rule, [("on", "hand_raised", 200.0), ("tick", 261.0)], ctx)
notes = notifies(ctx)
assert len(notes) == 2 and notes[0].get("to") == "Waiter" and notes[1].get("to") == "Manager", notes

print("all tests passed")
