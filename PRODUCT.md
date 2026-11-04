# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, with equal weight:

- **Owners of homes and small shops** (a café, a restaurant, a shop, a family home). They already have cameras, often EZVIZ or Hikvision. They set rules in plain words and get alerts on their phone. They are not technical, and they check the app in short moments, mostly on a phone.
- **CCTV operators** at a desk who watch many cameras for a client. Whenever makes them more efficient, as coding agents make developers more efficient. It does not replace them. Their job is to see what matters across many cameras, act on it and prove what happened.

## Product Purpose

Whenever is IFTTT for the physical world. A camera is the trigger, a person describes the rule in plain words, and any action follows: a phone alert, a light, a camera lens, any web service, or a report later. Success means a person turns a sentence into a working rule in about a minute and trusts each alert because it comes with proof.

## Positioning

The trigger is a camera and plain words, not a fixed list of events. Meta's Muse Wonder writes each rule as real code, tests it, and a reviewer checks it against the person's words. Muse Spark and SAM 3.1 watch the frames. The same harness runs a gate alert, a restaurant table timer or a weekly report with no new code. Actions are open: the person describes a web service in words and the AI connects it.

## Operating Context

- Cameras: the browser camera, or real security cameras read by `camera.py` (EZVIZ cloud, RTP, RTSP). A pan-tilt lens and a light can be controlled.
- Alerts reach every place that the person paired, with a picture: browser alerts (Web Push), Telegram, email (Amazon SES) or the ntfy app.
- Problems stay quiet: a missed check is a note, and only trouble that needs the person makes a message. Activity > Problems keeps all of it.
- A journal records every event, action and fact across all rules and restarts. Reports are scheduled rules that read it.
- Hosted demo: https://wheneverwhatever.si (pending DNS) and https://whenever.140-245-12-216.sslip.io, behind an access code, with a $5 daily model budget.
- Built for the Meta Global AI Developer Hackathon (startup track). The demo is a 3-minute video in the style of an Instagram Reel.

## Capabilities and Constraints

- One rule is called **a Whenever** ("Create a Whenever", "My Whenevers").
- A Whenever has a trigger (camera sensors or a schedule) and actions. Up to 5 run at once.
- Building blocks: frames, ask, check, find, gallery, arm, draw, image, notify, act, record, history, aim, move, light.
- Rule code runs in a sandbox with no key and no network. Keys stay in private files.
- Writing a Whenever costs about $0.15 to $0.35 and takes 30 to 90 seconds. Model calls cost money, so spend stays visible.
- Rules cannot hear yet. Questions about the past are not built yet.
- Stack: the existing codebase, plain HTML, CSS and JavaScript served by `server.py`, with no build step.

## Brand Commitments

- Name: Whenever. Domain: wheneverwhatever.si.
- Tagline: "Whenever it happens, do whatever you want."
- The repo and the product name only the owner, Akhilesh Khajuria. No Claude or Anthropic credit.
- Visual direction (chosen 2026-11-03): IFTTT-like, the category standard, but with Whenever's own twist, so it never reads as a copy of IFTTT.

## Evidence on Hand

- Real runs on the owner's cameras: Driveway, Garden, Side, Gate wide, Gate tracker (logs and rules stay private and out of the repo).
- AI-made test pictures and a test clip in `testdata/`, made with Muse Image.
- Measured costs and test results are in README.md.
- No customers, testimonials or benchmarks exist. Do not invent them.

## Product Principles

1. **One sentence is the program.** The person's words are the source of truth, and the UI shows how the AI understood them before money is spent.
2. **Proof with every alert.** An alert shows the frame and the facts that passed, never a guess presented as fact.
3. **Any trigger, any action.** The UI never hard-codes one use case. Homes, shops and operators use the same flow.
4. **Cost and privacy stay visible.** The person sees what a Whenever costs and where frames go.
5. **The phone is the main screen for owners. The camera wall is the main screen for operators.**

## Accessibility & Inclusion

Readable on a phone in daylight, usable with one hand, keyboard reachable on desktop. No specific standard has been set.
