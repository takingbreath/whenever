# Deployment evidence: the builder's own installation

**This is the builder's own deployment, not a customer site.** Akhilesh Khajuria runs Whenever on his own cameras, and runs the public demo beside it. The numbers here come from his notes and from this repo. They show that the system runs on real cameras for one owner. They do not show what a customer would see.

## What runs

**The server.** An Azure virtual machine named whenever-in, in Central India, with 16 vCPUs and 64 GB of memory, on Ubuntu 24.04. It has served the real cameras since 4 November 2026 at 13:39 IST. The `deploy` folder holds its systemd units, Caddy sites and the deploy script.

**Five real camera views on one wall:**

| View | Camera | Notes |
|---|---|---|
| Driveway | EZVIZ C8C | |
| Garden | EZVIZ C3N | |
| Side | EZVIZ C3N | |
| Gate wide | EZVIZ H9c, channel 1 | 2880x1620 HEVC. This lens never moves |
| Gate tracker | EZVIZ H9c, channel 2 | The same camera's pan-tilt tracker lens |

- `camera.py` reads the cameras through EZVIZ's cloud, with `ezviz:` and `ezviz-rtp:` addresses. The repo shows them with example serials only (`camera.env.example`).
- A local MediaMTX keeps the last 12 hours of every camera on disk, as the camera sent it (`recordDeleteAfter: 12h` in [deploy/mediamtx.yml](../deploy/mediamtx.yml)). The README gives about 2.8 GB an hour for these five.
- The free live analyzer, the detector, faces and embeddings run on the same machine's CPU.

**The demo beside it.** A second Whenever runs on the same machine, behind its own access code ([deploy/demo](../deploy/demo)).

- Its cameras are recorded clips of these cameras, plus a cafe video, which play in a loop through the demo's own media server.
- It has its own rules, people, alerts and data. Its systemd units hide the real server's keys, cameras, rules, data and footage.
- It has its own small daily budget inside the owner's budget for both servers. The example settings give $4 a day for the demo inside $10 for both ([deploy/demo/settings.env.example](../deploy/demo/settings.env.example)).

## Problems we saw, and what we changed

Each problem below was seen on this installation or its demo. Each change is in the code.

### 1. Looping video made checks expensive

**What we saw.** On the demo, four rules that Wonder wrote spent about $13 in one hour, for 3,387 Spark calls. Looping video always "changes", so a check that wakes on a change paid almost every time.

**What we changed:**

- **Watch plans that wake on a free count.** A check is paid only when the number of people or cars at a watched place changes, and the next look sees the same new number. The local detector does the counting ([server.py:1830](../server.py#L1830), `wake: "count"` in the writer's guide at [writer.py:121](../writer.py#L121)).
- **An idle sleep.** With no page open for 15 minutes, the demo stops paying for checks until a visitor comes back (`WHENEVER_IDLE_PAUSE_S`, [server.py:1200](../server.py#L1200)).
- **A 15-minute quiet time for repeats.** The same words from the same Whenever go to a phone at most once in 15 minutes on the demo. The page still lists each one ([server.py:2909](../server.py#L2909), commits `79dbc92`, `632141e` and `4a6fb5e`). The quiet time is 0 by default, as on a real site, so every alert goes out there.

**After the change.** A gate rule woken on a free count cost about $2.40 an hour on the demo. [eval/cost-results.md](../eval/cost-results.md) has this and the other costs, with what is measured and what is estimated.

### 2. Poor faces were named with high confidence

**What we saw.** The demo's recorded cameras show faces of 22 to 30 pixels, faces that look down, and the tops of heads. These were matched to tagged people at 85% to 99%. The confidence curve came from webcam faces that looked at the camera.

**What we changed.** A face names someone only when it passes a quality gate ([people.py:65](../people.py#L65), [people.py:161](../people.py#L161), commit `9ff9568`):

- at least 28 pixels wide
- toward the camera, by the positions of the eyes, nose and mouth (`frontal`, [people.py:145](../people.py#L145))
- a face detector score of at least 0.85.

An anonymous visitor is made and matched only from a face of at least 40 pixels.

**Result.** The gate kept 97% of the webcam faces (525 of 542). It rejected 16 of the 23 faces from the gate camera.

### 3. An alert arrived about 30 s late

**What we saw.** A rule ran a second model check on the event before it alerted. Each alert then arrived about 30 s late. On the demo's gate rule, that was after the gate had closed again (the writer's guide says so at [writer.py:215](../writer.py#L215)).

**What we changed.** The sensor's own paid check already proves the event. So a rule now alerts at once with that check's proof frame, `ctx.event.proof`. It looks for a better picture only for what the event does not show, such as a clear face, and sends it after. The writer's guide says this ([writer.py:215](../writer.py#L215)). The code reviewer's rules also say that a second check before the first alert is not needed, because it makes the alert late ([writer.py:735](../writer.py#L735)). Commit `021dc62` made this change.

**Result.** After the change, the owner measured 5 to 10 s from the event to the alert. The [gate-open example](../examples/gate-open) shows a rule written after the change. Its independent test checks that no paid call comes before the alert.

### 4. A rule's table search called SAM too often

**What we saw.** One rule's search for tables made about 51 SAM calls a minute. We found it and stopped it.

**How the code works now.** `ctx.places` asks SAM once for each camera and word. The rule's folder keeps the boxes, so later calls are free. A search that found nothing waits 600 s before SAM looks again ([server.py:1757](../server.py#L1757), `PLACE_RETRY_S`). The [cafe example](../examples/cafe-hand-raise) calls `ctx.places` on every staff look, and the folder answers them.

## What this does not show

- **One site, one owner, a short time.** The Azure server has watched the real cameras since 4 November 2026. There is no customer, and no long-term record here.
- **Detection accuracy.** The evaluation in [eval/README.md](../eval/README.md) measures it.
- **Cost at another site.** A site with more movement, more cameras or more rules pays for more checks.
