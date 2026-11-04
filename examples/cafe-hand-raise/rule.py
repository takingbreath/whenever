"""Guest raises a hand in the Cafe: alert Waiter, escalate to Manager after 60s without staff."""
from rule_runtime import ModelError, place_of, seat


def _pose_flag(person, name):
    pose = getattr(person, "pose", None)
    if isinstance(pose, dict):
        return pose.get(name)
    return getattr(pose, name, None) if pose is not None else None


def _tables(ctx):
    try:
        return ctx.places("table", camera="Cafe") or []
    except Exception:
        return []


def _people_at_tables(ctx, frames=None):
    tables = _tables(ctx)
    try:
        if frames:
            people = ctx.detect("person", frames, camera="Cafe", places=tables, pose=True)
        else:
            people = ctx.detect("person", camera="Cafe", places=tables, pose=True)
    except Exception:
        return [], tables
    people = [p for p in (people or []) if getattr(p, "camera", "Cafe") in ("Cafe", "", None)]
    return people, tables


def _find_table(ctx):
    """Return (table_name, raiser_found_or_None) for the hand-raise event.

    Only a person whose pose shows the raised hand may identify the table.
    A seated guest without a raised hand is never guessed to be the caller:
    when the raiser cannot be located, the table stays unknown.
    """
    frames = []
    try:
        if ctx.event is not None:
            frames = ctx.event.frames or []
    except Exception:
        frames = []
    people, tables = _people_at_tables(ctx, frames)
    raisers = [p for p in people if _pose_flag(p, "hand_up") is True]
    if not raisers:
        return "a table", None
    for p in raisers:
        table = getattr(p, "place", None)
        if not table and tables:
            try:
                table = place_of(p, tables, point=seat, near=0.5)
            except Exception:
                table = None
        if table:
            return table, p
    # The raiser was seen, but no table could be tied to them. Their Found
    # can still outline the caller in the alert picture; the table is unknown.
    return "a table", raisers[0]


def _staff_at_table(ctx, job, people):
    """Return a Found only when an identified staff member is at the table.

    Standing at the table is not enough: a joining guest, a guest who stood
    up, or any other visitor could do that. Staff arrival is proved only by
    ctx.who identifying the person at that table with role "staff".
    """
    table = job.get("table")
    if not table or table == "a table":
        return None
    at_table = [p for p in people if getattr(p, "place", None) == table]
    if not at_table:
        return None
    try:
        ctx.who(at_table, look=False)
    except Exception:
        return None
    for p in at_table:
        who = getattr(p, "who", None)
        if isinstance(who, dict) and who.get("role") == "staff" and (who.get("sure") or 0) >= 0.7:
            return p
    return None


def on_event(sensor, t, ctx):
    if sensor != "hand_raised":
        return
    table, guest = _find_table(ctx)

    jobs = ctx.state.setdefault("jobs", {})
    for job in jobs.values():
        if job.get("table") == table and not job.get("done"):
            ctx.log(f"Guest at {table} raised a hand again while already waiting for service")
            return

    occurrence = int(ctx.state.get("occurrence", 0)) + 1
    ctx.state["occurrence"] = occurrence
    key = f"{table}@{int(t)}#{occurrence}"
    jobs[key] = {
        "table": table,
        "t": float(t),
        "deadline": float(t) + 60.0,
        "occurrence": occurrence,
        "escalated": False,
        "done": False,
    }

    picture = guest if guest is not None else (ctx.event.proof if ctx.event is not None else None)
    if table == "a table":
        text = "A guest in the Cafe has raised a hand and is calling for service, but the table could not be identified. Please look for the guest with a raised hand."
    else:
        text = f"A guest at {table} has raised a hand and is calling for service. Please go to {table}."
    ctx.notify(text, picture=picture, at=t, to="Waiter")
    try:
        ctx.record("hand_raised", table=table, seconds=0, occurrence=occurrence)
    except Exception:
        pass
    ctx.show(table, "Waiting for staff, 0 min")
    ctx.log(f"Waiter alerted: guest at {table} raised a hand; will escalate at 60s if no identified staff member arrives")


def on_end(sensor, t, duration, ctx):
    # The hand going down does not cancel the call; the job in ctx.state
    # continues until identified staff arrives or 60s pass.
    return


def on_tick(t, ctx):
    jobs = ctx.state.get("jobs") or {}
    active = {k: j for k, j in jobs.items() if not j.get("done")}
    if not active:
        return

    due = any(t >= j.get("deadline", t + 1) and not j.get("escalated") for j in active.values())
    last = ctx.state.get("last_staff_check", 0)
    if not due and t - last < 4:
        return
    ctx.state["last_staff_check"] = float(t)

    people, _ = _people_at_tables(ctx)
    latest_frame = None
    for p in people:
        if getattr(p, "frame", None) is not None:
            latest_frame = p.frame

    changed = False
    for key, job in list(active.items()):
        staff = _staff_at_table(ctx, job, people)
        if staff is not None:
            waited = max(0.0, t - job.get("t", t))
            job["done"] = True
            changed = True
            try:
                ctx.record("staff_reached", table=job.get("table"), seconds=round(waited, 1),
                           occurrence=job.get("occurrence"))
            except Exception:
                pass
            ctx.show(job.get("table"), "Staff at table")
            ctx.log(f"Identified staff member reached {job.get('table')} after {int(waited)}s; no manager alert needed")
            continue

        waited = max(0.0, t - job.get("t", t))
        ctx.show(job.get("table"), f"Waiting for staff, {int(waited // 60)} min {int(waited % 60)}s")

        if t >= job.get("deadline", t + 1) and not job.get("escalated"):
            job["escalated"] = True
            job["done"] = True
            changed = True
            table = job.get("table")
            if table == "a table":
                text = "No identified staff member reached the guest who raised a hand in the Cafe within 1 minute (the table could not be identified). Please help that guest now."
            else:
                text = f"No identified staff member reached {table} within 1 minute after a guest raised a hand for service. Please help at {table} now."
            ctx.notify(text, picture=latest_frame, at=job.get("t"), to="Manager")
            try:
                ctx.record("escalated", table=table, seconds=60)
            except Exception:
                pass
            ctx.show(table, "Escalated to manager")
            ctx.log(f"Manager alerted: no identified staff reached {table} within 60s of a raised hand")

    if changed:
        ctx.state["jobs"] = {k: j for k, j in jobs.items() if not j.get("done")}
