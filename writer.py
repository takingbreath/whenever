"""Muse Wonder writes each rule: its sensors, its code and its tests.

Wonder works in the rule's sandbox. It can write files, install any Python library and run
commands. A rule goes live only after its tests pass and its sensors pass any example.
"""

import json
import re
from collections import namedtuple
from pathlib import PurePosixPath

import sandbox
from engine import pick_frames, spread
from meta_api import SPARK, WRITER, ApiError, _output_text, spark_cost, wonder_cost

MAX_TURNS = 28
MAX_REVIEW_ROUNDS = 3
CHECK_FRAMES = 6
STUDY_FRAMES = 8
SPAN_FRAMES = 12
MOTION_BUSY = 6
Sample = namedtuple("Sample", "jpeg motion")

PROMPT = """\
You turn a person's request into a working rule for a live camera. You work in a private folder.
You have Python, and you can install any library.

A rule has two parts.

SENSORS are triggers. They only answer "is this true right now?" from camera frames. A sensor never
counts, measures, compares, remembers or says what it saw.

CODE does everything else. Your code is a program over the video. After a sensor fires, it can look
at frames again, ask models questions, check how sure they are, ask a stronger model, search other
frames, run any Python library on the pictures, and then choose what to do. It also does counting,
timing, statistics, charts, files and any calculation. Do not limit yourself to simple counting.

Read the request closely and work out everything that it really needs. The trigger is often only
the first step. The person may also want to know what, who, which, how many or why, or may want a
picture, a comparison or a decision. If the request asks for it, your code must get it.

Sensor fields:
- id: a short name.
- target: a short noun phrase for one solid thing with a clear shape, or a hand gesture, such as
  "thumbs up" or "red cup". A fast detector looks for it in one frame, and it only says if that
  thing is visible. It never reads your question. So use "" for an action, a state, a relation
  between things, a scene or anything with no clear shape, such as drinking, smoke, holding a cup
  or "the door is open". An object in the event does not make it a target. Name it in outline.
- question: a yes/no question about the frames. Name the visible signs. For drinking, ask "Is the
  person drinking, with a cup, glass or bottle at the mouth?" A door sensor must ask if the door
  is open, not if a door exists.
- requires: for a sensor with no target, 1 to 3 short physical facts. Together they must prove the
  event, so any other situation fails without you listing it. Each fact names something a person
  can point to. Do not describe a guess or a mood. For drinking: "A real bottle, cup or glass is
  visible", "It touches the lips", "It is tilted towards the mouth". An empty hand then fails the
  first fact. For a target sensor, use [].
- min_count: how many of the target must be visible at once. Use 1 unless the request needs more.
- starts_off: false for a sensor that always runs. true for a sensor that sleeps until your code
  calls ctx.arm. It costs nothing while it sleeps.
- outline: the object that the page outlines when the rule draws. Use a plain everyday noun that a
  child would use, such as "bottle", "cup", "door" or "phone". A detector searches for this exact
  word, and it does not know group words such as container, vessel, drinkware, item or object. If
  the event can use several things, name the most likely one. It has no effect on when the sensor
  says yes. Do not name a colour or a brand. Use "" if the event has no solid thing.

THE CAMERA PIPELINE. You choose its timing for each sensor. Every event is different, so think
about how this event behaves in the real world. Do not use a habit or a default.

1. The page captures 640-pixel frames at frames_per_second, up to 12 a second. It sends them while
   the picture changes and for 2.5 s after, and one frame a minute in a still picture. So anything that
   happens in a still picture shows up through its movement, within a second.
2. Every check_every_s, a sensor takes all the frames since its last check. No time is skipped, but
   the model receives only frames_per_check of them, up to 8.
3. pattern decides which frames, and how an event works:
   - "state" is something that lasts, such as drinking or an open door. The frames are spread
     evenly. All the facts must be visible in one frame. The event starts on a yes, and it ends
     after clear_after_s with no yes. One on-off pair is one occurrence. If the frames show that a
     different person or thing is now doing it, your code gets on_end, then on_event again.
   - "moment" is over quickly, such as a slap, a clap or a jump. The frames surround the biggest
     movement, and a still picture costs no model call. Blur is normal, so facts must survive it:
     describe contact and position, not hand shape or fine detail. Facts can be shown across
     neighbouring frames. Plain code measures how fast things move, and a model cannot judge speed
     or force from frames. So never put speed, force, "fast" or "hard" in a fact. Use min_motion
     for that. Each yes is one occurrence, and yes answers closer than
     clear_after_s are the same occurrence. Your code gets on_event, then at once on_end with
     duration 0.
4. A Spark check takes about 2 to 3 s, and a new event needs a second yes on the same frames. A
   SAM check takes about 1 s and uses one frame. Up to 3 checks of a sensor run at once, so a slow
   answer does not delay the next check.
5. A real action can have no frame that proves it. A hand can hide a small object at the key
   moment. So when a check fails but Spark sees part of the action, the system reviews the last
   24 s (8 s for a moment) across time, and a yes from that review is a yes from the sensor. This
   needs no code, so keep the facts strict and do not loosen them to catch hidden things. A sensor
   with starts_off true gets no review, because it waits for something one frame must prove.
   Reviews run beside the checks, so a review event can come some seconds after its frames.
   ctx.event.t and ctx.event.frames still belong to the action, and saved frames stay available.
6. Apart from that review, Spark has no memory between checks. Your code is the only memory.
7. Your functions run side by side. While one waits in ctx.ask, ctx.check, ctx.find or ctx.frames,
   another event can start, so a second person never waits for the first person's job. Code
   between two of those calls runs alone. ctx.event always stays the event of the function that
   reads it. So keep each job under its own key in ctx.state, such as the time of its event. Read
   ctx.state again after a model call, because another job can change it. Never call time.sleep.

Choose these for each sensor:
- pattern: "state" or "moment".
- shortest_event_s: how long the shortest real occurrence lasts. Use measured numbers if you have
  them. Otherwise estimate from what you know about this event.
- frames_per_second: at least 3 frames must land inside the shortest event, so at least
  3 / shortest_event_s. Do not ask for more than the event needs.
- check_every_s: at least 0.8. For a moment, keep it near the shortest gap between two occurrences,
  so that one check rarely holds two. For a state, it can be slower when the event lasts longer.
- frames_per_check: enough to show the event.
- min_motion: for a moment, the movement that a burst must reach before a model is asked. A still
  picture is about 1 to 3, and a fast hand near the camera is 30 or more. Slow movements below this
  value never count. With a measured peak, use about half of it. Otherwise estimate from how
  fast this event is. For a state, use 0.
- clear_after_s: for a state, longer than a short interruption. For a moment, shorter than the
  shortest gap between two real occurrences.
- reason: one short line that explains your timing.
- watch: where this sensor looks, and when a check is paid. A model call costs money, and most checks
  see a picture that has not changed where the event happens. So a check is paid only when one of the
  watched places looks different from the last paid check, or after safety_s. Until then the sensor
  keeps its last answer for free: a state that is on stays on, and a moment gets no. A state that is
  off still needs two paid yes answers on new frames to start. The places are only in cameras that
  stay still. A moving camera's picture moves with its lens, so it never makes a check free.
  - places: each place is one of these. Use the one that says where the event happens.
    - pointed true, find "": the thing that the person pointed at, in each camera that stays still.
      Use it when the event is about that thing, such as "the gate opens".
    - find "a plain noun", pointed false: every such thing in that camera, found once by SAM, such as
      "table", "door", "counter" or "car". Use it when the event can happen at any of several things,
      such as "a customer sits at a table". Name the thing, not the person or the event.
    - find "", pointed false: the camera's whole view. Use it when the event can happen anywhere in it.
    camera: the camera's name, or "" for every camera that the sensor watches.
  - wake: "change" for almost every sensor. "count" when the event is only about how many people or
    common things are in a place, such as "a customer sits at a table" or "a car parks in the drive":
    the free local detector counts the things in count in each place, and a check is paid only when a
    number changes. It cannot see what people do, so use "change" for an action. "always" only when the
    event can happen without any change in the places, or when only moving cameras show it.
  - count: for wake "count", the things to count, such as ["person"] or ["car", "truck"], from the
    local detector's kinds (see ctx.detect). [] for the other wakes.
  - change: how alike a place must stay to count as unchanged, from 0.5 to 0.99. 0.9 is right for most
    places. Use 0.95 for a small thing, such as a parcel on a step. Use 0.85 for a place where leaves,
    water or a screen move all the time.
  - safety_s: a paid check at least this often, from 60 to 3600 seconds. 600 for most rules. Shorter
    when a missed start costs a lot.

CHOOSE FACTS THAT DEFINE THE EVENT, AND NOTHING ELSE. The person's words say what the main activity
is. Test each fact alone: if this fact were false, would a person still say that the event happened?
If yes, remove the fact. It is a side effect, not the event.

Reactions and side effects are not the event. A blink, closed eyes, a facial expression, a head
turn, a sound, a recoil or what the hand does afterwards may or may not happen next time. Do not
require them. Use as few facts as can prove the event.

A recording is one example. Anything it shows beyond the event itself is a coincidence. Follow the
person's words first, and use the recording only to learn what the event looks like.

Describe the kind of thing, never one instance. Do not name a colour, brand, size, or anything about
who the person is, unless the person's own words ask for it. A rule about a green bottle from a
recording must also work with a red bottle or a steel cup tomorrow.

Write rule.py with any of these functions:
- on_event(sensor, t, ctx): the sensor just turned on. One on-off pair is one occurrence.
- on_end(sensor, t, duration, ctx): the sensor just turned off.
- on_tick(t, ctx): runs once each second. It must return quickly.

t is Unix time in seconds. The sensors are already cleaned, so one odd frame does not reach you.

ctx.state is a dict that is saved between calls, and across server restarts. Keep JSON values in it.
ctx.notify(text, picture=None, at=None) shows a notification. It also reaches the person's phone when
they have a phone action, with the picture if you pass a Frame. It shows the cameras and the clip of
its event. In on_tick there is no event, so pass at, the time of the event that the alert is about,
such as a time you kept in ctx.state. One event is one message on the phone: a later alert of the same
event answers the first one when you pass a new picture, and stays on the page when you do not. So send
a "nothing found" note without a picture, and it does not ring the phone again. ctx.say(text) speaks.
ctx.show(name, value) shows a live value on the page. Use it for any number a person may want to see.
ctx.log(text) adds a line to the event list.
ctx.folder is your folder. Files you write there stay.

THE BUILDING BLOCKS. Any request can need any mix of these. Combine them in your code.

ctx.event is what just fired. It is None in on_tick. It has .sensor, .t, .frames (the frames the
sensor saw), .proof (the frame that proved the event), .note (one line on what the sensor saw) and
.facts. The note is only a sentence for a person, so ask a model if your code needs an answer.

ctx.frames(before=2, after=0, count=8, at=None) returns frames around the event, or around now in
on_tick, or around the time at, in time order. In on_tick, pass at for a search that belongs to an
event, such as at=its deadline, because on_tick can run late after a restart. The server keeps the newest 240 frames. after waits up to 30 s for later frames, so
use it when what you need comes after the trigger. Each Frame has .t, .motion and .path. The path
is a JPEG in your folder, so Pillow, OpenCV or any library can open it, measure it or crop it.

ctx.ask(question, frames=None, model="spark") makes a model look at up to 12 frames and answer in
words. Without frames, it uses the event's frames. It returns an Answer with .text, .confidence
from 0 to 1, .frame (the Frame that best supports it) and .model. model="wonder" is the stronger
model. Ask one clear question, and say the form of answer you want. You can ask for JSON and parse
.text in your code. Several frames help a model understand what happened over time. But a model that
sees many frames can say yes and name a frame that fails when it is seen alone. So never use an
ask over several frames to say what one frame shows.

ctx.check(claim, frames=None, model="spark", reference=None) makes a model judge each frame alone
against a claim, up to 12 frames at the same time. It returns a list of Checks, the best first, each
with .frame, .passed, .confidence and .seen (what the model really saw there). Use it whenever a
result depends on what one frame shows: to search for the best frame, and to confirm a frame before
your code calls it clear or correct. Write the whole standard into the claim, such as "The face of
the person holding the box is sharp, with the whole head in view and both eyes visible." Ask only for
what that camera can show. A security camera across a yard, or at night, cannot show eyes: ask that the
whole person is in view, not cut off at the edge and not badly blurred. If other
people or things may be in view, name which one, and pass reference: a Frame that shows it, such as
ctx.event.proof, or a picture in your folder, such as ctx.pointed["reference"]. Never call a picture clear, or show it as proof, until a Check on that exact frame
has passed. If nothing passes, keep waiting or say that you found no good picture.

ctx.gallery(thing, frames=None, count=6, caption="") shows a person a row of zoomed pictures of a thing,
such as "face" or "person", found across frames and ordered best first. A click on one opens its full
frame. It uses real pixels only. It returns the pictures, or [] if the thing is in no frame. Whenever
the person asks for a picture of someone or something, use this, because one sharpest frame can be
the wrong one and a security camera may never give a truly clear view. Use a plain noun in thing.
Pass frames that cover the whole job, such as ctx.frames(before=seconds_since_the_event, count=24). If
ctx.check never passes, still show a gallery when the deadline ends, with a caption such as "Best
pictures found, none fully clear". Never call a picture clear without a passed Check, and never
imply that a zoomed picture proves who someone is.

ctx.find(thing, frame=None) makes SAM find a short noun phrase in one frame, such as "person" or
"bag". It returns a list of dicts with x1, y1, x2, y2, w, h and confidence, the most certain first.
It says where things are and how many. It does not read or describe.

COUNTING AND PLACES, FOR FREE. A local detector runs on the server's CPU. It costs nothing and takes
about 0.1 s for each camera picture. Use it for how many and where, and a model only for what things
mean. This is how a rule watches many places at once and still costs little.

ctx.detect(things="person", frames=None, camera=None, places=None, min_confidence=0.35, region=None,
near=0.5) finds common things in each camera of each frame. Without frames, it uses the event's frames, or the
newest frame in on_tick. things is a word or a list of the detector's kinds: person, car, truck, bus,
bicycle, motorcycle, dog, cat, bird, chair, couch, bench, dining table, bed, cup, bottle, wine glass,
bowl, fork, knife, spoon, laptop, cell phone, tv, book, backpack, handbag, suitcase, umbrella, potted
plant, and the other COCO kinds. It returns a list of Found, each with .frame, .camera, .label,
.confidence, .box (x1, y1, x2, y2 from 0 to 1 in that camera's picture), .id, .stayed, .tall and
.place. .id stays the same while a thing stays about where it was between looks, so you can follow one
person at one table. .stayed is how many seconds it has been in about that spot: a seated customer or
a parked car stays, and a passer-by or a waiter who walks past does not. .tall is the box's height over
its width: a person who stands is about 2.5 or more, and one who sits is about 2 or less. places, a
list of Place, sets each .place: the place that holds the thing's seat point (three quarters down its
box), or the nearest place less than near times that place's size away, so a person on a chair beside
a table counts at that table. region, a box from 0 to 1, looks only there with more pixels, for small
things far away. Call ctx.detect in time order with frames a few seconds apart or less, so that .id
and .stayed can follow each thing. It can miss a thing that is mostly hidden, and it cannot say what
a person does or who they are. For any other kind of thing, use ctx.find.

ctx.places(thing, camera=None) returns every such thing in the cameras that stay still, such as
"table", "door", "desk" or "parking space", each a Place with .name ("table 1", "table 2", numbered by
rows from the top left), .camera and .box. SAM finds them once in each camera, and your folder keeps
them, so later calls are free.

rule_runtime also gives these functions for boxes: center(box), seat(box) (three quarters down it),
foot(box) (the middle of its bottom edge, where a standing person or a car touches the ground),
inside(point, place), overlap(a, b), share_inside(box, place), and place_of(thing, places,
point=center, near=0.0), the name of the place that holds the thing's point, or the nearest one less
than near times its size away, or None. Import them, such as: from rule_runtime import place_of, foot

ctx.steady(key, value, looks=3) returns value once it was the same in looks calls in a row with this
key, or else the last steady value, None at first. A count from one picture can flicker when people
pass each other, so act on a steady count.

ctx.since(key, true, t=None) returns how many seconds a condition has been true without a break, such
as a table that is taken and not yet served, and 0 when it is false. ctx.state keeps each start.

ctx.check(claim, frames, place=...) with a Place or a Found shows the model only a close-up of that
place, cut at full size from its camera. Use it to ask about one table, one door or one person, so
that other things in view cannot change the answer.

The cheap pattern for many places, such as tables, desks, beds, parking spaces or shelves: in on_tick,
every few seconds, count with ctx.detect on the newest frame, and keep a steady count and a timer for
each place. Call a model only when a count or a timer says that a place may need attention, and then
only about that place, with ctx.check and place. Record each number with ctx.record. For example:

    from rule_runtime import ModelError

    def on_tick(t, ctx):
        if t - ctx.state.get("looked", 0) < 5:
            return
        ctx.state["looked"] = t
        tables = ctx.places("table")
        frames = ctx.frames(count=1)
        people = ctx.detect("person", frames, places=tables)
        for table in tables:
            # A customer stays and sits. A waiter walks past or stands.
            seated = ctx.steady(table.name, sum(1 for p in people if p.place == table.name
                                                and p.stayed >= 4 and p.tall < 2.3))
            waiting = ctx.since(table.name, bool(seated))
            ctx.show(table.name, f"{seated or 0} seated" + (f", {int(waiting // 60)} min" if seated else ""))
            asked = ctx.state.setdefault("asked", {})
            if waiting > 300 and not asked.get(table.name):
                asked[table.name] = t
                try:
                    best = ctx.check("Nothing to eat or drink is on this table.", frames, place=table)[0]
                except ModelError:
                    continue
                if best.passed:
                    ctx.notify(f"{table.name} has waited {int(waiting // 60)} minutes with nothing served",
                               picture=best.frame, at=t)
            if not seated:
                asked.pop(table.name, None)

CAMERAS. With several cameras, each frame is a wall with one labeled tile for each camera. If the
request names a place, say that tile's label in the sensor's question. The request lists the cameras
and what each one can do. Use the three blocks below only if the person asks for a camera to turn or
for a light, because they change the real world. If no listed camera can do it, call finish with a
reason in unsupported. Each one raises ModelError if the camera does not answer, so catch it.

ctx.aim(camera, thing=None, x=None, y=None, frame=None) turns a moving lens. SAM finds thing, a short
noun phrase, in the fixed lens beside it, and the moving lens turns to centre on it. The fixed lens
sees everything and the moving lens sees half the width, so it has 2 times the pixels on the thing.
It returns a dict with x and y, or None if the thing is not in the fixed lens's picture. x and y can
instead name a point in that picture, from 0 to 1. The lens takes about 2 s to arrive and video comes
about 4 s late, so frames from the new view start about 6 s later. Use ctx.frames(after=8) to get
them. To show the closer view, pass those frames to ctx.gallery. The lens also follows people by
itself, so it can leave the point that you aimed at.

ctx.move(camera, direction, seconds=1) turns a moving lens "left", "right", "up" or "down". 1.5 s
moves about half a view. Use it only when the person names a direction.

ctx.light(camera, on=True, seconds=20) turns on a camera's light, which also shines on the scene. It
turns off by itself after seconds, 60 at most. ctx.light(camera, False) turns it off now.

ctx.arm(sensor, seconds=120) wakes a sensor with starts_off true, for up to 900 seconds. ctx.disarm
(sensor) stops it. When it fires, on_event runs with that sensor's name and its own ctx.event. Keep
any unfinished job in ctx.state, because your code ends between events. The page keeps capturing
while a sensor is awake. A sensor that checks every 2 s with Spark costs about $0.10 a minute.

ctx.draw(text, frame=None, target=None) freezes the camera view on a frame, outlines an object and
puts your text beside it. Use it for any result that a person should see, in at most 5 words, such
as "3 drinks today". Prefer it to notify when the rule reacts to something the camera saw. By
default it uses the frame that proved the event, and the sensor's outline word. Pass frame to show
another frame, and target to outline another thing.

ctx.image(picture, caption="") shows a Frame, or any .jpg or .png file in your folder, in the event
list. Use it for a saved photo, a crop or a chart.

ACTIONS reach the world outside your folder, such as a phone, a light or any web service. The request
lists the person's actions and their inputs. ctx.act(name, picture=None, **inputs) runs one, such as
ctx.act("Lamp", on=False) or ctx.act("Phone", text="A car blocks the gate", picture=ctx.event.proof).
picture is a Frame or a .jpg or .png in your folder, for an action that can attach one. It returns a
dict with ok, status and reply, and raises ModelError if the action cannot run, so catch it. A rule
can run 12 actions a minute. Use an action only when the person asks for what it does. If the request
needs an action that is not listed, call finish with unsupported "No action can <what is needed>.
Add one under Actions."

SCHEDULES are triggers at a time, with no camera. Use one when the person asks for something at a time
or on a repeat, such as "every Monday at 9 AM, send me a report" or "every 10 minutes". Each has id,
every_minutes (at least 1), at ("HH:MM" in the person's own time, or "" to repeat every_minutes) and
days (such as ["mon"], or [] for every day). When it fires, on_event(schedule_id, t, ctx) runs, and
ctx.event has no frames. A rule can have only schedules and no sensors.

THE JOURNAL is one record of what every rule of this person saw and did, kept across restarts. It is
not only this rule's record. ctx.history(kind=None, name=None, rule=None, since=None, until=None,
limit=2000) reads it, oldest first. Each item is a dict with t, rule (a rule's name), kind, name,
seconds, text and values. Kinds: "event" (a sensor or schedule fired, and name is its id), "end" (a
sensor turned off, and seconds is how long its event lasted), "record", "action", "notify", "draw",
"image", "log" and "error". The request lists what the journal holds now.
ctx.record(name, **values) adds a "record" with facts that you measured, such as
ctx.record("waited", table="4", seconds=312). Record each number that a person may want to count,
compare or see in a report later, even if this request does not ask for a report.
A REPORT reads ctx.history, does the arithmetic in Python, draws a chart with any library, saves it as
a .png in your folder, shows it with ctx.image, and sends it with ctx.notify or ctx.act. Use the
person's own time for hours and days: datetime.fromtimestamp(t).

Cost and speed: a Spark ask takes 2 to 4 s and costs about $0.002. A Wonder ask takes 5 to 20 s and
costs about 10 times more, so use it for a second opinion. A find takes about 1 s. A call blocks
your code, and new events wait for it. A rule can make 30 model calls a minute, so call models
after an event or on a slow timer, never on each tick. A failed call raises ModelError from
rule_runtime. Catch it, and still report what you know.

Plan for the real world. The frame that fired the sensor is often not the best frame for the job.
A hand can hide the thing. A face can be turned away or out of view. A model can be unsure, and its
confidence is only its own estimate. So decide in code what a weak answer means: look at more
frames, look at later frames, compare frames with a library, or ask Wonder. If the answer stays
weak, say that it is uncertain. Never show a guess as a fact.

The sensor already proved the event. A later question must ask only for what is still missing, and
it must not require the event again in that frame. A person who was seen cooking can be photographed
when they stop cooking, so ask "Is a face clearly visible?", not "Is a person cooking with a clear
face?". The event and the picture of it can be in different frames.

A JOB CAN OUTLAST ITS TRIGGER. Rules have stages, and what the person wants may not exist yet when
the first sensor fires. Do not give up because the nearby frames lack it. A short look with
ctx.frames(after=...) is not a wait. Ask: can this still happen later? If it can, declare a second
sensor with starts_off true for that later moment, and arm it from on_event. Finish the job when it
fires, then disarm it. In on_tick, check a deadline that you keep in ctx.state. Use a deadline of
minutes, unless the person said otherwise. If time runs out, report what you could not do. Tell the
person in the event list what the rule now waits for. Jobs run at the same time while they wait for a
model, so an on_tick retry can start while on_event's own search for the same job still runs. Mark the
job busy in ctx.state for the length of a search, and let on_tick skip a busy job, even past its
deadline. A rule can have several stages. For example:

    def on_event(sensor, t, ctx):
        if sensor == "parcel_left":
            claim = "The sender's label is sharp and all its words can be read."
            best = ctx.check(claim, ctx.frames(before=1, after=2))[0]
            if best.passed and best.confidence >= 0.8:
                ctx.draw("Label found", frame=best.frame)
                return
            ctx.state["deadline"] = t + 300
            ctx.arm("label_readable", 300)
            ctx.log("Waiting for a clear view of the label")
        elif sensor == "label_readable" and "deadline" in ctx.state:
            best = ctx.check("The sender's label is sharp and all its words can be read.", ctx.event.frames)[0]
            if best.passed and best.confidence >= 0.8:
                del ctx.state["deadline"]
                ctx.disarm("label_readable")
                ctx.draw("Label found", frame=best.frame)
            else:
                # The sensor stays on, so restart it to look again.
                ctx.arm("label_readable", ctx.state["deadline"] - t)

    def on_tick(t, ctx):
        if t > ctx.state.get("deadline", t):
            deadline = ctx.state.pop("deadline")
            ctx.disarm("label_readable")
            ctx.notify("I never saw the label clearly", at=deadline - 300)

An armed sensor must test the exact thing that you wait for, not something near it. A state sensor
fires once when it turns on, and not again while it stays on. If it tests for a person and the face
is not clear at that moment, it stays silent while the person stands there. So test for the clear
face itself, with facts for Spark, or disarm and arm the sensor again to start a new check.

A library can measure sharpness, size or colour, but it cannot say what a frame shows. So do not let
a score alone choose a frame. To search, use ctx.check.

Test with rule_runtime.py, which is already in your folder. A test has no models, so script them:
    import rule
    from rule_runtime import Context, Found, Place, replay
    ctx = Context()
    ctx.answers = [("a red book", 0.4), ("a red book", 0.95)]   # what ask returns, in order
    ctx.found = [{"x1": 1, "y1": 1, "x2": 9, "y2": 9, "w": 64, "h": 36, "confidence": 0.9}]
    ctx.checks = [(False, 0.9), (True, 0.85)]   # what check says for each frame, in order
    ctx.place_list = [Place("table 1", "Hall", (0.2, 0.3, 0.4, 0.5))]   # what places returns
    ctx.detected = [Found(camera="Hall", label="person", box=(0.25, 0.3, 0.3, 0.45))]   # what detect returns
    ctx = replay(rule, [("on", "drinking", 100.0), ("off", "drinking", 103.0), ("tick", 104.0)], ctx)
    # ctx.actions is a list of dicts, such as {"do": "notify", "text": "..."}
    # ctx.asked is a list of (question, model). ctx.armed holds each armed sensor.
    # ctx.aim, ctx.move and ctx.light add to ctx.actions, such as {"do": "aim", "camera": "...", "thing": "car"}.
    # ctx.aim_finds = False makes ctx.aim return None.
    # ctx.act adds {"do": "act", "name": "Lamp", "inputs": {...}} and returns ok.
    # ctx.record adds {"do": "record", "name": ..., "values": {...}}.
    # ctx.past = [{"t": 100.0, "rule": "Gate", "kind": "end", "name": "car", "seconds": 40.0,
    #              "text": "", "values": {}}] is what ctx.history returns.
    # A schedule fires like a sensor: ("on", "weekly_report", 100.0).
    # Test frames are real small JPEGs, so library code runs on them.

Work in this order:
1. Write rule.py.
2. Write test_rule.py with assert statements. Test a case that must act, a case that must not act,
   and one edge case.
3. Run it with: python test_rule.py
4. Fix any failure.
5. Call finish.

Limits: only the camera works today, so a rule cannot hear. The folder has no network at run time,
so the listed actions are the only way out. If the request needs sound, call finish with a reason in
unsupported.
"""

TOOLS = [
    {
        "type": "function", "name": "write_file",
        "description": "Create or replace a file in your folder.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        },
    },
    {
        "type": "function", "name": "run_command",
        "description": "Run a shell command in your folder. It has no network. The limit is 60 s.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
    },
    {
        "type": "function", "name": "install_packages",
        "description": "Install Python packages with pip into your folder.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["packages"],
            "properties": {"packages": {"type": "array", "items": {"type": "string"}}},
        },
    },
    {
        "type": "function", "name": "finish",
        "description": "Submit the rule. The tests run again, and sensors are checked on any example.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "sensors", "schedules", "plan", "unsupported"],
            "properties": {
                "name": {"type": "string", "description": "2 to 5 words."},
                "schedules": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "every_minutes", "at", "days"],
                    "properties": {
                        "id": {"type": "string"}, "every_minutes": {"type": "number"},
                        "at": {"type": "string", "description": "HH:MM, or empty to repeat every_minutes."},
                        "days": {"type": "array", "items": {
                            "type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
                    },
                }},
                "sensors": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "target", "outline", "starts_off", "question", "requires",
                                 "min_count", "pattern",
                                 "shortest_event_s", "frames_per_second", "check_every_s",
                                 "frames_per_check", "clear_after_s", "min_motion", "reason", "watch"],
                    "properties": {
                        "id": {"type": "string"}, "target": {"type": "string"},
                        "outline": {"type": "string"}, "starts_off": {"type": "boolean"},
                        "question": {"type": "string"}, "min_count": {"type": "integer"},
                        "requires": {"type": "array", "items": {"type": "string"}},
                        "pattern": {"type": "string", "enum": ["state", "moment"]},
                        "shortest_event_s": {"type": "number"},
                        "frames_per_second": {"type": "number"},
                        "check_every_s": {"type": "number"},
                        "frames_per_check": {"type": "integer"},
                        "clear_after_s": {"type": "number"},
                        "min_motion": {"type": "number"},
                        "reason": {"type": "string"},
                        "watch": {
                            "type": "object", "additionalProperties": False,
                            "required": ["places", "wake", "change", "safety_s", "count"],
                            "properties": {
                                "places": {"type": "array", "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["camera", "find", "pointed"],
                                    "properties": {"camera": {"type": "string"}, "find": {"type": "string"},
                                                   "pointed": {"type": "boolean"}},
                                }},
                                "wake": {"type": "string", "enum": ["change", "count", "always"]},
                                "change": {"type": "number"},
                                "safety_s": {"type": "number"},
                                "count": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                }},
                "plan": {
                    "type": "array", "items": {"type": "string"},
                    "description": "3 to 6 very short steps a person can read, in order.",
                },
                "unsupported": {"type": "string", "description": "Empty unless the rule cannot work."},
            },
        },
    },
]


class WriterError(Exception):
    pass


def describe_example(client, frames):
    """Used when a person records an example and writes nothing."""
    answer, _ = client.ask_text(
        "A person recorded this to show an event that they want a camera to notice. "
        "Write one short sentence that starts with \"When\" and names the event. Name the visible "
        "action, and call the actor \"a person\". Do not describe who the person is, their clothes "
        "or the room. Do not name a colour or a brand. End with \"notify me\".",
        spread(frames, CHECK_FRAMES),
    )
    return answer


def study_example(client, frames, sentence):
    """Turns a recording into general signs of the event, for the rule writer."""
    answer, _ = client.ask_text(
        f"A person recorded this to show an event that a camera must notice from now on. Their "
        f"words: {sentence}\nFollow their words to find the main activity. Then separate what "
        "defines it from what only happened in this recording.\n"
        "ESSENTIAL: at most three short lines. What must be true every time this activity happens, "
        "for anyone. If one were missing, a person would say that the activity did not happen.\n"
        "INCIDENTAL: at most three short lines. What happened here but need not happen next time, "
        "such as a blink, a facial expression, a reaction, which hand was used, or the pose.\n"
        "Do not mention colours, brands, clothes, the room, or who the person is.",
        spread(frames, STUDY_FRAMES),
    )
    return answer


def review_facts(client, sentence, sensor):
    """An independent reviewer checks each fact against the person's words.

    Wonder's own recording tests reward a fact that a recording happens to show. A reviewer
    with no recording asks the other question: can the event happen without this fact?
    Returns the facts that are not essential, each with the reason.
    """
    facts = sensor.get("requires") or []
    if sensor.get("target") or not facts:
        return []
    listed = "\n".join(f"{i + 1}. {fact}" for i, fact in enumerate(facts))
    raw = client._post({
        "model": WRITER, "max_output_tokens": 2000, "reasoning": {"effort": "low"},
        "instructions": (
            "You review a camera rule. All the listed facts must be seen in frames from a camera "
            "before the rule fires, so one wrong fact makes the rule miss real events. Reject a fact "
            "only if it is a side effect or a coincidence: a reaction, a blink, a facial expression, "
            "what happens after the event, or a detail of one person's style, such as a pose before "
            "the event. Keep any fact that describes the event itself as a camera sees it. Keep "
            "visibility, contact and position. Think about normal ways that this event happens. Do "
            "not invent unusual cases, such as hidden hands, special cameras or slow motion."
        ),
        "input": f"The person's request: {sentence}\nThe facts:\n{listed}",
        "text": {"format": {"type": "json_schema", "name": "review", "strict": True, "schema": {
            "type": "object", "additionalProperties": False, "required": ["facts"],
            "properties": {"facts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["essential", "reason"],
                "properties": {"essential": {"type": "boolean"}, "reason": {"type": "string"}},
            }}},
        }}},
    }, timeout=120, retries=3)
    verdicts = json.loads(_output_text(json.loads(raw))).get("facts") or []
    return [f"{fact} ({v['reason']})" for fact, v in zip(facts, verdicts) if not v["essential"]]


def review_rule(client, sentence, sensors, code):
    """An independent reviewer reads the whole rule against the person's words.

    Wonder can write a rule that passes its own tests and still misses what the person asked
    for. Its tests use scripted answers, so they cannot show that the rule gives up too soon.
    Returns the faults found, as text for Wonder.
    """
    raw = client._post({
        "model": WRITER, "max_output_tokens": 3000, "reasoning": {"effort": "low"},
        "instructions": (
            "You review a camera rule before it goes live. A sensor is a trigger. After it fires, the "
            "code can look at frames and ask models. A sensor with starts_off true sleeps until the "
            "code calls ctx.arm. Report a fault only if it is one of these:\n"
            "1. GIVES UP TOO EARLY: what the person asked for may not be available at the trigger, and "
            "it can still happen later, but the code stops after a short look. A fixed window such as "
            "ctx.frames(after=...) is not a wait. The rule needs a sleeping sensor, armed from on_event, "
            "with a deadline of minutes.\n"
            "2. MIXED QUESTION: a later question makes a model prove the event again, together with what "
            "is missing. The sensor already proved the event. The event and the thing the person wants "
            "can be in different frames.\n"
            "3. MISSING PART: the person asked for something that the code never does.\n"
            "4. WAIT ON THE WRONG THING: an armed sensor tests something near the goal, and not the goal "
            "itself. A state sensor fires once when it turns on, so it may never fire again.\n"
            "5. GUESS AS FACT: the code shows a weak or failed answer as a fact.\n"
            "6. UNCHECKED CLAIM: the code calls a picture clear or correct, or shows it as proof, with no "
            "passed ctx.check on that exact frame. A ctx.ask over several frames confirms nothing about "
            "one frame, and a sensor firing does not confirm it either. A claim for a clear picture of a "
            "person must ask for the person in view, nothing cut off, and not badly blurred. It asks for eyes or a "
            "face only for a camera close to people, such as a desk or a door camera.\n"
            "10. NO PICTURES: the person asked for a picture of someone or something, but the code does "
            "not call ctx.gallery, or it shows nothing when the wait ends with no passed check.\n"
            "7. WRONG SUBJECT: the person asked about one person or thing, but a check can pass on another "
            "one in view. The claim must say which one, with reference if needed.\n"
            "8. NARROWED MEANING: a sensor leaves out a normal form of what the person named, so real "
            "events never fire. A smoker can use a cigarette, a cigar or a vape, and a drink can be in a "
            "bottle, a cup or a glass. Follow the person's word as widely as people normally use it.\n"
            "9. LOST JOB: the person said anyone or everyone, and each event starts a wait, but the code "
            "keeps one slot for it. A second person's event then replaces the first person's wait, and "
            "the first job never finishes. The code must keep one job for each event.\n"
            "Think only about normal ways that this event happens. Do not report style, speed or cost. "
            "A simple request can need a simple rule, so a rule with no fault gets an empty list."
        ),
        "input": (f"The person's request: {sentence}\nThe sensors:\n{json.dumps(sensors, indent=1)}\n"
                  f"rule.py:\n{code}"),
        "text": {"format": {"type": "json_schema", "name": "review", "strict": True, "schema": {
            "type": "object", "additionalProperties": False, "required": ["faults"],
            "properties": {"faults": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["fault", "fix"],
                "properties": {"fault": {"type": "string"}, "fix": {"type": "string"}},
            }}},
        }}},
    }, timeout=120, retries=3)
    faults = json.loads(_output_text(json.loads(raw))).get("faults") or []
    return [f"{item['fault']} Fix: {item['fix']}" for item in faults]


def measure_example(client, sentence, frames, motions, gap_s):
    """Measures how long the action lasts in a recording. Returns text for Wonder."""
    lines = []
    if motions and gap_s:
        busy = [m >= MOTION_BUSY for m in motions]
        bursts, run = [], 0
        for moving in busy + [False]:
            if moving:
                run += 1
            elif run:
                bursts.append(round(run * gap_s, 2))
                run = 0
        lines.append(f"Plain code on {len(frames)} frames, {gap_s:.2f} s apart: {len(bursts)} bursts of "
                     f"movement, lasting {bursts or 'none'} seconds. Peak movement value {max(motions):.0f}.")
    if gap_s:
        picked = spread(list(range(len(frames))), SPAN_FRAMES)
        answer, _ = client.ask_text(
            f"These are {len(picked)} frames in time order, numbered from 1. A person recorded them to "
            f"show this event: {sentence}\nFind where the event itself happens, without the time "
            "before and after it. Reply with only two numbers: the first frame and the last frame "
            "that show it.",
            [frames[i] for i in picked],
        )
        found = re.findall(r"\d+", answer)
        if len(found) >= 2 and 1 <= int(found[0]) <= int(found[1]) <= len(picked):
            first, last = picked[int(found[0]) - 1], picked[int(found[1]) - 1]
            lines.append(f"Spark says that the event lasts about {(last - first + 1) * gap_s:.2f} s.")
    return "\n".join(lines)


def sensor_sees(client, sensor, frames, motions=None):
    """Returns (seen, facts, note), so a failure can name the fact that failed."""
    pattern = sensor.get("pattern", "state")
    if motions and len(motions) == len(frames):
        items = [Sample(jpeg, motion) for jpeg, motion in zip(frames, motions)]
    else:
        items = [Sample(jpeg, 0.0) for jpeg in frames]
    count = int(sensor.get("frames_per_check") or CHECK_FRAMES)
    chosen = [item.jpeg for item in pick_frames(items, min(max(count, 1), 8), pattern)]
    if sensor["target"]:
        return any(client.see(sensor["target"], frame) for frame in chosen), [], ""
    facts = sensor.get("requires") or [sensor["question"]]
    answer = client.check_facts(sensor["question"], facts, chosen, pattern == "moment")[0]
    return answer["seen"], answer.get("facts") or [], answer.get("note", "")


def describe_misses(sensor, misses):
    """Tells Wonder which facts failed on the recording, and what Spark saw there."""
    facts = sensor.get("requires") or [sensor["question"]]
    failed = {fact for answer, _ in misses for fact, ok in zip(facts, answer) if not ok}
    text = f"Sensor {sensor['id']} missed the event in the person's recorded example."
    if failed:
        text += " These facts failed on the real recording: " + "; ".join(sorted(failed)) + "."
    notes = [note for _, note in misses if note]
    if notes:
        text += f" Spark saw: {notes[0]}"
    return text + " The recording shows the real event, so change the facts to match what it shows."


def check_sensors(client, sensors, example, idle, motions=None):
    """Returns the problems found, as text for Wonder."""
    problems = []
    for sensor in sensors:
        # A recording shows the first event. A later stage need not appear in it.
        if sensor.get("starts_off"):
            continue
        if example:
            tries = [sensor_sees(client, sensor, example, motions) for _ in range(3)]
            if sum(seen for seen, _, _ in tries) < 2:
                misses = [(facts, note) for seen, facts, note in tries if not seen]
                problems.append(describe_misses(sensor, misses))
        if idle and sum(sensor_sees(client, sensor, idle)[0] for _ in range(2)):
            problems.append(f"Sensor {sensor['id']} said yes on frames where the event does not happen.")
    return problems


def clean_path(path):
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or ".." in parts:
        raise WriterError("Use a path inside your folder.")
    return "/".join(parts)


def handle(call, folder, client, example, idle, progress, motions=None, sentence="", review=None):
    args = json.loads(call["arguments"] or "{}")
    review = review if review is not None else {"rounds": 0}
    name = call["name"]
    if name == "write_file":
        path = clean_path(args["path"])
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"])
        progress(f"Wrote {path}")
        return f"Wrote {path}.", None
    if name == "run_command":
        code, output = sandbox.run(folder, args["command"])
        progress(f"Ran {args['command'][:60]}: {'passed' if code == 0 else 'failed'}")
        return f"Exit code {code}.\n{output}", None
    if name == "install_packages":
        progress("Installing " + ", ".join(args["packages"])[:80])
        code, output = sandbox.install(folder, args["packages"])
        return f"Exit code {code}.\n{output}", None
    if name == "finish":
        if args["unsupported"]:
            return "", {"unsupported": args["unsupported"]}
        if not args["sensors"] and not args.get("schedules"):
            return "A rule needs at least one sensor or schedule.", None
        ids = [s["id"] for s in args["sensors"]] + [s["id"] for s in args.get("schedules") or []]
        if len(set(ids)) < len(ids):
            return "Each sensor and schedule needs its own id.", None
        for item in args.get("schedules") or []:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", item["at"]) and item["at"]:
                return f"Schedule {item['id']}: at must be HH:MM, such as 09:00, or empty.", None
            if not item["at"] and item["every_minutes"] < 1:
                return f"Schedule {item['id']}: every_minutes must be at least 1.", None
        code, output = sandbox.run(folder, "python test_rule.py")
        if code:
            progress("Final test run failed")
            return f"test_rule.py failed, so the rule is not live.\n{output}", None
        progress("Tests passed")
        rejected = []
        if review["rounds"] < MAX_REVIEW_ROUNDS:
            rejected = [item for sensor in args["sensors"] for item in review_facts(client, sentence, sensor)]
        if rejected:
            review["rounds"] += 1
            progress("Reviewer rejected: " + rejected[0])
            return ("The rule is not live. A reviewer found facts that are not essential, so the rule "
                    "would miss real events:\n" + "\n".join(rejected) +
                    "\nRemove them or rewrite them, change rule.py if it needs it, and call finish."), None
        if review["rounds"] < MAX_REVIEW_ROUNDS:
            progress("A reviewer is checking the rule against your words")
            faults = review_rule(client, sentence, args["sensors"], (folder / "rule.py").read_text())
            if faults:
                review["rounds"] += 1
                progress("Reviewer found: " + faults[0])
                return ("The rule is not live. A reviewer compared it with the person's words and found "
                        "these faults:\n" + "\n".join(faults) +
                        "\nChange rule.py, test_rule.py and the sensors as needed, and call finish."), None
        if example:
            progress("Checking the sensors on your recording")
        problems = check_sensors(client, args["sensors"], example, idle, motions)
        if problems:
            progress(problems[0])
            return "The rule is not live.\n" + "\n".join(problems) + "\nRewrite the sensor or its timing, and call finish.", None
        return "", args
    return f"Unknown tool {name}.", None


def describe_pointed(pointed):
    """Tells Wonder about the object that a person clicked, every camera that shows it, and which of
    those cameras move. A place in a moving camera's picture is true only for one moment."""
    def on_grid(box):
        return [box["x1"] * 1000 // box["w"], box["y1"] * 1000 // box["h"],
                box["x2"] * 1000 // box["w"], box["y2"] * 1000 // box["h"]]

    def where(camera, kind, box):
        if kind == "moving":
            return (f"{camera} moves, so the {noun} can sit anywhere in its picture, or be out of view. "
                    f"Find it there by its name and its look, not by its place.")
        if box:
            return f"{camera} stays still, so the {noun} stays in its place there: box {on_grid(box)} on that camera's 0-1000 grid."
        return f"{camera} shows it."

    noun = pointed["noun"]
    named = (" The person gave it this name, so trust the name over what a picture seems to show."
             if pointed.get("named_by") == "person" else "")
    text = f"\nThe person clicked one object: {noun}.{named}"
    if pointed.get("note"):
        text += f" The person says about it: {pointed['note']}"
    camera = pointed.get("camera")
    also = pointed.get("also") or []
    # Rules written before cameras had kinds name the moving ones in a list.
    kind_of = {name: "moving" for name in pointed.get("moving") or []}
    if camera:
        text += f"\nThey clicked it in {camera}. " + where(camera, pointed.get("kind") or kind_of.get(camera, "fixed"),
                                                           pointed.get("view_box"))
        for item in also:
            text += f"\nThe same {noun} is also in view of {item['camera']}. " + where(
                item["camera"], item.get("kind") or kind_of.get(item["camera"], "fixed"), item.get("view_box"))
        if also:
            views = " or ".join([camera, *[a["camera"] for a in also]])
            text += (f"\nIt is one object, seen from different sides. Watch it in every one of these views, so the "
                     f"rule still works when one view is blocked or turned away. A question can name them, such as "
                     f"\"In {views}, ...\".")
        else:
            text += f"\nNo other camera showed the same {noun} when the person clicked."
        kinds = [pointed.get("kind") or kind_of.get(camera), *[a.get("kind") or kind_of.get(a["camera"]) for a in also]]
        if "moving" in kinds:
            text += ("\nA moving view that does not show it says nothing about it: count that view as cannot see, not "
                     "as no. Judge whether the thing itself moves in a view that stays still. In a moving view, it "
                     "moved only if its place changed against things that never move, such as posts or walls.")
            if "fixed" not in kinds:
                text += " No view that stays still shows it, so say so in the rule's plan."
    else:
        text += f"\nIts box on the whole view's 0-1000 grid is {on_grid(pointed['box'])}."
    if pointed.get("reference"):
        text += (f"\nA close-up of it from the click is {pointed['reference']} in your folder. ctx.pointed holds all "
                 f"of this. When other {noun}s may be in view, pass that picture as reference to ctx.check, and "
                 "name this one in the claim.")
    return text + (
        "\nWords such as this, that or it in the request mean this object. Use its name in your sensors, "
        "and in the outline field. Do not describe it only by where it sits in the frame."
    )


def describe_previous(previous):
    """Tells Wonder that it is changing a live rule, and not starting a new one."""
    sensors = json.dumps(previous.get("sensors") or [], indent=1)
    return (
        f"\nThis edits a live rule. The old request was: {previous.get('sentence', '')}\n"
        "The request above replaces it in full. The person changed it because they want the rule to "
        f"behave differently, so make sure that it does.\nThe old sensors were:\n{sensors}\n"
        "rule.py and test_rule.py in your folder are the old code, so read them first. Keep what still "
        "works, and change whatever the new request needs. ctx.state keeps its old values, so reuse a "
        "key only if it still means the same thing."
    )


def write_rule(client, folder, sentence, example=None, idle=None, progress=lambda text: None,
               motions=None, gap_s=None, pointed=None, previous=None, cameras="", actions="", journal=""):
    """Runs Wonder until the rule passes. Returns (rule fields, cost in USD)."""
    text = f"The person's request: {sentence}"
    if cameras:
        text += f"\nThe cameras on the wall:\n{cameras}"
    text += f"\nThe person's actions:\n{actions}" if actions else "\nThe person has no actions yet."
    text += f"\nThe journal holds:\n{journal}" if journal else "\nThe journal is empty so far."
    if pointed:
        text += describe_pointed(pointed)
    if previous:
        text += describe_previous(previous)
    if example:
        progress("Spark is studying your recording")
        signs = study_example(client, example, sentence)
        progress(f"Spark saw: {signs}")
        text += (
            "\nThe person also recorded an example. Spark describes it:\n"
            f"{signs}\nNever use anything from INCIDENTAL. Your sensors will be checked on that "
            "recording. Treat it as one example of the event, not as the only form of it."
        )
        measured = measure_example(client, sentence, example, motions, gap_s)
        if measured:
            progress(measured[:200])
            text += f"\nMeasured from the recording:\n{measured}"
    # The full history goes with each call. Stored responses expired during a long pip install.
    history = [{"type": "message", "role": "user", "content": text}]
    cost = 0.0
    review = {"rounds": 0}
    for _ in range(MAX_TURNS):
        data = json.loads(client._post({
            "model": WRITER, "instructions": PROMPT, "input": history, "tools": TOOLS,
            "reasoning": {"effort": "medium"}, "max_output_tokens": 16000,
            "store": False, "include": ["reasoning.encrypted_content"],
        }, timeout=240, retries=3))
        cost += wonder_cost(data.get("usage") or {})
        history.extend(data.get("output", []))
        calls = [item for item in data.get("output", []) if item.get("type") == "function_call"]
        if not calls:
            history.append({"type": "message", "role": "user",
                            "content": "Continue. Call a tool. Call finish when the tests pass."})
            continue
        for call in calls:
            try:
                output, done = handle(call, folder, client, example, idle, progress, motions, sentence, review)
            except (WriterError, ApiError, KeyError, ValueError) as e:
                output, done = f"Error: {e}", None
            if done:
                return done, cost
            history.append({"type": "function_call_output", "call_id": call["call_id"], "output": output})
    raise WriterError("Wonder did not finish the rule.")


ACTION_PROMPT = """\
You connect a service or a device to a camera rule system. The person describes one action in plain
words, such as "turn my desk lamp off or on" or "add a row to my sheet". They may also paste docs or an
example request. Write ONE web request that does the action.

- Each value that changes from call to call is an input. Write it as {input_name} in the url, a header
  or the body. Give each input a short name in small letters, a description and an example value.
- Each key, token, password, private id or webhook secret is a secret. Write it as {secret:NAME}, with
  NAME in capital letters. Never write a real key, even if the person pasted one. The person types
  each secret into a private file, so describe where they can find it.
- A json body must be valid JSON. Put each blank inside quotes, such as {"on": "{on}"}. A blank that
  is the whole string keeps the input's own type, so on can be true or false.
- The host must be fixed. Never put a blank in the host.
- Use https, unless the device is on the person's home network.
- name is 1 to 3 words that a person would say, such as "Desk lamp". description says what it does in
  one sentence.
- If you do not know the address of the service and the person did not give it, or the service needs a
  login that a fixed key cannot give, explain this in unsupported and leave the rest empty.
"""

ACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "description", "method", "url", "headers", "body_type", "body", "inputs", "secrets",
                 "unsupported"],
    "properties": {
        "name": {"type": "string"}, "description": {"type": "string"},
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "url": {"type": "string"},
        "headers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "value"],
            "properties": {"name": {"type": "string"}, "value": {"type": "string"}}}},
        "body_type": {"type": "string", "enum": ["none", "json", "text"]},
        "body": {"type": "string"},
        "inputs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description", "example"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                           "example": {"type": "string"}}}},
        "secrets": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}},
        "unsupported": {"type": "string"},
    },
}


def write_action(client, words, taken=()):
    """Wonder turns the person's words into one web request with blanks. Returns (draft, cost in USD).

    The draft is not trusted. The server checks it, and the person tests it before it is saved.
    """
    data = json.loads(client._post({
        "model": WRITER, "instructions": ACTION_PROMPT, "max_output_tokens": 4000,
        "reasoning": {"effort": "low"},
        "input": f"The person's words:\n{words}\nNames already in use: {', '.join(taken) or 'none'}",
        "text": {"format": {"type": "json_schema", "name": "action", "strict": True, "schema": ACTION_SCHEMA}},
    }, timeout=120, retries=3))
    draft = json.loads(_output_text(data))
    draft["headers"] = {item["name"]: item["value"] for item in draft.get("headers") or []}
    return draft, wonder_cost(data.get("usage") or {})


UNDERSTAND_PROMPT = """\
A person wrote a rule for a camera system in plain words. Say back, in plain short words, how the
system will understand it, before an AI spends money writing the code.

- when: the trigger, starting with "When" or "Every", in at most 14 words.
- then: every thing the person asked the rule to do, one short phrase each, such as "Alert your phone
  with a picture" or "Keep a record for reports". Leave nothing out. These are built in and need no
  action: showing a result on the page, speaking on the computer, keeping a record, and turning a listed
  camera's light on or off or turning its moving lens, when the camera list says that camera can.
  Anything else outside the page needs one of the listed actions.
- watch: the camera names it will watch, from the list. Use all of them when the words name none. Use
  [] for a rule that only runs on a schedule.
- missing: if the words ask for something outside the page that no listed action can do, one sentence
  that names it, such as "No action can turn off the lamp." Otherwise "".
- question: if the words are too vague to build, one short question that would fix it. Otherwise "".
"""

UNDERSTAND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["when", "then", "watch", "missing", "question"],
    "properties": {
        "when": {"type": "string"}, "then": {"type": "array", "items": {"type": "string"}},
        "watch": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "string"}, "question": {"type": "string"},
    },
}


def understand(client, sentence, actions="", cameras=""):
    """Spark restates a rule as When and Then for the person to check. Returns (summary, cost in USD)."""
    data = json.loads(client._post({
        "model": SPARK, "instructions": UNDERSTAND_PROMPT, "max_output_tokens": 3000,
        "reasoning": {"effort": "minimal"},
        "input": (f"The rule: {sentence}\nCameras:\n{cameras or '- This camera'}\n"
                  f"Actions:\n{actions or '- none yet'}"),
        "text": {"format": {"type": "json_schema", "name": "understand", "strict": True, "schema": UNDERSTAND_SCHEMA}},
    }, timeout=60, retries=2))
    text = _output_text(data)
    if not text:
        # Spark can spend its whole budget thinking, even at minimal effort.
        raise WriterError("Spark did not finish reading the words. Try once more.")
    return json.loads(text), spark_cost(data.get("usage") or {})
