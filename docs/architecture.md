# Architecture: from one sentence to one alert

This page follows one real rule through every stage of Whenever, with links to the code. The rule is the cafe rule in [examples/cafe-hand-raise](../examples/cafe-hand-raise):

> When a guest in the cafe raises a hand to call for service, alert the Waiter at once. If no staff member reaches that table within 1 minute, alert the Manager.

Muse Wonder wrote this rule on 5 November 2026, for the demo's Cafe camera. The folder holds its program, its sensor settings and its own test, as the server saved them.

**This runtime was built during the hackathon.** Wonder writes and tests a Python program for each rule. A second Wonder call reviews the program against the person's words. The program runs in a sandbox and reaches models only through typed calls that the server makes. Every generated rule stays inspectable as code: the page shows it, and [examples/](../examples) runs three of them with no API call.

## The chain at a glance

| Stage | What happens | Who does it | Main code |
|---|---|---|---|
| 1. Request | A person types or says one sentence. Spark says back how it reads the words. | Person, Muse Spark, Muse Voice Transcribe | [server.py:4929](../server.py#L4929), [writer.py:1159](../writer.py#L1159), [voice.py](../voice.py) |
| 2. Program | Wonder writes the sensors, `rule.py` and `test_rule.py` in the rule's folder, and runs the test. | Muse Wonder | [writer.py:1012](../writer.py#L1012) |
| 3. Review | A reviewer checks each fact and the whole program against the words. | Muse Wonder, in a separate call | [writer.py:676](../writer.py#L676), [writer.py:711](../writer.py#L711) |
| 4. Sandbox | The program runs in its own process, folder and Python, with no network and no key. | Local: `sandbox-exec` on a Mac, Landlock and seccomp on Linux | [sandbox.py:114](../sandbox.py#L114), [jail.py:52](../jail.py#L52) |
| 5. Visual checks | A watch plan decides when a check is paid. A paid check asks Spark about the sensor's facts. | Local watch plan, then Muse Spark (or SAM 3.1 for a solid thing) | [server.py:3929](../server.py#L3929), [meta_api.py:450](../meta_api.py#L450) |
| 6. Temporal state | Checks become events: a state starts on two yes answers in a row, and ends after some seconds with no yes. | Local: `engine.py` | [engine.py:35](../engine.py#L35), [server.py:4022](../server.py#L4022) |
| 7. Rule logic | The program gets `on_event` and a tick each second. It keeps timers and jobs in `ctx.state`. | The generated program, with free local models | [examples/cafe-hand-raise/rule.py](../examples/cafe-hand-raise/rule.py) |
| 8. Action | An alert goes only to the places named "Waiter" or "Manager", with the event's picture and clip. | Local server, Telegram, Web Push, ntfy or email | [server.py:2555](../server.py#L2555), [server.py:2868](../server.py#L2868) |
| 9. Evidence | Each check, step, alert and fact goes to the evidence store and the journal. "Why?" shows them. | Local: `evidence.py`, `journal.py` | [evidence.py:121](../evidence.py#L121), [server.py:1294](../server.py#L1294) |

## Which model does what

Muse Wonder, Muse Spark, SAM 3.1 and Muse Voice Transcribe are paid API calls. PE-Core is a Meta model that runs on the server's own CPU. The other local models are not Meta models.

| Model | Meta? | Where it runs | What it does in Whenever | Code |
|---|---|---|---|---|
| Muse Wonder 1.0 (`muse-wonder-1.0`) | Yes | Meta's API, paid | Writes each rule's sensors, code and tests. Reviews facts and code against the words. Answers `ctx.ask(model="wonder")`. Turns a plain-words action into a web request. | [meta_api.py:28](../meta_api.py#L28), [writer.py:1041](../writer.py#L1041) |
| Muse Spark 1.3 (`muse-spark-1.3`) | Yes | Meta's API, paid | Judges the sensor's facts in frames. Reviews the last 24 s when no single frame proves an action. Checks one frame for `ctx.check` and answers `ctx.ask`. Compares two people, names a clicked thing, reads back a rule and plans Ask | [meta_api.py:450](../meta_api.py#L450), [meta_api.py:518](../meta_api.py#L518), [meta_api.py:330](../meta_api.py#L330), [meta_api.py:281](../meta_api.py#L281) |
| SAM 3.1 (`sam-3.1`) | Yes | Meta's API, paid | Finds a solid thing for a target sensor, finds places such as tables once, outlines the proof, finds the thing under a click | [meta_api.py:205](../meta_api.py#L205), [meta_api.py:232](../meta_api.py#L232), [meta_api.py:236](../meta_api.py#L236) |
| Muse Voice Transcribe 1.0 | Yes | Meta's API, paid | Turns speech into text while the talk key is held | [voice.py](../voice.py) |
| Perception Encoder, PE-Core-S-16-384 | Yes, open weights | The server's CPU, int8 ONNX, free per call | Embeds saved moments so Ask ranks them before any paid call. Remembers how a person looks for the search in words and for names carried by clothes. | [embed.py:96](../embed.py#L96), [moments.py:128](../moments.py#L128), [search.py:335](../search.py#L335), [people.py:424](../people.py#L424) |
| YOLOX-S | No (Megvii, Apache-2.0) | The server's CPU, free per call | `ctx.detect`, count wakes, the live overlay | [detect.py:179](../detect.py#L179) |
| RTMPose-m | No (OpenMMLab, Apache-2.0) | The server's CPU, free per call | Body points, `hand_up`, `sitting`, `lying` | [detect.py:236](../detect.py#L236) |
| YuNet and ArcFace R50 (or SFace) | No (OpenCV Zoo, InsightFace) | The server's CPU, free per call | Finds faces and matches them for `ctx.who` | [people.py:161](../people.py#L161), [people.py:565](../people.py#L565) |

ArcFace R50's weights are for non-commercial use only. `WHENEVER_FACE_MODEL=sface` uses SFace (Apache-2.0) instead.

## One rule, stage by stage

### 1. Request

The person types the sentence in **When** and taps the actions in **then**, or holds the mic. Muse Voice Transcribe turns speech into text ([voice.py](../voice.py)).

Before Wonder spends money, Spark says back the trigger, the actions and the cameras ([writer.py:1159](../writer.py#L1159), route at [server.py:4929](../server.py#L4929)). The README gives about $0.002 for this step.

Then the page posts the sentence. `App.start_job` ([server.py:1939](../server.py#L1939)) refuses it at the spend limit or when Wonder already writes 2 rules. It starts `App._write` on its own thread ([server.py:1960](../server.py#L1960)).

### 2. Wonder writes a program

`sandbox.create` makes the rule's folder with its own virtual environment and a copy of `rule_runtime.py` ([sandbox.py:44](../sandbox.py#L44)). `writer.write_rule` then runs Wonder for up to 28 turns ([writer.py:1012](../writer.py#L1012)).

Wonder has four tools ([writer.py:548](../writer.py#L548)):

- `write_file` writes a file in the folder.
- `run_command` runs a command in the sandbox, with no network.
- `install_packages` installs built wheels only.
- `finish` submits the sensors, the plan and any schedules.

The guide that Wonder reads is `PROMPT` ([writer.py:25](../writer.py#L25)). It explains:

- what a sensor is, and how the camera pipeline times it
- the watch plan, which decides when a check is paid ([writer.py:121](../writer.py#L121))
- the building blocks, such as `ctx.check`, `ctx.detect` and `ctx.who` ([writer.py:183](../writer.py#L183))
- "Alert at once" with the frame that the sensor's paid check proved ([writer.py:215](../writer.py#L215))
- counting and places for free with the local detector ([writer.py:236](../writer.py#L236))
- tagged people and their roles, such as "staff" ([writer.py:264](../writer.py#L264))
- jobs that outlast their trigger, with deadlines in `ctx.state` ([writer.py:469](../writer.py#L469))
- how to test with scripted model answers ([writer.py:514](../writer.py#L514)).

For the cafe rule, `rule.json` lists Wonder's steps. It read `rule_runtime.py` with `sed` and `grep`, wrote `rule.py` and `test_rule.py`, and ran the test.

### 3. Review against the words

On `finish`, the server runs `test_rule.py` again in the sandbox ([writer.py:906](../writer.py#L906)). A failed test keeps the rule off. Then two reviewers run, each a separate Wonder call that does not see Wonder's reasoning:

- `review_facts` rejects a required fact that is a side effect or one person's style ([writer.py:676](../writer.py#L676)).
- `review_rule` reads the whole program against the words, for 10 kinds of fault ([writer.py:711](../writer.py#L711)). Examples are "gives up too early", "wait on the wrong thing" and "unchecked claim".

A fault sends Wonder back to work. An approval holds only for the exact sensors and code that the reviewers read, so a change after it is reviewed again ([writer.py:931](../writer.py#L931)). After 3 rounds of changes with a fault left, the job stops, and the rule does not go live ([writer.py:856](../writer.py#L856)).

Both reviewers changed the cafe rule (from its `rule.json`):

- The fact reviewer rejected "That person's arm is extended upward from the shoulder", because people also call with a bent elbow.
- The code reviewer found that any standing person at the table stopped the Manager alert. It wrote: "Polling for a standing person is only a proxy for the actual goal". Wonder changed the code to need a person whom `ctx.who` names with the role "staff".

If the person recorded an example, Spark also checks the sensors on it ([writer.py:832](../writer.py#L832)). Then the server saves `rule.json` with Wonder's steps ([server.py:1994](../server.py#L1994)) and starts the rule ([server.py:1996](../server.py#L1996)).

### 4. The sandbox and the typed calls

`sandbox.start_worker` starts `rule_runtime.py` in the rule's folder ([sandbox.py:114](../sandbox.py#L114)). On a Mac, `sandbox-exec` denies the network, the home folder and writes outside the folder ([sandbox.py:21](../sandbox.py#L21)). On Linux, `jail.py` applies Landlock to files ([jail.py:52](../jail.py#L52)) and seccomp to internet sockets ([jail.py:93](../jail.py#L93)).

The program holds no key and has no network. Each `ctx` block becomes one JSON message to the server ([rule_runtime.py:824](../rule_runtime.py#L824)). `App._rule_call` runs it from a fixed table of blocks and sends the answer back ([server.py:2449](../server.py#L2449)). The answers come back as typed objects:

- `ctx.check` returns `Check` items: frame, passed, confidence and what the model saw ([rule_runtime.py:433](../rule_runtime.py#L433)).
- `ctx.detect` returns `Found` items with a box, a track id, seconds stayed, a place and body points ([rule_runtime.py:459](../rule_runtime.py#L459)).
- `ctx.who` sets `.who` on each person: name, role, how sure and by what ([rule_runtime.py:546](../rule_runtime.py#L546)).

A rule can make 60 model calls a minute ([server.py:124](../server.py#L124)). At the spend limit, paid blocks stop, and the free ones go on: the detector, faces, saved places, the journal and actions ([server.py:2462](../server.py#L2462)).

### 5. Visual checks

Frames reach `App.frame`, which saves each one and starts any check that is due ([server.py:4140](../server.py#L4140), [server.py:3908](../server.py#L3908)). A check never holds up the camera.

`App._check` first asks the sensor's watch plan whether to pay ([server.py:3929](../server.py#L3929)). `held_answer` keeps the last answer for free while the watched places look the same, or while the count of things there stays the same ([server.py:1830](../server.py#L1830)). The cafe sensor watches the whole Cafe view and wakes on a change. The night driveway rule wakes on a free count of cars.

A paid check sends the chosen frames to Spark with the sensor's facts ([server.py:3751](../server.py#L3751), [meta_api.py:450](../meta_api.py#L450)). The cafe sensor needs both facts in one frame:

- "A person's hand is at or above the height of their head"
- "That person is seated at a cafe table"

A sensor with a solid target, such as "thumbs up", asks SAM 3.1 instead ([server.py:3727](../server.py#L3727)). When a check fails but Spark sees part of the action, a review reads up to the last 24 s across time ([server.py:3772](../server.py#L3772), [server.py:3807](../server.py#L3807)).

### 6. Temporal state

`engine.SensorState` turns checks into events ([engine.py:35](../engine.py#L35)):

- A "state", such as a raised hand, starts on two yes answers in a row, each on new frames. It ends after `clear_after_s` with no yes. The cafe sensor uses 4 s.
- A "moment", such as a car that drives in, needs two yes answers on the same frames. It fires once for each burst.

`App._settle` applies checks in time order, because a later check can finish first ([server.py:3987](../server.py#L3987)). `App._act` writes the event to the journal and sends it to the program with its frames and its proof frame ([server.py:4022](../server.py#L4022)).

The program keeps its own time state in `ctx.state`. The worker saves it to `data/state.json` after each job, so it survives a restart ([rule_runtime.py:887](../rule_runtime.py#L887)). The server sends `on_tick` once a second ([server.py:2094](../server.py#L2094), [server.py:236](../server.py#L236)).

### 7. The generated logic

These lines are from the cafe rule that Wonder wrote, cut to the timer and the escalation. The `#` lines that give line numbers are added here, and `...` marks lines left out. The rest is Wonder's code. The full file is [examples/cafe-hand-raise/rule.py](../examples/cafe-hand-raise/rule.py).

```python
# on_event (lines 98-115): one job for each call, with its own deadline, then the Waiter at once.
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
    ...
    ctx.notify(text, picture=picture, at=t, to="Waiter")

# on_tick (lines 136-176): every 4 s, or at a deadline, look for staff at each waiting table.
    due = any(t >= j.get("deadline", t + 1) and not j.get("escalated") for j in active.values())
    last = ctx.state.get("last_staff_check", 0)
    if not due and t - last < 4:
        return
    ...
    for key, job in list(active.items()):
        staff = _staff_at_table(ctx, job, people)
        if staff is not None:
            ...
            job["done"] = True
            ...
            continue
        ...
        if t >= job.get("deadline", t + 1) and not job.get("escalated"):
            job["escalated"] = True
            job["done"] = True
            ...
            ctx.notify(text, picture=latest_frame, at=job.get("t"), to="Manager")

# _staff_at_table (lines 81-83): only a person whom ctx.who names as staff counts.
        who = getattr(p, "who", None)
        if isinstance(who, dict) and who.get("role") == "staff" and (who.get("sure") or 0) >= 0.7:
            return p
```

What the lines do:

- **One job for each call.** `on_event` keys each call by table, time and number. A second table's call gets its own job and its own deadline, so one guest never replaces another guest's wait.
- **The Waiter at once.** The alert goes out inside `on_event`, with no model call first. Earlier lines find the raiser's table with the free detector and body points. They pass the guest's `Found` as the picture, so the alert outlines that guest.
- **A free timer.** `on_tick` runs each second, but it looks for staff only every 4 s, or when a deadline is due. Each look uses the free detector and `ctx.who`, so the wait costs no model call.
- **Staff, not just a person.** A job ends only when `ctx.who` names a person at that table with the role "staff", at least 0.7 sure. This is the reviewer's fix from stage 3.
- **The Manager once.** At 60 s, with no staff, the job is marked escalated and done, so the Manager alert goes out once. `at=job.get("t")` ties it to the original event, so the alert shows that event's cameras.

[examples/cafe-hand-raise/independent_test.py](../examples/cafe-hand-raise/independent_test.py) checks this from the words alone, with negative cases. For example, staff at 40 s means no Manager alert, and staff at another table do not count.

### 8. Action

`ctx.notify` adds one action to the program's output ([rule_runtime.py:715](../rule_runtime.py#L715)). The server reads it in `Rule._handle` ([server.py:1009](../server.py#L1009)) and calls `App.notify`, which runs beside the rule ([server.py:2548](../server.py#L2548)). `App._notify` ([server.py:2555](../server.py#L2555)) then:

1. finds the occurrence that the alert belongs to
2. drops the same words about the same occurrence within 90 s ([server.py:2922](../server.py#L2922))
3. cuts the picture from only the cameras where the event happened, and outlines the subject ([server.py:2803](../server.py#L2803))
4. keeps a repeated alert off the phone during a quiet time, which is off on a real site and 15 minutes on the demo ([server.py:2909](../server.py#L2909))
5. sends it only to the alert places named "Waiter" or "Manager" ([server.py:2868](../server.py#L2868))
6. adds the clip to the same message, from the media server's 12-hour buffer or from saved frames ([server.py:2841](../server.py#L2841)).

### 9. Evidence

Every step leaves a record that a person can open later:

- **Each check** is saved with its frames, the facts, the answer and the proof frame ([evidence.py:121](../evidence.py#L121)). "Why?" shows this record.
- **Each step of a job**, such as a detector look or a model answer, is added to its event ([server.py:2476](../server.py#L2476)).
- **Each alert, log and fact** goes to the feed and to the journal in `data/journal.db` ([server.py:1294](../server.py#L1294), [journal.py](../journal.py)). The cafe rule records `hand_raised`, `staff_reached` and `escalated`, so a report can count waits later.
- **The program itself** stays in the rule's folder with its test and `rule.json`. The rule's page shows the code ([server.py:2087](../server.py#L2087)).

## Inspect it yourself

- `python3 examples/run_examples.py` runs three generated rules and their tests, with no API call ([examples/README.md](../examples/README.md)).
- `python3 replay.py <rule id>` scores a watch plan on saved checks, at no model cost ([replay.py](../replay.py)).
- `python3 -m unittest discover tests` runs the offline unit tests.

## Why does this need AI?

A motion detector says that pixels changed. A fixed object detector says that a person box exists. Neither says what the requests in this repo mean:

- an open gate, as against headlights on a closed gate
- a guest who raises a hand to call for service, as against a phone at the ear
- a staff member at that table, as against a guest who stands up.

The free body-point check alone is not enough for the second one. In 2,698 looks of our footage, about 85% of its `hand_up` hits were real raised hands (README). Each sentence also needs its own logic: a timer, an escalation, a time of day, one job for each guest. A fixed product would need a feature for each. Here Wonder writes that logic as a program, and Spark and SAM judge the frames.

The evaluation in [eval/README.md](../eval/README.md) compares Whenever with a motion baseline. Its numbers and its method are there, and this page does not repeat them.
