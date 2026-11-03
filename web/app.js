const FRAME_WIDTH = 640;     // small frames can hide what a person holds
const SHARP_WIDTH = 1280;    // a larger copy for rule code and saved pictures
const SMALL_W = 64;
const SMALL_H = 36;
const TICK_MS = 80;
const CELL = 8;              // compare blocks, so a small change in one place still counts
const CHANGE_LEVEL = 5;      // mean grey difference (0-255) in a block that counts as a change
const SETTLE_MS = 2500;      // keep checking this long after the last change
const IDLE_CHECK_MS = 10000; // check a still picture at this slow rate
const MAX_IN_FLIGHT = 4;
const HISTORY_LENGTH = 24;
const POLL_MS = 400;
const RECORD_IDLE_MS = 1500;
const RECORD_EVENT_MS = 6000;
const RECORD_GAP_MS = 100;
const FREEZE_MS = 4500;      // how long a drawn event stays on the view
const POINT_MS = 2600;       // how long a pointed object stays outlined
const OUTLINE_WAIT_MS = 12000; // the longest a freeze waits for its outline
const SHUTTER_MS = 140;
const OUTLINE_COLOR = "#3fbf9b";

const useClip = new URLSearchParams(location.search).has("clip");
const pageId = crypto.randomUUID();
const $ = (id) => document.getElementById(id);

const video = $("video");
const overlay = $("overlay");
const overlayCtx = overlay.getContext("2d");
const ink = $("ink");
const small = document.createElement("canvas");
small.width = SMALL_W;
small.height = SMALL_H;
const smallCtx = small.getContext("2d", { willReadFrequently: true });
const full = document.createElement("canvas");
const fullCtx = full.getContext("2d");
const sharp = document.createElement("canvas");
const sharpCtx = sharp.getContext("2d");

const liveCanvas = document.createElement("canvas");
let liveCamera = false;      // a security camera feeds the view, in place of this computer's camera
let aiming = null;           // the moving lens that the next click in the view aims
let controlsKey = "";
let liveLost = false;
let liveSeq = 0;
let previous = null;
let lastChange = 0;
let peakMotion = 0;
let lastSent = 0;
let inFlight = 0;
let sent = 0;
let skipped = 0;
let stopped = false;
let inactive = false;
let recording = false;
let flashTimer = null;
let rules = [];
let lastFeed = 0;
let lastResult = 0;
let captureFps = 3;
let rulesKey = "";
let example = null;          // { frames: [base64], idle: [base64] }
let pointed = null;          // the object a person clicked: { id, noun }
let editing = null;          // the live rule that the form will replace
let freezeTimer = null;
let frozenDraw = null;       // the feed id of the draw on the view
let firstState = true;
let holdCapture = false;     // a rule waits for later frames
let voice = null;            // { id, chain, node } while the talk key is held
let talkHeld = false;
let micStream = null;
let audioContext = null;
const history = new Map();   // "rule/sensor" -> what the model said recently

function setStatus(text, kind) {
  const pill = $("status");
  pill.textContent = text;
  pill.className = "pill" + (kind ? " " + kind : "");
}

function showNotice(html) {
  const notice = $("notice");
  notice.innerHTML = html;
  notice.hidden = !html;
}

// A click on a picture opens the frame it came from, so a person can judge it in context.
function openFrame(url, box, title) {
  $("viewer-title").textContent = title;
  $("viewer-image").src = url;
  const mark = $("viewer-box");
  mark.hidden = !box;
  if (box) {
    mark.style.left = `${(box.x1 / box.w) * 100}%`;
    mark.style.top = `${(box.y1 / box.h) * 100}%`;
    mark.style.width = `${((box.x2 - box.x1) / box.w) * 100}%`;
    mark.style.height = `${((box.y2 - box.y1) / box.h) * 100}%`;
  }
  $("viewer").showModal();
}

function pictureTitle(picture) {
  const when = picture.t ? new Date(picture.t * 1000).toLocaleTimeString() : "";
  return [picture.camera, when].filter(Boolean).join(", ") || "Full frame";
}

function galleryRow(pictures) {
  const row = element("div", "gallery");
  for (const picture of pictures) {
    const open = document.createElement("button");
    open.type = "button";
    open.title = pictureTitle(picture);
    open.onclick = () => openFrame(picture.frame, picture.box, pictureTitle(picture));
    const image = document.createElement("img");
    image.src = picture.zoom;
    image.alt = open.title;
    open.append(image);
    row.append(open);
  }
  return row;
}

function log(text, check, picture, pictures) {
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString();
  item.append(time, text);
  if (picture) {
    const image = document.createElement("img");
    image.src = picture;
    image.alt = text;
    image.className = "log-picture";
    image.onclick = () => openFrame(picture, null, text);
    item.append(image);
  }
  if (pictures?.length) item.append(galleryRow(pictures));
  if (check) {
    const why = element("button", "quiet small", "Why?");
    why.type = "button";
    why.onclick = () => showEvidence(check);
    item.append(why);
  }
  $("log").prepend(item);
}

function proofFacts(check) {
  const answers = check.answers || [];
  // A review joins clues across frames, so one frame's facts do not explain its yes.
  if (check.review?.passed) {
    return [
      ...check.review.clues_for.map((text) => ({ pass: true, text })),
      ...check.review.clues_against.map((text) => ({ pass: false, text })),
    ];
  }
  // A SAM check has no facts. It only finds its target.
  const facts = !answers.length ? [] : (check.requires || []).map((fact, index) => ({
    pass: answers.every((a) => a.facts?.[index]),
    text: fact,
  }));
  if (check.pattern === "moment") {
    const peak = Math.round(check.peak_motion || 0);
    facts.push({ pass: peak >= check.min_motion, text: `Movement ${peak}, needed ${Math.round(check.min_motion)}` });
  }
  if (!answers.length) {
    const count = (check.boxes || []).length;
    const sure = check.conf == null ? "" : `, confidence ${check.conf.toFixed(2)}`;
    facts.push({ pass: count > 0, text: `SAM found ${count} ${check.target}${sure}` });
  }
  return facts;
}

async function showEvidence(id) {
  const check = await (await fetch("/api/check/" + id)).json();
  const body = $("evidence-body");
  body.replaceChildren();
  const framePath = (name) => `/evidence/${check.session}/${name}`;

  const proof = element("div", "proof");
  const image = document.createElement("img");
  image.src = framePath(check.proof_frame || check.frames?.[0]);
  image.alt = "The frame that proved this event";
  const proofInk = document.createElement("canvas");
  const facts = element("ul", "proof-facts");
  for (const fact of proofFacts(check)) {
    facts.append(element("li", fact.pass ? "pass" : "fail", fact.text));
  }
  proof.append(image, proofInk, facts);
  body.append(proof);

  const strip = element("div", "evidence-frames");
  (check.frames || []).forEach((name, index) => {
    const thumb = document.createElement("img");
    thumb.src = framePath(name);
    thumb.alt = `Frame ${index + 1}`;
    if (name === check.proof_frame) thumb.className = "best";
    strip.append(thumb);
  });
  body.append(strip);

  body.append(element("p", "sentence", check.question));
  (check.answers || []).forEach((a) => body.append(element("p", "sentence", `Spark saw: ${a.note}`)));

  if (check.review) {
    if (check.who) body.append(element("p", "sentence", `Who: ${check.who}`));
    const verdict = check.review.passed ? "yes" : "no";
    body.append(element("h2", null,
      `Spark watched the last ${check.review.span_s} s: ${verdict}, ${check.review.confidence.toFixed(2)} sure`));
    const lines = element("ol", "steps-done");
    check.review.frames.forEach((line) => lines.append(element("li", null, line)));
    body.append(lines);
  }

  if (check.steps?.length) {
    body.append(element("h2", null, "What the rule did next"));
    const steps = element("ol", "steps-done");
    for (const step of check.steps) {
      const row = element("li");
      if (step.pictures) {
        row.append(step.caption, galleryRow(step.pictures));
      } else if (step.image) {
        const picture = document.createElement("img");
        picture.src = step.image;
        picture.alt = step.caption;
        row.append(picture, step.caption);
      } else {
        const sure = step.confidence == null ? "" : ` (${step.confidence.toFixed(2)} sure)`;
        row.append(element("b", null, step.model), `: ${step.question} `, element("i", null, `${step.answer}${sure}`));
      }
      steps.append(row);
    }
    body.append(steps);
  }

  const wrong = element("button", "quiet", "This was wrong");
  wrong.type = "button";
  wrong.onclick = async () => {
    await fetch("/api/label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ check: id, label: "wrong" }),
    });
    wrong.textContent = "Saved as wrong";
    wrong.disabled = true;
  };
  body.append(wrong);
  $("evidence").showModal();

  const outline = check.outline || (await (await fetch(`/api/check/${id}/outline`, { method: "POST" })).json()).outline;
  if (outline) {
    await image.decode();
    paintOutline(proofInk, outline, { left: 0, top: 0, width: proofInk.clientWidth, height: proofInk.clientHeight });
  }
}

// Where the video really sits inside the frame, because it uses object-fit: contain.
function videoArea() {
  const width = overlay.clientWidth;
  const height = overlay.clientHeight;
  const scale = Math.min(width / video.videoWidth, height / video.videoHeight);
  const drawnW = video.videoWidth * scale;
  const drawnH = video.videoHeight * scale;
  return { left: (width - drawnW) / 2, top: (height - drawnH) / 2, width: drawnW, height: drawnH };
}

function clearInk() {
  ink.width = ink.clientWidth;
  ink.height = ink.clientHeight;
}

// outline.path is in the mask's own pixels, and the mask covers the object's box.
function paintOutline(canvas, outline, area) {
  canvas.width = canvas.clientWidth * devicePixelRatio;
  canvas.height = canvas.clientHeight * devicePixelRatio;
  const context = canvas.getContext("2d");
  context.scale(devicePixelRatio, devicePixelRatio);
  const left = area.left + (outline.x1 / outline.w) * area.width;
  const top = area.top + (outline.y1 / outline.h) * area.height;
  const width = ((outline.x2 - outline.x1) / outline.w) * area.width;
  const height = ((outline.y2 - outline.y1) / outline.h) * area.height;
  context.strokeStyle = OUTLINE_COLOR;
  context.lineJoin = "round";
  context.shadowColor = OUTLINE_COLOR;
  context.shadowBlur = 12;
  if (!outline.path) {
    context.lineWidth = 3;
    context.strokeRect(left, top, width, height);
    return;
  }
  const scaleX = width / outline.mask_w;
  const scaleY = height / outline.mask_h;
  context.translate(left, top);
  context.scale(scaleX, scaleY);
  const shape = new Path2D(outline.path);
  context.fillStyle = "rgba(63, 191, 155, 0.26)";
  context.fill(shape);
  context.lineWidth = 3 / Math.min(scaleX, scaleY);
  context.stroke(shape);
}

// The label stays readable, so it sits outside the mirrored layers.
function placeTag(text, outline) {
  const tag = $("tag");
  tag.textContent = text;
  tag.hidden = !text;
  if (!text) return;
  const area = videoArea();
  let x = 0.5;
  let y = 0.9;
  if (outline) {
    x = (outline.x1 + outline.x2) / 2 / outline.w;
    y = Math.max(outline.y1 / outline.h - 0.02, 0.12);
  }
  if ($("frame").classList.contains("mirror")) x = 1 - x;
  // Keep the whole label inside the picture.
  const half = tag.offsetWidth / 2;
  const centreX = Math.min(Math.max(area.left + x * area.width, area.left + half), area.left + area.width - half);
  tag.style.left = `${centreX}px`;
  tag.style.top = `${area.top + y * area.height}px`;
}

function endFreeze() {
  frozenDraw = null;
  $("freeze").hidden = true;
  $("replay").hidden = true;
  $("tag").hidden = true;
  clearInk();
}

function holdFreeze(ms) {
  clearTimeout(freezeTimer);
  freezeTimer = setTimeout(endFreeze, ms);
}

function showDraw(item) {
  if (!item.frame) return flash(`${item.rule}: ${item.text}`);
  const freeze = $("freeze");
  frozenDraw = item.id;
  freeze.src = item.frame;
  freeze.hidden = false;
  // A short flash, like a camera shutter. A timer cannot stall when the tab is in the background.
  freeze.classList.add("shutter");
  setTimeout(() => freeze.classList.remove("shutter"), SHUTTER_MS);
  $("replay").hidden = false;
  clearInk();
  placeTag(item.text, null);
  // Finding the object can take several seconds, so the freeze waits for the answer.
  holdFreeze(OUTLINE_WAIT_MS);
}

function showOutline(item) {
  if (item.draw !== frozenDraw) return;
  if (item.outline) {
    paintOutline(ink, item.outline, videoArea());
    placeTag($("tag").textContent, item.outline);
  }
  holdFreeze(FREEZE_MS);
}

// Turns a click into a pixel on the raw frame. The view may be mirrored.
function clickToFrame(event) {
  const rect = $("frame").getBoundingClientRect();
  const area = videoArea();
  let x = (event.clientX - rect.left - area.left) / area.width;
  const y = (event.clientY - rect.top - area.top) / area.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  if ($("frame").classList.contains("mirror")) x = 1 - x;
  return { x, y };
}

function setPointed(value) {
  pointed = value;
  $("pointed-box").hidden = !value;
  $("pointed-name").textContent = value?.noun || "";
}

async function cameraCommand(command) {
  const response = await fetch("/api/camera", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  if (!response.ok) log((await response.json()).error || "The camera did not answer.");
}

function setAiming(name) {
  aiming = name;
  $("frame").classList.toggle("aiming", !!name);
  controlsKey = "";
}

function controlButton(text, label, command) {
  const button = element("button", "quiet", text);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", () => cameraCommand(command()));
  return button;
}

function cameraControls(camera) {
  const row = element("div", "control");
  row.append(element("span", null, camera.name));
  if (camera.moves) {
    for (const [direction, arrow] of [["left", "←"], ["up", "↑"], ["down", "↓"], ["right", "→"]]) {
      row.append(controlButton(arrow, `Move ${camera.name} ${direction}`,
        () => ({ do: "move", camera: camera.name, direction })));
    }
    const aim = element("button", aiming === camera.name ? "quiet on" : "quiet", "Click to aim");
    aim.type = "button";
    aim.title = "Then click a place in the fixed lens's picture";
    aim.addEventListener("click", () => setAiming(aiming === camera.name ? null : camera.name));
    row.append(aim);
  }
  if (camera.light) {
    const on = camera.light_on;
    const light = controlButton(on ? "Light off" : "Light on", `Turn ${camera.name} light ${on ? "off" : "on"}`,
      () => ({ do: "light", camera: camera.name, on: !on }));
    light.classList.toggle("on", on);
    row.append(light);
  }
  return row;
}

function renderControls(controls = []) {
  const key = JSON.stringify(controls);
  if (key === controlsKey) return;
  controlsKey = key;
  $("controls").hidden = !controls.length;
  $("controls").replaceChildren(...controls.map(cameraControls));
}

async function pointAt(event) {
  if (!video.videoWidth || recording || event.target.closest("a")) return;
  const spot = clickToFrame(event);
  if (!spot) return;
  if (aiming) {
    const camera = aiming;
    setAiming(null);
    return cameraCommand({ do: "aim", camera, x: spot.x, y: spot.y });
  }
  drawFrame();
  const data = full.toDataURL("image/jpeg", 0.8);
  const freeze = $("freeze");
  frozenDraw = null;
  freeze.src = data;
  freeze.hidden = false;
  clearInk();
  placeTag("Looking", null);
  holdFreeze(FREEZE_MS * 2);
  try {
    const response = await fetch("/api/point", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        frame: data.split(",")[1],
        x: Math.round(spot.x * (full.width - 1)),
        y: Math.round(spot.y * (full.height - 1)),
        w: full.width,
        h: full.height,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Pointing failed.");
    if (!result.found) {
      placeTag("Nothing there. Try again.", null);
      holdFreeze(POINT_MS);
      return;
    }
    paintOutline(ink, result.box, videoArea());
    placeTag("Naming it", result.box);
    const named = await (await fetch(`/api/point/${result.id}/name`, { method: "POST" })).json();
    placeTag(named.noun, result.box);
    setPointed({ id: result.id, noun: named.noun });
    holdFreeze(POINT_MS);
  } catch (error) {
    endFreeze();
    log(error.message);
  }
}

function showTalking(live) {
  const button = $("talk");
  button.classList.toggle("live", live);
  button.textContent = live ? "Listening" : "Hold to talk";
}

async function openMic() {
  if (!micStream) {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  }
  if (!audioContext) {
    audioContext = new AudioContext({ sampleRate: 24000 });
    await audioContext.audioWorklet.addModule("/mic.js");
  }
  await audioContext.resume();
}

async function openVoice() {
  const response = await fetch("/api/voice", { method: "POST" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Voice failed.");
  return data.id;
}

async function sendAudio(session, chunk) {
  session.id ??= await session.opened;
  const response = await fetch(`/api/voice/${session.id}/audio`, { method: "POST", body: chunk });
  const data = await response.json();
  if (data.text) $("sentence").value = data.text;
}

async function startTalk() {
  if (talkHeld) return;
  talkHeld = true;
  showTalking(true);
  const note = $("form-note");
  try {
    await openMic();
    // The connection takes over a second. Audio waits in the chain, so the first words stay.
    const session = { chain: openVoice(), node: new AudioWorkletNode(audioContext, "mic") };
    session.opened = session.chain;
    session.node.port.onmessage = ({ data: chunk }) => {
      session.chain = session.chain.then(() => sendAudio(session, chunk));
    };
    session.source = audioContext.createMediaStreamSource(micStream);
    session.source.connect(session.node);
    voice = session;
    // The key may already be up, because a first start can take a second.
    if (!talkHeld) await stopTalk();
    await session.opened;
  } catch (error) {
    talkHeld = false;
    showTalking(false);
    note.className = "note error";
    note.textContent = `The microphone did not start: ${error.message}`;
  }
}

async function stopTalk() {
  talkHeld = false;
  showTalking(false);
  const session = voice;
  voice = null;
  if (!session) return;
  session.source.disconnect();
  session.node.port.onmessage = null;
  try {
    await session.chain;
    const id = await session.opened;
    const data = await (await fetch(`/api/voice/${id}/end`, { method: "POST" })).json();
    if (data.text) $("sentence").value = data.text;
  } catch (error) {
    $("form-note").className = "note error";
    $("form-note").textContent = `Voice failed: ${error.message}`;
  }
}

function flash(text) {
  const el = $("flash");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.hidden = true; }, 3000);
}

function seconds(value) {
  return value == null ? "–" : value.toFixed(2) + " s";
}

function drawBoxes(results) {
  overlay.width = overlay.clientWidth;
  overlay.height = overlay.clientHeight;
  overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
  const boxes = results.flatMap((r) => r.boxes || []);
  if (!boxes.length || !video.videoWidth) return;

  const area = videoArea();
  overlayCtx.lineWidth = 3;
  overlayCtx.strokeStyle = OUTLINE_COLOR;
  for (const b of boxes) {
    overlayCtx.strokeRect(
      area.left + (b.x1 / b.w) * area.width,
      area.top + (b.y1 / b.h) * area.height,
      ((b.x2 - b.x1) / b.w) * area.width,
      ((b.y2 - b.y1) / b.h) * area.height,
    );
  }
}

function record(r) {
  const key = `${r.rule}/${r.sensor}`;
  const h = history.get(key) || { hits: [], text: "" };
  h.hits = [...h.hits, r.hit].slice(-HISTORY_LENGTH);
  if (r.spark_s != null) {
    const unit = r.spark_frames === 1 ? "frame" : "frames";
    h.text = `Spark ${r.answer ? "yes" : "no"}, ${r.spark_frames} of ${r.frames_covered} ${unit}, ${seconds(r.spark_s)}`;
  } else if (r.sam_s != null) {
    const conf = r.conf != null ? ` ${r.conf.toFixed(2)}` : "";
    h.text = `SAM ${r.hit ? "found" : "nothing"}${conf}, ${seconds(r.sam_s)}`;
  }
  history.set(key, h);
}

function renderSensor(ruleId, sensorId) {
  const box = $(`sensor-${ruleId}-${sensorId}`);
  const h = history.get(`${ruleId}/${sensorId}`);
  if (!box || !h) return;
  const dots = box.querySelector(".dots");
  dots.replaceChildren(...h.hits.map((hit) => {
    const dot = document.createElement("i");
    if (hit) dot.className = "hit";
    return dot;
  }));
  box.querySelector(".model-note").textContent = h.text;
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function startEdit(rule) {
  editing = rule;
  $("sentence").value = rule.sentence;
  $("sentence-label").textContent = `Edit: ${rule.name}`;
  $("add").textContent = "Update rule";
  $("cancel-edit").hidden = false;
  $("form-note").className = "note";
  $("form-note").textContent = "Change the words, then update. The old rule keeps running until the new one is ready.";
  $("sentence").rows = 5;
  $("sentence").focus();
}

function endEdit() {
  editing = null;
  $("sentence-label").textContent = "When\u2026";
  $("add").textContent = "Add rule";
  $("cancel-edit").hidden = true;
  $("sentence").rows = 2;
}

function renderRules() {
  const list = $("rules");
  list.replaceChildren();
  if (!rules.length) {
    list.append(element("li", "empty", "No rules yet."));
    return;
  }
  for (const rule of rules) {
    const item = element("li", "rule");
    item.id = "rule-" + rule.id;

    if (rule.paused) item.classList.add("paused");

    const head = element("div", "rule-head");
    const toggle = element("button", "quiet small", rule.paused ? "Resume" : "Stop");
    toggle.type = "button";
    toggle.onclick = async () => {
      await fetch(`/api/rule/${rule.id}/${rule.paused ? "resume" : "stop"}`, { method: "POST" });
      refreshState();
    };
    const edit = element("button", "quiet small", "Edit");
    edit.type = "button";
    edit.onclick = () => startEdit(rule);
    const remove = element("button", "quiet small", "Remove");
    remove.type = "button";
    remove.onclick = async () => {
      await fetch("/api/rule/" + rule.id, { method: "DELETE" });
      refreshState();
    };
    head.append(element("span", null, rule.paused ? `${rule.name} (stopped)` : rule.name), toggle, edit, remove);
    item.append(head, element("p", "sentence", rule.sentence));
    if (rule.pointed) item.append(element("p", "timing", `Pointed at: ${rule.pointed}`));

    for (const sensor of rule.sensors) {
      const timing = element("p", "timing");
      timing.textContent = `${sensor.pattern}, ${sensor.frames_per_second} frames/s, check every ${sensor.check_every_s} s, ` +
        `${sensor.frames_per_check} frames, clear after ${sensor.clear_after_s} s` +
        `${sensor.starts_off ? ", wakes when the rule needs it" : ""}` +
        `${sensor.pattern === "moment" ? `, movement over ${sensor.min_motion}` : ""}. ${sensor.reason}`;
      item.append(timing);
    }

    const blocks = element("ol", "blocks");
    for (const step of rule.plan) blocks.append(element("li", null, step));
    item.append(blocks);

    const values = element("div", "values");
    values.id = "values-" + rule.id;
    item.append(values);

    for (const sensor of rule.sensors) {
      const row = element("div", "model");
      row.id = `sensor-${rule.id}-${sensor.id}`;
      row.title = sensor.target ? `SAM finds "${sensor.target}"` : sensor.question;
      row.append(
        element("span", "model-name", sensor.id),
        element("span", "dots"),
        element("span", "model-note", sensor.target ? "SAM" : "Spark"),
      );
      item.append(row);
    }

    const details = element("details", "code");
    details.append(element("summary", null, "Show the code"));
    const code = element("pre");
    details.append(code);
    details.ontoggle = async () => {
      if (details.open && !code.textContent) {
        code.textContent = await (await fetch(`/api/rule/${rule.id}/code`)).text();
      }
    };
    item.append(details);

    list.append(item);
    rule.sensors.forEach((sensor) => renderSensor(rule.id, sensor.id));
  }
}

function renderValues() {
  for (const rule of rules) {
    const box = $("values-" + rule.id);
    if (!box) continue;
    box.replaceChildren(...Object.entries(rule.values).map(([name, value]) => {
      const tile = element("div", "value");
      tile.append(element("b", null, value), element("span", null, name));
      return tile;
    }));
  }
}

async function refreshState() {
  const state = await (await fetch(`/api/state?after=${lastFeed}&results=${lastResult}`)).json();
  rules = state.rules;
  stopped = state.stopped;
  captureFps = state.capture_fps;
  holdCapture = state.hold_capture;
  if (state.live_camera && !liveCamera) showLiveCamera();
  if (liveCamera) {
    liveLost = !state.live_camera;
    setStatus(liveLost ? "The camera stream stopped" : liveLabel(state), liveLost ? "warn" : "live");
  }
  $("s-spend").textContent = "$" + state.spend_usd.toFixed(3);
  renderControls(state.controls);
  const key = rules.map((r) => `${r.id}:${r.paused}`).join(",");
  if (key !== rulesKey) {
    rulesKey = key;
    renderRules();
  }
  renderValues();
  handleResults(state.results);
  for (const item of state.feed) {
    lastFeed = item.id;
    if (item.kind === "outline") {
      showOutline(item);
    } else if (item.kind === "draw") {
      // After a reload, old draws stay in the list and do not freeze the view.
      if (!firstState) showDraw(item);
      log(`${item.rule}: ${item.text}`, item.check);
    } else if (item.kind === "image") {
      log(`${item.rule}: ${item.text}`, item.check, item.url);
    } else if (item.kind === "gallery") {
      log(`${item.rule}: ${item.text}`, item.check, null, item.pictures);
    } else if (item.kind === "notify" || item.kind === "say") {
      flash(`${item.rule}: ${item.text}`);
      log(`${item.rule}: ${item.text}`);
    } else if (item.kind === "error") {
      log(`${item.rule} error: ${item.text}`);
    } else {
      log(item.text);
    }
  }
  if (stopped) setStatus("Stopped at the spend limit", "warn");
  firstState = false;
}

function handlePause(data) {
  if (data.inactive) {
    inactive = true;
    setStatus("Another tab took over", "warn");
    showNotice("This camera is paused because a newer tab took over. Reload this page to use it here.");
  }
  if (data.stopped) {
    stopped = true;
    setStatus("Stopped at the spend limit", "warn");
  }
}

function handleResults(results) {
  if (!results.length) return;
  lastResult = results[results.length - 1].id;
  const seen = results.filter((r) => !r.skipped);
  if (seen.length) drawBoxes(seen);
  for (const r of results) {
    if (r.error) log(r.error);
    if (r.skipped) continue;
    record(r);
    renderSensor(r.rule, r.sensor);
    if (r.sam_s != null) $("s-sam").textContent = seconds(r.sam_s);
    if (r.spark_s != null) $("s-spark").textContent = seconds(r.spark_s);
    if (r.event) log(`${r.sensor} ${r.event}`, r.event === "off" ? null : r.check);
  }
}

function changeLevel() {
  smallCtx.drawImage(video, 0, 0, SMALL_W, SMALL_H);
  const pixels = smallCtx.getImageData(0, 0, SMALL_W, SMALL_H).data;
  const grey = new Uint8Array(SMALL_W * SMALL_H);
  for (let i = 0; i < grey.length; i++) {
    grey[i] = (pixels[i * 4] + pixels[i * 4 + 1] + pixels[i * 4 + 2]) / 3;
  }
  let highest = 0;
  if (previous) {
    for (let top = 0; top + CELL <= SMALL_H; top += CELL) {
      for (let left = 0; left + CELL <= SMALL_W; left += CELL) {
        let total = 0;
        for (let y = top; y < top + CELL; y++) {
          for (let x = left; x < left + CELL; x++) {
            const i = y * SMALL_W + x;
            total += Math.abs(grey[i] - previous[i]);
          }
        }
        highest = Math.max(highest, total / (CELL * CELL));
      }
    }
  }
  previous = grey;
  return highest;
}

function drawFrame() {
  full.width = FRAME_WIDTH;
  full.height = Math.round((FRAME_WIDTH * video.videoHeight) / video.videoWidth);
  fullCtx.drawImage(video, 0, 0, full.width, full.height);
}

// Sensors use the small frame, because each check is paid for. Rule code uses this copy.
function captureSharp() {
  if (video.videoWidth <= FRAME_WIDTH) return null;
  sharp.width = Math.min(SHARP_WIDTH, video.videoWidth);
  sharp.height = Math.round((sharp.width * video.videoHeight) / video.videoWidth);
  sharpCtx.drawImage(video, 0, 0, sharp.width, sharp.height);
  return new Promise((resolve) => sharp.toBlob(resolve, "image/jpeg", 0.85));
}

async function sendSharp(name, copy) {
  const blob = await copy;
  if (!blob || !name) return;
  await fetch(`/api/frame/${name}/sharp`, {
    method: "POST",
    headers: { "Content-Type": "image/jpeg", "X-Page": pageId },
    body: blob,
  });
}

function sendFrame(motion) {
  drawFrame();
  const copy = captureSharp();
  const capturedAt = Date.now() / 1000;
  inFlight++;
  lastSent = performance.now();
  $("s-sent").textContent = ++sent;
  full.toBlob(async (blob) => {
    try {
      const response = await fetch("/api/frame", {
        method: "POST",
        headers: { "Content-Type": "image/jpeg", "X-Frame-Time": String(capturedAt), "X-Page": pageId, "X-Motion": motion.toFixed(1) },
        body: blob,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Check failed.");
      handlePause(data);
      if (copy) await sendSharp(data.name, copy);
      $("s-latency").textContent = seconds(Date.now() / 1000 - capturedAt);
    } catch (error) {
      log(error.message);
    } finally {
      inFlight--;
    }
  }, "image/jpeg", 0.7);
}

function tick() {
  // The reader sends a security camera's frames itself, so this page only shows them.
  if (liveCamera) return;
  if (!video.videoWidth || document.hidden || stopped || inactive || recording) return;
  const now = performance.now();
  const motion = changeLevel();
  peakMotion = Math.max(peakMotion, motion);
  if (motion > CHANGE_LEVEL) lastChange = now;
  if (!rules.length) return;

  const changing = holdCapture || now - lastChange < SETTLE_MS;
  const due = changing ? 1000 / captureFps : IDLE_CHECK_MS;
  if (now - lastSent >= due && inFlight < MAX_IN_FLIGHT) {
    sendFrame(peakMotion);
    peakMotion = 0;
  } else {
    $("s-skipped").textContent = ++skipped;
  }
  setStatus(changing ? "Checking" : "Watching for a change", "live");
}

function grab() {
  drawFrame();
  return full.toDataURL("image/jpeg", 0.7).split(",")[1];
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function capture(ms) {
  const frames = [];
  const motions = [];
  const started = performance.now();
  while (performance.now() - started < ms) {
    motions.push(changeLevel());
    frames.push(grab());
    await wait(RECORD_GAP_MS);
  }
  return { frames, motions, gapS: (performance.now() - started) / frames.length / 1000 };
}

async function recordExample() {
  if (!video.videoWidth || recording) return;
  const cue = $("cue");
  recording = true;
  $("record").disabled = true;
  cue.hidden = false;
  cue.textContent = "Stay still";
  const idle = await capture(RECORD_IDLE_MS);
  cue.textContent = "Do it now";
  const event = await capture(RECORD_EVENT_MS);
  cue.hidden = true;
  recording = false;
  $("record").disabled = false;
  example = { frames: event.frames, motions: event.motions, gapS: event.gapS, idle: idle.frames };
  const thumbs = $("thumbs");
  const every = Math.max(1, Math.floor(event.frames.length / 5));
  thumbs.replaceChildren(...event.frames.filter((_, i) => i % every === 0).map((data) => {
    const image = document.createElement("img");
    image.src = "data:image/jpeg;base64," + data;
    image.alt = "";
    return image;
  }));
  $("example-box").hidden = false;
  $("form-note").className = "note";
  $("form-note").textContent = `Recorded ${event.frames.length} frames. Add a sentence, or add the rule as it is.`;
}

async function followJob(id) {
  const steps = $("steps");
  while (true) {
    const job = await (await fetch("/api/job/" + id)).json();
    steps.replaceChildren(...job.steps.map((text) => element("li", null, text)));
    if (job.status !== "working") return job;
    await wait(POLL_MS);
  }
}

$("record").addEventListener("click", recordExample);
$("frame").addEventListener("click", pointAt);
$("clear-pointed").addEventListener("click", () => setPointed(null));
$("cancel-edit").addEventListener("click", () => {
  endEdit();
  $("sentence").value = "";
  $("form-note").textContent = "";
});
$("talk").addEventListener("pointerdown", startTalk);
$("talk").addEventListener("pointerup", stopTalk);
$("talk").addEventListener("pointerleave", () => { if (talkHeld) stopTalk(); });

const typing = (target) => target.closest("textarea, input, button, dialog");
window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || typing(event.target)) return;
  event.preventDefault();
  startTalk();
});
window.addEventListener("keyup", (event) => {
  if (event.code === "Space" && talkHeld) stopTalk();
});
$("evidence-close").addEventListener("click", () => $("evidence").close());
$("viewer-close").addEventListener("click", () => $("viewer").close());

$("clear-example").addEventListener("click", () => {
  example = null;
  $("example-box").hidden = true;
});

$("rule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const sentence = $("sentence").value.trim();
  const note = $("form-note");
  if (!sentence && !example) {
    note.className = "note error";
    note.textContent = "Write a sentence or record an example.";
    return;
  }
  $("add").disabled = true;
  note.className = "note";
  note.textContent = editing
    ? "Wonder is changing the rule. The old rule keeps running until then."
    : "Wonder is building the rule. This can take a few minutes.";
  try {
    const response = await fetch(editing ? `/api/rule/${editing.id}/edit` : "/api/rule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sentence,
        example: example?.frames,
        idle: example?.idle,
        motions: example?.motions,
        gap_s: example?.gapS,
        point: pointed?.id,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The rule failed.");
    const job = await followJob(data.job);
    if (job.status === "failed") throw new Error(job.error);
    note.textContent = `${editing ? "Rule updated" : "Rule ready"}. Cost about $${job.cost}.`;
    endEdit();
    $("sentence").value = "";
    $("steps").replaceChildren();
    example = null;
    $("example-box").hidden = true;
    setPointed(null);
    refreshState();
  } catch (error) {
    note.className = "note error";
    note.textContent = error.message;
  } finally {
    $("add").disabled = false;
  }
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("sentence").value = chip.textContent;
    $("sentence").focus();
  });
});

function liveLabel(state) {
  const count = state.cameras?.length || 1;
  return count > 1 ? `Live: ${count} cameras` : "Live: security camera";
}

// The canvas stream fills the same video element, so pointing, recording and outlines still work.
async function showLiveCamera() {
  liveCamera = true;
  inactive = false;
  $("notice").hidden = true;
  $("frame").classList.remove("mirror");
  if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
  setStatus("Live: security camera", "live");
  const context = liveCanvas.getContext("2d");
  let started = false;
  for (;;) {
    try {
      const response = await fetch(`/api/live.jpg?after=${liveSeq}`);
      if (response.status !== 200) continue;
      liveSeq = Number(response.headers.get("X-Seq"));
      const picture = await createImageBitmap(await response.blob());
      if (liveCanvas.width !== picture.width) {
        liveCanvas.width = picture.width;
        liveCanvas.height = picture.height;
      }
      context.drawImage(picture, 0, 0);
      picture.close();
      if (!started) {
        started = true;
        video.srcObject = liveCanvas.captureStream();
      }
    } catch (error) {
      await wait(1000);
    }
  }
}

async function startVideo() {
  if (useClip) {
    video.src = "/testdata/clip.webm";
    return;
  }
  const state = await (await fetch("/api/state?after=999999&results=999999")).json();
  if (state.live_camera) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    // A security camera can start while the browser asks for this one.
    if (liveCamera) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    video.srcObject = stream;
    $("frame").classList.add("mirror");
  } catch (error) {
    setStatus("No camera", "warn");
    showNotice(
      'The page cannot use the camera. Allow camera access and reload, ' +
      'or <a href="/?clip=1">run the test clip</a>.',
    );
  }
}

fetch("/api/claim", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ page: pageId }),
});
renderRules();
refreshState();
startVideo();
setInterval(tick, TICK_MS);
setInterval(refreshState, POLL_MS);
