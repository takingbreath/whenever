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

## The app

One rule is called a **Whenever**. The page works like IFTTT, with one sentence per rule:

- **Start:** choose this device's camera, the demo clip, or security cameras.
- **Create:** fill two slots, **When** (type it or hold the mic) and **then** (tap the actions). Point at a thing or show an example if the words need help. Templates cover homes, shops and cafés, reports and gestures.
- **Check:** before Wonder spends money, Spark says back how it read the words, for about $0.002. It warns when an action is missing.
- **My Whenevers:** a colour card for each Whenever, with an on/off switch. A red tab slides out under a card that fired.
- **Whenever page:** live values, what it does, how it watches, its activity with proof frames, the code, edit and delete. **Watch it live** opens Live focused on it: its camera is lit and the rest of the wall dims.
- **Activity:** one feed from the journal, with proof pictures, "Why?" and Right or Wrong. **Reports** show events by hour, by Whenever and by action. **Problems** shows what went wrong, grouped, with the technical detail and how fast each model answers.
- **Actions:** choose where alerts go (browser alerts, Telegram, email or the ntfy app), test and remove each place, and connect a new action in plain words. The first time you choose **Alert my phone**, the same pairing sheet opens.
- **Live:** the camera wall, the lens and light controls, and the Whenevers that watch.
- **Cameras:** this laptop's or phone's own camera, a spare phone that becomes the camera from a QR code (no app; it logs in, keeps its screen on and sends its picture), or the demo clip. Makes that need something Whenever cannot get today, such as a maker's developer key, Google's review or Ring's partner approval, show as Coming soon with the reason. Developers can still connect any camera with `camera.py`, under a closed section at the bottom.

A phone alert opens the Activity page of the hosted app when you tap it.

Most trouble passes by itself. A live check that Meta does not answer in time is only a note, because the next check looks again. Checks that fail for 30 s become a warning on the Whenever's card. A rule that stops or an action that fails shows one message. Everything stays in **Activity > Problems** and in `data/problems.json`.

## How it works

1. **Trigger:** a person writes or speaks a sentence, records an example, or does both. Muse Voice Transcribe turns speech into text while the talk key is held. If there is only a recording, Muse Spark writes the sentence.
   A click on the view marks one object. SAM 3.1 finds that object from the click, and Spark names it. Wonder then knows what "this" means.
2. **Rule writer:** Muse Wonder writes the sensors and the code in the rule's own folder. It knows how the camera pipeline works, and it chooses the timing for each sensor: the type, the capture rate, how often to check, how many frames a check gets, and how much movement counts. A reviewer rejects facts that are side effects, such as a blink. It can install any Python library. Its tests must pass. Its sensors must also pass any recorded example, and a recording gives it the measured length of the action.
3. **Capture:** frames reach the server at the rate that the fastest sensor needs, up to 12 a second. They never wait for a model.
4. **Sensors:** each sensor only says what is true now. SAM 3.1 checks solid things and hand gestures. Spark checks actions and things with no clear shape, using required facts. Each check covers all the frames since the last check, so no time is skipped. A state, such as an open gate, starts only when two checks in a row say yes, each on its own new frames. The same frames asked twice repeat the same mistake: car headlights once made a closed gate look open twice. Before a state of a pointed thing starts, from checks or from a review, Spark also sees the thing's close-up in a camera that stays still, a moment before beside now. If the thing did not move, and only the light changed or its parts say no, nothing starts. A real opening moves the gate, whatever the light. In the saved logs of the night of 3 November, this stopped 7 false openings of 12 and kept all 3 real ones, in 3 runs each. **A watch plan decides when a check is paid.** Wonder writes one for each sensor, as data: the places where the event happens (the pointed thing, things found once by name such as "table", or a camera's whole view, only in cameras that stay still), how much a place must change to count, and a safety interval. A check is paid only when a watched place looks different from the last paid check, or after the safety interval. Until then the sensor keeps its last answer for free: a state that is on stays on, and a moment gets no. A state that is off still needs two paid yes answers on new frames to start. Every check saves how much each place still looks the same, so `replay.py` can score any plan on saved footage. On 3,965 saved gate checks, the pointed-thing plan paid for 27% and kept all 4 real openings. The 4 yes answers it skipped were 3 false openings and 1 duplicate. Without broken decoder frames it would pay for 10%. A moment, which can last for one frame, needs two yes answers on the same frames.

   A check sees only the cameras that its Whenever watches, large and each named, and a close-up of the pointed thing in each camera that stays still, cut from that camera's own full frame. A moving lens is left out while it turns and for 6 seconds after, and while it looks away from the thing. "Why?" shows this exact picture.

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

Each make of camera puts its stream at its own path, so a short address gives the known paths in order, and the reader tries the next one when a path gives no picture (`camera_sources.py`). Add `/CHANNEL` for a recorder and `?sub` for the smaller stream.

| Make | Address | Notes |
| --- | --- | --- |
| Hikvision, HiLook, Prama | `hikvision://admin:PASSWORD@IP[/channel]` | RTSP is on by default |
| EZVIZ with local RTSP | `ezviz-local://admin:VERIFICATIONCODE@IP` | Turn on RTSP under LAN Live View, Local Server Settings |
| CP Plus, Dahua, Amcrest, Imou, Lorex | `cpplus://admin:PASSWORD@IP[/channel]` (or `dahua://`, `amcrest://`, `imou://`, `lorex://`) | Imou: turn on RTSP in the app, use the safety code |
| TP-Link Tapo | `tapo://CAMERAUSER:CAMERAPASSWORD@IP` | Make a Camera Account in the Tapo app; battery models have no RTSP |
| Reolink | `reolink://admin:PASSWORD@IP[/channel]` | Check that RTSP is on; both Reolink paths are tried |
| Axis | `axis://USER:PASSWORD@IP` | |
| UniFi Protect | `rtsp://IP:7447/TOKEN` | Turn on RTSPS in Protect, then use port 7447 without `?enableSrtp` |
| Eufy (wired models) | `rtsp://USER:PASSWORD@IP/live0` | Turn on NAS (RTSP) in the Eufy app |
| Any ONVIF camera | `onvif://USER:PASSWORD@IP[:PORT]` | Whenever asks the camera for its stream |
| MJPEG or JPEG address | `http://IP/video.mjpg` or `snapshot:http://USER:PASSWORD@IP/snap.jpg` | Also Home Assistant, Frigate and go2rtc addresses |

Ring, Google Nest, Arlo, Blink, Wyze and most Xiaomi and Qubo cameras send video only to their own cloud. A bridge at home, such as go2rtc, docker-wyze-bridge, Scrypted or Home Assistant, gives an `rtsp://` address that works here.

`python3 camera.py --find` lists the ONVIF cameras on your network, with a line to copy. `python3 camera.py --test` reads one picture from each camera in `camera.env`, and says what to check when one fails: the password, the path, RTSP, or the network. The **Cameras** screen (Live, then the camera pill) shows the same steps for each make, and it lets you switch between this device's camera, the demo clip and security cameras at any time.

To send cameras to a hosted Whenever, such as https://wheneverwhatever.si, add `WHENEVER_SERVER=https://...` and `WHENEVER_CODE=<the access code>` to `camera.env`. The passwords of the cameras stay on that computer; only pictures leave it, two views a second at 1280 pixels for the live view.

The cameras go on one wall, so one model call watches all of them, and every rule covers every camera. While the reader runs, the page shows the wall in place of the Mac's camera, and a page that opens or reloads only watches. Each camera's full frame is also saved while it has movement, so zoom has real pixels. Frames saved with no rule cost nothing. The oldest pictures go first when a run reaches 20 GB.

## Many cameras on one server

Decoding every frame of every camera does not scale: five cameras took a whole 4-core server, and the live view fell below one picture a second. With a media server, Whenever decodes almost nothing until something happens, and it keeps every pixel when it does.

- **Relay as it is.** Each camera's video goes into [MediaMTX](https://github.com/bluenviron/mediamtx) unchanged (`deploy/mediamtx.yml`, `deploy/whenever-media.service`). It listens only on the server.
- **Live view with no server decoding.** The page plays each camera as low-latency HLS through Whenever's server, which checks the login (`/hls/...`). The browser decodes with its own hardware, at the camera's full size, and draws the wall, so pointing and outlines work as before. A browser that cannot play a camera's codec, such as Firefox with H.265, gets the old live wall, and only then does the server decode for it.
- **Watch cheaply.** For each camera, `camera.py` reads the size of every compressed frame, without decoding, and decodes only the keyframes (one every 4 s on these cameras). A second of video more than 1.6 times its usual size, a change between two keyframes, or a camera's own motion alarm (EZVIZ, `ezviz_events.py`) wakes full decoding. The camera's alarm only wakes; it never decides, because camera alarms miss things and report things that did not happen.
- **Decode in full only while something changes.** Full decoding at the camera's full size runs until 8 s after the scene is still, or while a rule waits for frames. Checks, zoom and galleries get every pixel.
- **A 12-hour buffer.** MediaMTX keeps the last 12 hours of every camera on disk, as the camera sent it (about 2.8 GB an hour for these five). A clip after an alert, a review of earlier frames, and Ask's closer looks at the past all read it. To change it, set `MTX_PATHDEFAULTS_RECORDDELETEAFTER` for MediaMTX and `WHENEVER_BUFFER_MIN` for the server.
- **Clips from the buffer.** An alert's clip is the camera's own video around the moment, at full size, converted once to H.264 so chats and mail apps play it.

On the Oracle server, at rest, the five cameras take about 40% of one core and the media server about 3%, where the old way took 150 to 270%. The settings are `WHENEVER_MEDIA` for `camera.py`, and `WHENEVER_HLS` and `WHENEVER_PLAYBACK` for the server. Without them, everything works the old way, as on a Mac.

## Turn a lens and use a light

The H9c has 2 lenses. Gate wide never moves and sees everything. Gate tracker moves and sees half that width, so it has 2 times the pixels on a thing. An aim point is a point in the Gate wide picture. We measured 5 points, and each one landed within 0.04 of that point, so this needs no calibration.

- **The fixed lens finds, and the moving lens goes to look closer.** `ctx.aim("Gate tracker", "white car")` makes SAM find the car in Gate wide and turns the tracker to it.
- **A moving lens wakes no checks.** Its picture changes when it turns, so only fixed cameras count as movement. Its full frames are saved when the fixed lens beside it sees something, and a gallery prefers them.
- **A light always has an end.** The light turns off by itself after at most 60 s, even if the server stops. Stop or Delete on a rule also turns off that rule's light.
- **A rule's words never reach a command.** `ezviz_control.py` has a fixed list of actions, `camera_control.py` checks each argument, and no shell runs. A rule can use a camera 12 times a minute.

The page has a row for this: arrows, **Click to aim** and the light. After **Click to aim**, click a place in the Gate wide picture.

The lens also follows people by itself, so it can leave the point that a rule aimed at. Frames from a new view start about 6 s later, because the lens takes about 2 s to arrive and the video comes about 4 s late. So a rule aims at a thing that stays, or lets the camera follow a person.

The lens can also stay where it lost a person, such as on a wall. Every 30 seconds, while nothing is going on, SAM looks for the pointed thing in the moving lens. After two misses in a row, the Whenever's card says so, its checks leave that lens out, and Whenever aims the lens back at the thing, found in the fixed lens beside it, at most once every 5 minutes.

**Fixed or moving.** No camera says whether its lens turns, so Whenever judges it. A camera that Whenever can turn is moving. So is one whose whole picture shifts: `camera.py` compares each camera's picture about four times a second, and one shift that explains the change is a pan, while a person walking or a light changing is not. While a lens turns, its picture adds no motion, so a pan does not start a check. Under **Live > Cameras: which ones move**, you can set each camera to Stays still or Moves, and your choice wins. The tags are in `data/cameras.json`.

When you point at a thing, a camera that stays still keeps the thing's place, which is a good clue. In a moving camera the place changes, so a rule finds the thing there by its look: the close-up from the click is saved with the rule as `pointed/clicked.jpg`, and `pointed.json` names every camera that shows it, with its kind. Rule code reads this as `ctx.pointed`, and can pass the close-up as `reference` to `ctx.check`.

## Actions: reach anything outside the camera

A rule's code has no network. It names an action, and the server sends the request.

- **Phone:** `ctx.notify(text, picture)` also goes to every place that you paired under **Actions**. `ctx.act("Phone", text=..., picture=frame)` sends one on purpose. One occurrence is one message. Its first alert goes out at once with the picture of only its cameras, and Telegram then swaps the picture for the clip in the same message. Email waits for the clip. A later alert of the same occurrence with a new picture, such as the person, answers the first message. A later note with nothing new to see, such as "no clear picture", stays in the app. From `on_tick`, pass `at=` the time of the event, so the alert shows that event's cameras. The places:
  - **Browser alerts** (Web Push). On the hosted app, scan the QR code with a phone and tap **Turn on alerts**. Chrome on Android needs nothing more. An iPhone needs iOS 16.4 or later, and Whenever on its Home Screen; the pairing page shows how. The pairing link works once, for 15 minutes, and it also logs that phone in, so a tap on an alert opens the proof. The server makes its own push keys (`WEBPUSH_PRIVATE_KEY` in `actions.env`) and needs the `cryptography` package.
  - **Telegram.** The sheet opens BotFather with `/newbot` typed for you, gives a name and a username to copy, and asks for the token. Then you tap **Start** in your bot. The token goes in `actions.env` as `TELEGRAM_BOT_TOKEN`. Give each server its own bot, because only one program can read a bot's messages.
  - **Email** through Amazon SES. Put `AWS_SES_ACCESS_KEY_ID`, `AWS_SES_SECRET_ACCESS_KEY`, `AWS_SES_REGION` and `WHENEVER_MAIL_FROM` in `actions.env`. On a Mac, `AWS_SES_PROFILE=<name>` can use a profile from `~/.aws` instead of the two keys. The sender's domain must be verified in SES (Easy DKIM: three CNAME records), and the key needs only `ses:SendEmail` and `ses:SendRawEmail` on that identity. Do not use an admin key. An address gets alerts only after someone taps the button in the first message.
  - **ntfy app.** Whenever makes a private topic (`NTFY_TOPIC`) for the [ntfy](https://ntfy.sh) app, and you confirm a test alert.
- **Any web request:** under **Actions** on the page, describe the action in plain words, such as "Turn my desk lamp on or off. It is Hue light 3." You can paste docs or an example request. Wonder writes one request with blanks: `{on}` for an input, and `{secret:HUE_USER}` for a key. You check the request, press **Test it**, and save it. Then any new rule can call `ctx.act("Desk lamp", on=False)`.

The same safety rules hold:

- A key stays in `actions.env`, which Git ignores. It never reaches rule code, Wonder or the page, and it is hidden in any reply.
- An action reaches only the host that you saved. It follows no redirect, and its host has no blanks.
- A rule can run 12 actions a minute.
- With `WHENEVER_PUBLIC=1`, as on a hosted server, an action reaches only public `https` hosts.

## Journal and reports

The server writes everything into one journal, `data/journal.db`: each event and how long it lasted, each action, each notification, and each fact that a rule records with `ctx.record("waited", table="4", seconds=312)`. The journal belongs to you, not to one rule, and it stays across restarts.

A report is a rule with a **schedule**, such as "Every Monday at 9 AM, send my phone the busy hours of last week." A schedule fires with no camera. The rule reads `ctx.history(...)`, does the arithmetic, draws a chart and sends it. So no rule needs its own report code, and one report can use what all your rules saw. Schedules use your own clock. A hosted server sets `WHENEVER_TZ`, such as `Asia/Kolkata`.

Live rules return after a restart, with their saved values and the numbers they show. The list is in `data/rules.json`. Today's model spend also survives a restart (`data/spend.json`), so a restart cannot reset the daily limit.

**Activity** shows one card for each occurrence: when it started and ended, the clip of its cameras, the pictures that its job found, and where each alert went. Noted facts, drawings and the model's notes are under **Details** on the card. The "Why?" view also opens the proof of an older run.

## Ask

One box on every screen takes a question in plain words: "When did the gate last open?", "When was a person last at the gate?", or, after pointing at a car, "When did this arrive?" Spark first turns the question into a plan: the kind of answer, the cameras, the span, what to look for, and how fast that thing moves.

- **From the journal.** A question about what a rule already saw, such as "How many times did the gate open yesterday?", is answered from the journal. The answer is exact and costs one small call.
- **Now.** A question about now is one look at the live picture.
- **The past, in the footage.** Whenever looks the way a careful person would: wide and cheap first, then closer where something may be.
  1. **Moments.** The saved frames show when each camera changed. Each stretch of change is an episode. The plan's pace sets how many moments of each episode to see: a person who walks past needs several, a parcel needs one now and then.
  2. **Free ranking.** Meta's Perception Encoder (PE-Core-S-16-384, int8, on the server's CPU) embeds each camera once a second while it changes and once a minute while it is still. Each moment gets a score: how much it looks like the words ("a photo of a person"), less how its own camera usually looks around then. So a camera that always shows the gate does not win on its view alone. A pointed thing adds its own picture to the words. Moments that the index lacks are embedded during the question.
  3. **Contact sheets.** Spark judges 9 moments on one picture, each labelled with its camera and time (the grid idea of T*). It says which moments show the claim and which ones it cannot tell. Those get a closer look from the camera's own recording, at 2 frames a second (the loop of VideoAgent).
  4. **Full-size proof.** Before Whenever says that something happened, the best moment is judged alone at full size. A hit that this look rejects does not end the search.
  5. **An honest no.** Six sheets are free, about 3 cents. If nothing is found and moments are left, the answer says how many moments it saw and how many it did not, and it offers to look at more for a stated price.
- **When did this arrive?** For a pointed thing in a camera that does not move, no model searches. Its place is compared with how it looks now, 1, 2, 4, 8 minutes back and more, until it looks different. Then Whenever halves the gap. Spark only confirms the two frames on each side of the change. A change of light at dusk is not an arrival: the comparison evens out brightness, and Whenever looks further back when the thing was already there.

An answer shows its proof: the picture of the moment and a clip from the recording. "Watch for this from now on" turns the question into a Whenever. The index is `data/moments.db`. To build its model files, see `deploy/export_pe.py`.

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
| `ctx.detect(things, frames, places)` | The local detector finds people, cars, chairs, cups and the other COCO kinds in each camera, for free. Each thing gets an id that stays while it stays put, how many seconds it stayed, how tall its box is (sitting or standing), and its place |
| `ctx.places(thing, camera)` | Every such thing in a camera that stays still, such as "table", named "table 1", "table 2" by rows. SAM finds them once |
| `ctx.check(claim, frames, place=...)` | The model sees only one place's close-up, such as one table, so other things in view cannot change the answer |
| `ctx.steady(key, value, looks)`, `ctx.since(key, true)` | A count that must repeat before it counts, and how long a condition has been true, kept across restarts |
| `ctx.detect(..., pose=True)` | Free body points for each person (RTMPose, Apache-2.0), and whether a hand is up, they lie down, or they sit |
| `ctx.visit(key, present)` | The visits at one place: when guests sit and leave a table, how long it stood empty, how long a car stayed |
| `ctx.crossings(line, found)`, `Line`, `edge` | Entries, exits and footfall across a line, such as a door's threshold, with a dead zone so people who stand on it count once |
| `beside(place, side, size)` | A zone next to a place, such as the floor in front of a counter where people queue |
| `center`, `seat`, `foot`, `inside`, `overlap`, `place_of` | Box helpers from `rule_runtime`, such as "which table is this person at" |
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

**Count for free, pay for meaning.** `detect.py` runs YOLOX-S (Apache-2.0, Megvii) as ONNX on the server's CPU, about 50 ms for one camera picture, with no model call. A rule counts and places things with it, and pays Spark only when a count or a timer says that one place may need attention, and then only for that place's close-up. A watch plan can also use `wake: "count"`: a check is paid only when the number of people (or cars, or any COCO kind) at a watched place changes, and the next look sees the same new number. On the 43-second restaurant clip in our tests, the detector found the 6 tables by itself, and it counted 2 seated customers at one table from second 16 and 4 at another from second 38, which is what the video shows. Waiters who walk past do not count, because they do not stay and their boxes are tall. A count wake paid for 11 of 43 looks of that busy clip, where a pixel-change wake paid for 39. On five minutes of real night footage from our gate camera, at about 1.5 frames a second, `ctx.crossings` counted the one person who walked out through the gate, once, and nothing else. Run `python3 detect.py --download` once to get both models; it checks each file's sha256. [docs/operator-questions.md](docs/operator-questions.md) lists what a restaurant operator asks of the cameras, and the blocks that answer each question.

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

- **Local detector:** the model is not in Git. Run `sudo -u whenever /srv/whenever/venv/bin/python /srv/whenever/app/detect.py --download` once. Without it, `ctx.detect` gives an error that says so, and a counting watch plan pays for every check.

To send a new version, run `deploy/deploy.sh <ssh host>` from this folder. It copies only the files that Git would publish.

## Test

To score a watch plan on every saved check of a Whenever, at no model cost:

```bash
python3 replay.py <rule id> [--sensor <id>] [--watch '<plan as JSON>']
```

It prints the paid checks, the paid checks for each hour, and each live yes answer that the plan would keep as no, to look at.

To test a rule on a past run, with no camera:

```bash
python3 experiments/overlay/replay_run.py logs/<run> <first frame> <last frame> "<sentence>"
```

The unit tests need no API key:

```bash
python3 -m unittest discover tests
```
