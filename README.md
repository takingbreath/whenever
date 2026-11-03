# Whenever

Point at it, say it, or show it. Your camera runs it.

Whenever is IFTTT for the physical world. A person gives a rule in plain English, and a camera becomes the trigger. When the rule fires, the result appears on the object itself.

This repo holds the prototype. It runs on a Mac. Rules can see, but they cannot hear yet.

## Hackathon

Akhilesh Khajuria ([takingbreath](https://github.com/takingbreath)) built Whenever for the Meta Global AI Developer Hackathon, startup track. All the code in this repo is new work, written from 30 October 2026.

| Meta model | What it does here |
|---|---|
| Muse Wonder 1.0 | Writes each rule's sensors and code, and reviews them against the person's words |
| Muse Spark 1.3 | Checks actions in frames, joins clues across time, and names a thing that a person points at |
| SAM 3.1 | Finds, counts and outlines things, picks the zoomed pictures, and finds the thing that a lens turns to |
| Muse Voice Transcribe 1.0 | Turns speech into text while the talk key is held |
| Muse Image 1.0 | Made the test pictures in `testdata` |

Code from others is not in this repo. You install it:

- `meta_sam_parser` from Meta, under the SAM License, plus `websockets` and `Pillow` (see `requirements.txt`)
- `pyezvizapi`, under Apache-2.0, for EZVIZ cameras only
- `ffmpeg`, for security cameras only

## Run

1. Put your Meta API key alone in a file named `.env` in this folder.
2. Install the libraries into this folder:

   ```bash
   python3 -m pip install --target vendor -r requirements.txt
   ```

3. Start the server:

   ```bash
   python3 server.py
   ```

4. Open <http://127.0.0.1:8787> in Chrome. Allow the camera and the microphone.
5. Give a rule. Write or speak a sentence, record an example, or do both. Click any object in the view to point at it, then say "this" in the sentence.

To run without a camera, open <http://127.0.0.1:8787/?clip=1>. The test clip uses AI-made frames.

## How it works

1. **Trigger:** a person writes or speaks a sentence, records an example, or does both. Muse Voice Transcribe turns speech into text while the talk key is held. If there is only a recording, Muse Spark writes the sentence.
   A click on the view marks one object. SAM 3.1 finds that object from the click, and Spark names it. Wonder then knows what "this" means.
2. **Rule writer:** Muse Wonder writes the sensors and the code in the rule's own folder. It knows how the camera pipeline works, and it chooses the timing for each sensor: the type, the capture rate, how often to check, how many frames a check gets, and how much movement counts. A reviewer rejects facts that are side effects, such as a blink. It can install any Python library. Its tests must pass. Its sensors must also pass any recorded example, and a recording gives it the measured length of the action.
3. **Capture:** frames reach the server at the rate that the fastest sensor needs, up to 12 a second. They never wait for a model.
4. **Sensors:** each sensor only says what is true now. SAM 3.1 checks solid things and hand gestures. Spark checks actions and things with no clear shape, using required facts. Each check covers all the frames since the last check, so no time is skipped. A new event needs two yes answers on the same frames.

   A real action can have no frame that proves it. A hand can hide a cigarette at the lips. So when a check fails but Spark sees part of the action, the harness shows Spark the last 24 seconds and asks it to join the clues across time. This works for any rule and needs no code in the rule. Each no doubles the wait, so a false near miss costs little, and the wait stops at 24 seconds, so no frame goes unread. A sensor that waits for something one frame must prove, such as a clear face, gets no review.

   While a state is on, a review also runs every 8 seconds and lists each person who does the action. A person with different clothes gets a new event, even if they start straight after the first person.

   Detection and analysis are separate:

   - **Checks** run on their own threads and their own model connections. Up to 3 run at once for a sensor, so a slow answer does not delay the next check.
   - **Reviews, outlines and rule calls** run on other threads and other connections. Nothing they do can hold up a check.
   - **Evidence** stays on disk with a time for each frame, so a job that runs late still gets the frames of its own event.
   - **Rule code** runs each event as its own job. A job gives way while it waits for a model, so a second person's job starts at once. Code between two model calls runs alone, so a rule needs no locks.
5. **Event cleaner:** `engine.py` removes flicker. A "state" lasts, and it ends after a quiet time. A "moment" is one occurrence for each burst of movement. Plain code measures speed. Models never judge speed or force.
6. **Rule code:** a sensor is only the trigger. After it fires, the rule's code is a program over the video. Wonder writes this program from the building blocks below, so the harness holds no special case for any one request. The code can look again, ask models, judge their answers, and do any calculation.
7. **Action:** the code can draw, show a picture, notify, speak, show a live value or add to the event list. A draw freezes the view on the frame that proved the event. SAM outlines the object on that frame, and the rule's text appears beside it. The frame is frozen because a model answer takes seconds, and a live outline would lag behind a moving object.
8. **Proof:** "Why?" shows that frame, the object's outline, and each fact that passed or failed. A moment also shows the measured movement against the rule's threshold.

## Use security cameras

`camera.py` sends cameras to the server in place of the browser page. It needs `ffmpeg`. Put one camera on each line of `camera.env`, with an address, a name, and then what it can do. Git ignores that file, because an address can hold a camera's code.

```text
ezviz:K12345678 Driveway
ezviz-rtp:BD1234567:1 Gate wide
ezviz-rtp:BD1234567:2 Gate tracker | moving light
rtsp://user:code@192.168.1.20:554/Streaming/Channels/101 Shop
```

Then start the server and run:

```bash
python3 camera.py
```

- `ezviz:SERIAL` uses your saved EZVIZ login and EZVIZ's cloud, for a camera with no RTSP. Install `pyezvizapi` in `.ezviz` with `python3 -m venv .ezviz && .ezviz/bin/pip install pyezvizapi`, and run `ezviz_code.py` in a terminal once to log in.
- `ezviz-rtp:SERIAL:CHANNEL` is the same, for a camera that sends RTP, such as the H9c.
- A Hikvision camera or recorder uses an RTSP address like the one above.

The cameras go on one wall, so one model call watches all of them, and every rule covers every camera. While the reader runs, the page shows the wall in place of the Mac's camera, and a page that opens or reloads only watches. Each camera's full frame is also saved while it has movement, so zoom has real pixels. Frames saved with no rule cost nothing. The oldest pictures go first when a run reaches 20 GB.

## Turn a lens and use a light

The H9c has 2 lenses. Gate wide never moves and sees everything. Gate tracker moves and sees half that width, so it has 2 times the pixels on a thing. An aim point is a point in the Gate wide picture. We measured 5 points, and each one landed within 0.04 of that point, so this needs no calibration.

- **The fixed lens finds, and the moving lens goes to look closer.** `ctx.aim("Gate tracker", "white car")` makes SAM find the car in Gate wide and turns the tracker to it.
- **A moving lens wakes no checks.** Its picture changes when it turns, so only fixed cameras count as movement. Its full frames are saved when the fixed lens beside it sees something, and a gallery prefers them.
- **A light always has an end.** The light turns off by itself after at most 60 s, even if the server stops. Stop or Delete on a rule also turns off that rule's light.
- **A rule's words never reach a command.** `ezviz_control.py` has a fixed list of actions, `camera_control.py` checks each argument, and no shell runs. A rule can use a camera 12 times a minute.

The page has a row for this: arrows, **Click to aim** and the light. After **Click to aim**, click a place in the Gate wide picture.

The lens also follows people by itself, so it can leave the point that a rule aimed at. Frames from a new view start about 6 s later, because the lens takes about 2 s to arrive and the video comes about 4 s late. So a rule aims at a thing that stays, or lets the camera follow a person.

## Actions: reach anything outside the camera

A rule's code has no network. It names an action, and the server sends the request.

- **Phone:** `ctx.notify(text, picture)` also goes to your phone through [ntfy](https://ntfy.sh). Put `NTFY_TOPIC=<a long random name>` in `actions.env`, and follow that topic in the ntfy app. `ctx.act("Phone", text=..., picture=frame)` sends one on purpose.
- **Any web request:** under **Actions** on the page, describe the action in plain words, such as "Turn my desk lamp on or off. It is Hue light 3." You can paste docs or an example request. Wonder writes one request with blanks: `{on}` for an input, and `{secret:HUE_USER}` for a key. You check the request, press **Test it**, and save it. Then any new rule can call `ctx.act("Desk lamp", on=False)`.

The same safety rules hold:

- A key stays in `actions.env`, which Git ignores. It never reaches rule code, Wonder or the page, and it is hidden in any reply.
- An action reaches only the host that you saved. It follows no redirect, and its host has no blanks.
- A rule can run 12 actions a minute.
- With `WHENEVER_PUBLIC=1`, as on a hosted server, an action reaches only public `https` hosts.

## Journal and reports

The server writes everything into one journal, `data/journal.db`: each event and how long it lasted, each action, each notification, and each fact that a rule records with `ctx.record("waited", table="4", seconds=312)`. The journal belongs to you, not to one rule, and it stays across restarts.

A report is a rule with a **schedule**, such as "Every Monday at 9 AM, send my phone the busy hours of last week." A schedule fires with no camera. The rule reads `ctx.history(...)`, does the arithmetic, draws a chart and sends it. So no rule needs its own report code, and one report can use what all your rules saw. Schedules use your own clock. A hosted server sets `WHENEVER_TZ`, such as `Asia/Kolkata`.

Live rules return after a restart, with their saved values. The list is in `data/rules.json`.

## Pictures

`ctx.gallery("face")` finds a thing across frames and shows a row of zoomed pictures, the best first. A click opens the full frame from that camera, with the thing marked. Zoom uses real pixels only. It does not use AI to add detail, because that can invent a face or a number plate. Rule code calls a picture clear only after `ctx.check` passes on that frame.

## Stop and resume a rule

**Stop** keeps the rule, but it reads no frames and calls no model, so it costs nothing. A job that was running ends, and it cannot act afterwards. **Resume** starts from that moment. It does not read the frames from the stopped time, and a sleeping sensor stays asleep until the rule needs it. An edit of a stopped rule stays stopped.

## Edit a rule

Click **Edit** on a rule, change its words, and click **Update rule**. Wonder gets the new words, the old words and the old code. The old rule keeps running until the new rule passes. If the edit fails, the old rule stays. The new rule keeps its saved values, its recording and the object that you pointed at.

## Building blocks

| Block | What it does |
|---|---|
| `ctx.event` | What just fired: its frames, its proof frame, and what the sensor saw |
| `ctx.frames(before, after, count)` | Frames around the event. `after` waits for later frames. Each frame is a JPEG in the rule's folder, so any Python library can open it |
| `ctx.ask(question, frames, model)` | Spark or Wonder looks at frames and answers in words, with a confidence and the best frame |
| `ctx.check(claim, frames, model, reference)` | A model judges each frame alone against a claim, and says what it really sees. Use it to search frames and to confirm a frame before a rule calls it clear or correct |
| `ctx.find(thing, frame)` | SAM says where a thing is and how many exist, with a confidence |
| `ctx.arm(sensor, seconds)`, `ctx.disarm(sensor)` | Wakes or stops a sensor at run time, so a rule can have stages and wait for a later moment |
| `ctx.draw(text, frame, target)` | Freezes the view on any frame and outlines any object |
| `ctx.image(picture, caption)` | Shows a frame, a crop or a chart in the event list |
| `ctx.gallery(thing, frames, count, caption)` | Shows a row of zoomed pictures of a thing, the best first. A click opens the full frame |
| `ctx.aim(camera, thing, x, y, frame)` | Turns a moving lens to a thing that SAM finds in the fixed lens, or to a point there |
| `ctx.move(camera, direction, seconds)` | Turns a moving lens left, right, up or down |
| `ctx.light(camera, on, seconds)` | Turns a camera's light on or off. It turns off by itself after 60 s at most |
| `ctx.act(name, picture, **inputs)` | Runs one of your actions, such as the phone or a lamp |
| `ctx.record(name, **values)` | Adds a measured fact to the journal |
| `ctx.history(kind, name, rule, since, until)` | Reads the journal of all your rules |
| `ctx.notify(text, picture)` | Shows an alert, and sends it to your phone |
| `ctx.say`, `ctx.show`, `ctx.log` | Other outputs |
| Any Python library | Pixel work, statistics, charts and files |

For example, Wonder can write a rule that asks Spark what an object is, checks the confidence, and asks Wonder for a second opinion if the answer is weak. Another rule can see that a face is out of view, wake a second sensor, wait for the face to return, and save that frame. The confidence is the model's own estimate, not a measured accuracy.

Before a rule goes live, a second reviewer reads its code against the person's words. It rejects a rule that gives up while the thing can still happen later, makes a later question prove the event again, skips part of the request, waits on the wrong thing, shows a guess as a fact, claims a picture with no check on that frame, can pass on the wrong person or thing, narrows the person's word, or lets a second person's event replace the first person's wait.

The page also sends a 1280-pixel copy of each frame. Rule code and its pictures use that copy, and sensors keep the 640-pixel frame, because each sensor check is paid for.

The rule still has no key and no network. The server makes each model call and sends the answer back. Each call appears in the event list and in "Why?".

## Why frames and not video files

Both models accept video. We measured them on 2-second clips at 10 frames per second. All of the test frames are AI-made.

| Path | Time for one check | Steam for 1 s | Steam for 0.3 s |
|---|---|---|---|
| Spark, mp4 | About 3 s | 6 of 6 yes | 0 of 12 yes |
| Spark, 6 ordered frames | 1.5 to 2.8 s | 6 of 6 yes | 12 of 12 yes |
| SAM, mp4 | 2.3 to 3 s | 10 of 10 frames | 3 of 3 frames |

- Spark's token count stayed the same for every 2-second clip, so it probably samples only a few frames. Its docs do not say.
- SAM video checks every frame, but each request costs at least 50 frames ($0.01), and a clip must first be recorded. We keep it for checks on past video.

## Limits

- The server listens on this Mac only.
- The API key stays in the server. Rule code cannot read it.
- Each rule runs in its own sandbox. It can write only in its folder and cannot read your home folder.
- Rule code has no network, except while pip installs libraries. A rule reaches the world only through the cameras in `camera.env` and the actions that you saved.
- A rule can make 60 model calls a minute. Every frame in the session stays available to it.
- Frames go to Meta's API in Standard mode. Audio goes to Meta's API only while the talk key is held.
- Development mode saves every frame and model answer in `logs/`, on this Mac. The oldest pictures go first when a run reaches 20 GB. This folder is not in the repo.
- The server stops calling the API at an estimated spend of $5 a day. This estimate does not include voice.
- A draw works on the frames that a rule already saw. It does not follow an object live.
- A schedule that comes while the server is off is skipped. The next one runs.
- Up to 5 rules run at once.

## Host it on a Linux server

The `deploy` folder holds a systemd unit, a Caddy site and a deploy script. The server runs Whenever as its own user, `whenever`, in `/srv/whenever`, behind Caddy, which adds HTTPS. The browser camera works only over HTTPS.

- **Login:** a hosted page asks for the access code in `access.env`. The code is a file, not an environment value, so rule code cannot read it from the server's process.
- **Spend:** the daily limit is `WHENEVER_SPEND_LIMIT`, $5 by default. It starts again at midnight in `WHENEVER_TZ`.
- **Rule code:** on Linux, `jail.py` locks each rule's process with Landlock and seccomp. It can write only in its folder, it can read only the system folders, and it has no internet socket. Neither needs root or a change to the host.
- **Service:** the systemd unit hides every home folder from the service and lets it write only in `/srv/whenever`.
- **Cameras:** your home cameras stay at home. Visitors use their own browser camera, or the test clip at `/?clip=1`.

To send a new version, run `deploy/deploy.sh <ssh host>` from this folder. It copies only the files that Git would publish.

## Test

To test a rule on a past run, with no camera:

```bash
python3 experiments/overlay/replay_run.py logs/<run> <first frame> <last frame> "<sentence>"
```

The unit tests need no API key:

```bash
python3 -m unittest discover tests
```
