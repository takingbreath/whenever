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
const JOURNAL_MS = 4000;     // the Activity screen reads the journal this often
const JOURNAL_IDLE_MS = 15000;
const RECORD_IDLE_MS = 1500;
const RECORD_EVENT_MS = 6000;
const RECORD_GAP_MS = 100;
const FREEZE_MS = 4500;      // how long a drawn event stays on the view
const POINT_MS = 2600;       // how long a pointed object stays outlined
const OUTLINE_WAIT_MS = 12000; // the longest a freeze waits for its outline
const SHUTTER_MS = 140;
const FIRED_TAB_S = 15 * 60; // a card shows its red tab this long after it fired
const OUTLINE_COLOR = "#ffffff";
const HUES = ["indigo", "jade", "peacock", "plum", "marigold"];

const params = new URLSearchParams(location.search);
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
// A security camera's pictures are drawn here, and the video element plays this canvas.
const liveCanvas = document.createElement("canvas");
let liveCamera = false;      // a security camera feeds the view, in place of this computer's camera
let aiming = null;           // the moving lens that the next click in the view aims
let controlsKey = "";
let actionsKey = "";
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
let state = null;            // the newest /api/state
let rules = [];
let lastFeed = 0;
let lastResult = 0;
let captureFps = 3;
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
let source = null;           // "device" or "clip", once the person chose
let screen = "";
let journal = [];
let journalAt = 0;
let filter = "all";
let feedKind = "all";
let createStep = "compose";
let focusId = null;          // the Whenever that Live shows on its own
let pendingSentence = "";
const thenPicked = new Set();
const camerasPicked = new Set();
const history = new Map();   // "rule/sensor" -> what the model said recently
const keys = {};             // the last render key of each list, so a poll repaints only on change

/* ---------- Icons: drawn paths, used as masks so they take the text colour ---------- */

const ICONS = {
  live: '<path d="M15 10l4.55-2.28A1 1 0 0 1 21 8.62v6.76a1 1 0 0 1-1.45.9L15 14"/><rect x="3" y="6" width="12" height="12" rx="2"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.5"/>',
  list: '<rect x="4" y="4" width="16" height="6" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pulse: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  bolt: '<path d="M13 3L4 14h7l-1 7 9-11h-7l1-7z"/>',
  play: '<circle cx="12" cy="12" r="9"/><path d="M10 8.8l5 3.2-5 3.2z"/>',
  cctv: '<path d="M4 6l12 3.5-1.6 5.2L2.5 11.2z"/><path d="M14.4 14.7L16 18h4M20 15v6M17 10.5l3 1"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12"/><path d="M11 11.5v-2a1.5 1.5 0 0 1 3 0V12"/><path d="M14 10.5a1.5 1.5 0 0 1 3 0V12"/><path d="M17 11.5a1.5 1.5 0 0 1 3 0V16a6 6 0 0 1-6 6h-2a6 6 0 0 1-5-2.7L4.3 15.6a1.6 1.6 0 0 1 2.6-1.8L8 15"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8.5 21h7"/>',
  record: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4" fill="black"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  bell: '<path d="M10 5a2 2 0 1 1 4 0 7 7 0 0 1 4 6v3a4 4 0 0 0 2 3H4a4 4 0 0 0 2-3v-3a7 7 0 0 1 4-6"/><path d="M9 17v1a3 3 0 0 0 6 0v-1"/>',
  eye: '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  photo: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-9 9"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M14 6l4 4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 18h2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  bulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/>',
  move: '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  speaker: '<path d="M5 9h3l5-4v14l-5-4H5z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  screen: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  note: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  chart: '<path d="M4 20V11M10 20V5M16 20v-7M21 20H3"/>',
};

function iconUrl(name) {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' ` +
    `stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round'>${ICONS[name] || ""}</svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

function paintIcons(root = document) {
  root.querySelectorAll(".icon[data-icon]:not([data-painted])").forEach((el) => {
    el.style.setProperty("--icon", iconUrl(el.dataset.icon));
    el.dataset.painted = "1";
    el.setAttribute("aria-hidden", "true");
  });
}

function icon(name) {
  const el = document.createElement("span");
  el.className = "icon";
  el.dataset.icon = name;
  el.style.setProperty("--icon", iconUrl(name));
  el.dataset.painted = "1";
  el.setAttribute("aria-hidden", "true");
  return el;
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function button(text, className, onclick, iconName) {
  const el = element("button", className);
  el.type = "button";
  if (iconName) el.append(icon(iconName));
  if (text) el.append(text);
  if (onclick) el.addEventListener("click", onclick);
  return el;
}

/* ---------- Words and times ---------- */

function clock(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
}

function dayName(t) {
  const d = new Date(t * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
}

function hueOf(rule) {
  if (!rule.sensors?.length && rule.schedules?.length) return "ink";
  let n = 0;
  for (const c of rule.id) n = (n * 31 + c.charCodeAt(0)) >>> 0;
  return HUES[n % HUES.length];
}

// The sentence is the program, so the joints of the sentence stay light and the slots stay heavy.
function sentenceNode(text, className = "sentence-big") {
  const p = element("p", className);
  const parts = String(text).split(/(^\s*(?:when|whenever|if|every)\b|,?\s+then\b)/i);
  for (const part of parts) {
    if (!part) continue;
    if (/^(\s*(when|whenever|if|every)|,?\s+then)$/i.test(part)) p.append(element("span", "joint", part));
    else p.append(part);
  }
  return p;
}

function setStatus(text, kind) {
  const pill = $("status");
  pill.textContent = text;
  pill.className = "status" + (kind ? " " + kind : "");
}

function showNotice(html) {
  const notice = $("notice");
  notice.innerHTML = html;
  notice.hidden = !html;
}

function flash(text) {
  const el = $("flash");
  el.textContent = text;
  el.hidden = false;
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.hidden = true; }, 3600);
}

// Errors from the camera engine reach the person as a short message.
function log(text) {
  if (text) flash(text);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) location.reload();
  if (!response.ok) throw new Error(data.error || "The server said no.");
  return data;
}

/* ---------- Pictures and proof ---------- */

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
    image.loading = "lazy";
    open.append(image);
    row.append(open);
  }
  return row;
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
    body.append(element("h2", null, "What the Whenever did next"));
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
      } else if (step.act) {
        row.append(element("b", null, step.act), `: ${step.ok ? "done" : `failed with status ${step.status}`}`);
      } else {
        const sure = step.confidence == null ? "" : ` (${step.confidence.toFixed(2)} sure)`;
        row.append(element("b", null, step.model), `: ${step.question} `, element("i", null, `${step.answer}${sure}`));
      }
      steps.append(row);
    }
    body.append(steps);
  }

  const row = element("div", "form-row");
  row.append(labelButton(id, "right", "This was right"), labelButton(id, "wrong", "This was wrong"));
  body.append(row);
  $("evidence").showModal();

  const outline = check.outline || (await (await fetch(`/api/check/${id}/outline`, { method: "POST" })).json()).outline;
  if (outline) {
    await image.decode();
    paintOutline(proofInk, outline, { left: 0, top: 0, width: proofInk.clientWidth, height: proofInk.clientHeight });
  }
}

// A person's verdict on an event is saved beside its proof, to measure the rules later.
function labelButton(check, label, text) {
  const el = button(text, "chip", async () => {
    await postJson("/api/label", { check, label }).catch(() => {});
    el.textContent = label === "right" ? "Saved as right" : "Saved as wrong";
    el.classList.add("on");
    el.disabled = true;
  }, label === "right" ? "check" : "x");
  return el;
}

/* ---------- The camera view ---------- */

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
  context.shadowColor = "rgba(0, 0, 0, 0.55)";
  context.shadowBlur = 10;
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
  context.fillStyle = "rgba(255, 255, 255, 0.22)";
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
  if (value) {
    $("point-text").textContent = `Got it: ${value.noun}. Tap again to change it.`;
  }
}

async function cameraCommand(command) {
  try {
    await postJson("/api/camera", command);
  } catch (error) {
    flash(error.message || "The camera did not answer.");
  }
}

function setAiming(name) {
  aiming = name;
  $("frame").classList.toggle("aiming", !!name);
  controlsKey = "";
  if (name) flash(`Tap a place in the picture to turn ${name} there.`);
}

function cameraControls(camera) {
  const row = element("div", "control");
  row.append(element("span", "control-name", camera.name));
  const buttons = element("div", "control-buttons");
  row.append(buttons);
  const add = (...items) => buttons.append(...items);
  if (camera.moves) {
    for (const [direction, label] of [["left", "Left"], ["up", "Up"], ["down", "Down"], ["right", "Right"]]) {
      const b = button(label, "chip", () => cameraCommand({ do: "move", camera: camera.name, direction }));
      b.setAttribute("aria-label", `Turn ${camera.name} ${direction}`);
      add(b);
    }
    const aim = button("Tap to aim", aiming === camera.name ? "chip on" : "chip",
      () => setAiming(aiming === camera.name ? null : camera.name), "hand");
    add(aim);
  }
  if (camera.light) {
    const on = camera.light_on;
    const light = button(on ? "Light off" : "Light on", on ? "chip on" : "chip",
      () => cameraCommand({ do: "light", camera: camera.name, on: !on }), "bulb");
    add(light);
  }
  return row;
}

// The frame takes the picture's own shape, so a wide camera wall has no black bands. On a phone a
// wall of three or more columns is wider than the screen, so each camera stays big and the person pans.
const phone = matchMedia("(max-width: 600px)");

function shapeFrame() {
  if (!video.videoWidth) return;
  const frame = $("frame");
  const ratio = video.videoWidth / video.videoHeight;
  const columns = liveCamera ? (state?.wall?.[0] || 1) : 1;
  frame.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
  if (phone.matches && columns >= 3) {
    frame.style.width = `${(columns / 1.5) * 100}%`;
    frame.style.maxWidth = "none";
  } else {
    frame.style.width = "";
    frame.style.maxWidth = `calc(72vh * ${ratio.toFixed(4)})`;
  }
}

video.addEventListener("loadedmetadata", shapeFrame);
video.addEventListener("resize", shapeFrame);
phone.addEventListener("change", shapeFrame);

function renderControls(controls = []) {
  const key = JSON.stringify([controls, aiming]);
  if (key === controlsKey) return;
  controlsKey = key;
  $("controls").hidden = !controls.length;
  $("controls").replaceChildren(...controls.map(cameraControls));
}

async function pointAt(event) {
  if (!video.videoWidth || recording || event.target.closest("a, button")) return;
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
    const result = await postJson("/api/point", {
      frame: data.split(",")[1],
      x: Math.round(spot.x * (full.width - 1)),
      y: Math.round(spot.y * (full.height - 1)),
      w: full.width,
      h: full.height,
    });
    if (!result.found) {
      placeTag("Nothing there. Try again.", null);
      holdFreeze(POINT_MS);
      return;
    }
    paintOutline(ink, result.box, videoArea());
    placeTag("Naming it", result.box);
    const named = await postJson(`/api/point/${result.id}/name`);
    placeTag(named.noun, result.box);
    setPointed({ id: result.id, noun: named.noun });
    holdFreeze(POINT_MS);
  } catch (error) {
    endFreeze();
    flash(error.message);
  }
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

/* ---------- Voice ---------- */

function showTalking(live) {
  const mic = $("talk");
  mic.classList.toggle("live", live);
  mic.setAttribute("aria-label", live ? "Listening" : "Hold to talk");
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
  const data = await postJson("/api/voice");
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
    note("form-note", `The microphone did not start: ${error.message}`, "error");
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
    note("form-note", `Voice failed: ${error.message}`, "error");
  }
}

function note(id, text, kind) {
  const el = $(id);
  el.className = "note" + (kind ? " " + kind : "");
  el.textContent = text || "";
}

/* ---------- Sensor history, shown on a Whenever's page ---------- */

function seconds(value) {
  return value == null ? "–" : value.toFixed(2) + " s";
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
  const h = history.get(`${ruleId}/${sensorId}`);
  if (!h) return;
  for (const box of document.querySelectorAll(`[data-sensor="${ruleId}/${sensorId}"]`)) {
    box.querySelector(".dots").replaceChildren(...h.hits.map((hit) => {
      const dot = document.createElement("i");
      if (hit) dot.className = "hit";
      return dot;
    }));
    box.querySelector(".model-note").textContent = h.text;
  }
}

function sensorRow(rule, sensor) {
  const row = element("div", "sensor-row");
  row.dataset.sensor = `${rule.id}/${sensor.id}`;
  row.append(element("span", "dots"), element("span", "model-note", sensor.pattern === "moment"
    ? "Checks each burst of movement"
    : `Checks every ${sensor.check_every_s} s while the picture changes`));
  return row;
}

/* ---------- Router ---------- */

const SCREENS = [...document.querySelectorAll("[data-screen]")];

function needsStart() {
  return !source && !liveCamera && !(state && state.live_camera);
}

function route() {
  const [path] = location.hash.slice(1).split("?");
  const parts = path.split("/").filter(Boolean);
  let name = parts[0] || (needsStart() ? "start" : "whenevers");
  if (!SCREENS.some((s) => s.dataset.screen === name)) name = "whenevers";
  const changed = name !== screen;
  screen = name;
  for (const s of SCREENS) {
    const on = s.dataset.screen === name;
    // Live never hides, because capture needs a rendered video.
    if (s.dataset.screen === "live") s.classList.toggle("offstage", !on);
    else s.hidden = !on;
  }
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const current = a.dataset.nav === name || (name === "whenever" && a.dataset.nav === "whenevers");
    if (current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("point-banner").hidden = !(name === "live" && /point=1/.test(location.hash));
  focusId = name === "live" ? (location.hash.match(/rule=([a-z0-9]+)/) || [])[1] || null : null;
  renderFocus(true);
  if (changed) window.scrollTo(0, 0);
  if (name === "whenever") renderDetail(parts[1], true);
  if (name === "activity") {
    showActivityView(parts[1] === "reports" ? "reports" : "feed");
    loadJournal(true);
    localStorage.setItem("whenever-seen", String(Date.now() / 1000));
    $("activity-dot").hidden = true;
  }
  if (name === "actions") renderPhoneCard(true);
  if (name === "whenevers") renderList(true);
  if (name === "create") renderCreate();
}

window.addEventListener("hashchange", route);

/* ---------- My Whenevers ---------- */

function watchingText(rule) {
  const named = rule.cameras || [];
  if (named.length === 1) return `Watching ${named[0]}`;
  if (named.length === 2) return `Watching ${named[0]} and ${named[1]}`;
  if (named.length > 2) return `Watching ${named.length} cameras`;
  return (state?.cameras?.length || 0) > 1 ? "Watching all cameras" : "Watching";
}

function whereLine(rule) {
  const where = element("span", "card-where");
  if (rule.schedules?.length && !rule.sensors?.length) {
    const s = rule.schedules[0];
    where.append(icon("clock"), element("span", null, s.days?.length ? `${s.when} on ${s.days.join(", ")}` : s.when));
    return where;
  }
  if (!rule.paused) where.classList.add("watching");
  where.append(element("i", "live-dot"), element("span", null, rule.paused ? "Stopped" : watchingText(rule)));
  return where;
}

// The trigger and the actions as icon tiles, so a list can be scanned without reading every card.
function ruleIcons(rule) {
  const text = (rule.sentence || "").toLowerCase();
  const names = [rule.schedules?.length && !rule.sensors?.length ? "clock" : "live"];
  if (/phone|alert|notif/.test(text)) names.push("phone");
  if (/light|lamp/.test(text)) names.push("bulb");
  if (/turn .* to|look closer|aim/.test(text)) names.push("move");
  if (/screen|show|draw|outline/.test(text)) names.push("screen");
  if (/say|speak|out loud/.test(text)) names.push("speaker");
  if (/record|report|count|chart/.test(text)) names.push("chart");
  for (const action of state?.actions || []) {
    if (action.kind !== "phone" && text.includes(action.name.toLowerCase())) names.push("globe");
  }
  const tiles = element("span", "icon-tiles");
  for (const name of [...new Set(names)].slice(0, 5)) {
    const tile = element("span", "icon-tile");
    tile.append(icon(name));
    tiles.append(tile);
  }
  return tiles;
}

function toggleSwitch(rule) {
  const sw = element("button", "switch");
  sw.type = "button";
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-checked", String(!rule.paused));
  sw.setAttribute("aria-label", rule.paused ? `Turn on ${rule.name}` : `Stop ${rule.name}`);
  sw.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    sw.setAttribute("aria-checked", String(rule.paused));
    await postJson(`/api/rule/${rule.id}/${rule.paused ? "resume" : "stop"}`).catch((e) => flash(e.message));
    refreshState();
  });
  return sw;
}

function ruleCard(rule, tag = "a") {
  const card = element(tag, "card" + (rule.paused ? " stopped" : ""));
  card.dataset.hue = hueOf(rule);
  if (tag === "a") card.href = `#/whenever/${rule.id}`;
  const top = element("div", "card-top");
  top.append(ruleIcons(rule), toggleSwitch(rule));
  card.append(top, sentenceNode(rule.sentence || rule.name));
  const foot = element("div", "card-foot");
  foot.append(whereLine(rule), element("span", null, rule.fired ? `Ran ${rule.fired} ${rule.fired === 1 ? "time" : "times"}` : "Not run yet"));
  if (rule.last_fired) foot.append(element("span", null, `Last ${clock(rule.last_fired)}`));
  if (rule.pointed) foot.append(element("span", null, `Pointed at ${rule.pointed}`));
  card.append(foot);
  return card;
}

function firedTab(rule) {
  if (!rule.last_fired || Date.now() / 1000 - rule.last_fired > FIRED_TAB_S) return null;
  const tab = element("a", "fired-tab");
  tab.href = "#/activity";
  tab.append(icon("bell"), element("span", null, `Fired ${ago(rule.last_fired)}`), element("span", null, "See proof"));
  return tab;
}

function renderList(force) {
  const visible = rules.filter((r) => filter === "all" || (filter === "on" ? !r.paused : r.paused));
  const key = JSON.stringify([filter, visible.map((r) => [r.id, r.paused, r.fired, r.last_fired, r.sentence]),
    Math.floor(Date.now() / 60000), state?.cameras?.length]);
  if (!force && key === keys.list) return;
  keys.list = key;
  const list = $("whenever-list");
  if (!rules.length) {
    $("more").hidden = true;
    const empty = element("li", "empty");
    empty.append(element("h2", null, "Nothing is watching yet"),
      element("p", null, "Say what should happen, in one sentence. Whenever writes it, tests it, and starts watching."));
    const go = element("a", "pill-button big", null);
    go.href = "#/create";
    go.append(icon("plus"), "Create a Whenever");
    empty.append(go);
    list.replaceChildren(empty);
    return;
  }
  if (!visible.length) {
    list.replaceChildren(element("li", "empty", filter === "on" ? "No Whenever is watching." : "No Whenever is stopped."));
    return;
  }
  list.replaceChildren(...visible.map((rule) => {
    const item = element("li");
    item.append(ruleCard(rule));
    const tab = firedTab(rule);
    if (tab) item.append(tab);
    return item;
  }));
  renderMore();
}

// With few Whenevers, the list offers a next one, one from each kind of use.
function renderMore() {
  const more = $("more");
  more.hidden = rules.length === 0 || rules.length >= 3;
  if (more.hidden || $("more-templates").children.length) return;
  const picks = ["Shop and café", "Reports", "Home"].map((group) => TEMPLATES.find((t) => t.group === group));
  $("more-templates").replaceChildren(...picks.map((t) => templateCard(t, () => { location.hash = "#/create"; useTemplate(t); })));
}

$("filter").addEventListener("click", (event) => {
  const b = event.target.closest("[data-filter]");
  if (!b) return;
  filter = b.dataset.filter;
  $("filter").querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  renderList(true);
});

/* ---------- One Whenever ---------- */

function renderDetail(id, force) {
  const rule = rules.find((r) => r.id === id);
  const box = $("detail");
  const key = JSON.stringify(rule ? [rule.id, rule.paused, rule.fired, rule.last_fired, rule.sentence,
    rule.plan, rule.schedules.map((s) => s.next)] : null);
  if (!force && key === keys.detail) {
    if (rule) renderValues(rule);
    return;
  }
  keys.detail = key;
  if (!rule) {
    const empty = element("div", "empty");
    empty.append(element("h2", null, "This Whenever is gone"),
      element("p", null, "It was deleted, or it was replaced by an edit."));
    const back = element("a", "pill-button", "See My Whenevers");
    back.href = "#/whenevers";
    empty.append(back);
    box.replaceChildren(empty);
    return;
  }
  const hero = ruleCard(rule, "div");
  hero.classList.add("detail-card");
  const nodes = [hero];
  const tab = firedTab(rule);
  if (tab) nodes.push(tab);

  const values = element("div", "values");
  values.id = "values-" + rule.id;
  nodes.push(values);

  const actionsRow = element("div", "detail-actions");
  const live = element("a", "pill-button", null);
  live.href = `#/live?rule=${rule.id}`;
  live.append(icon("live"), "Watch it live");
  actionsRow.append(
    live,
    button("Change the words", "pill-button ghost", () => startEdit(rule), "pencil"),
    deleteButton(rule),
  );
  nodes.push(actionsRow);

  nodes.push(element("h2", "section-title", "What it does"));
  const plan = element("ol", "plan");
  for (const step of rule.plan || []) plan.append(element("li", null, step));
  nodes.push(plan);

  nodes.push(element("h2", "section-title", "How it watches"));
  const how = element("dl", "how");
  for (const s of rule.schedules || []) {
    const next = new Date(s.next * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
    how.append(element("dt", null, "On a schedule"), element("dd", null, `${s.when}${s.days?.length ? ` on ${s.days.join(", ")}` : ""}. Next ${next}.`));
  }
  for (const sensor of rule.sensors || []) {
    how.append(element("dt", null, sensor.starts_off ? "Wakes when needed" : "Always looking"));
    const dd = element("dd");
    dd.append(element("span", null, sensor.target
      ? `Looks for ${article(sensor.target)} ${sensor.target} in each frame it checks`
      : `Asks: ${sensor.question}`));
    dd.append(sensorRow(rule, sensor));
    how.append(dd);
  }
  nodes.push(how);
  const code = element("details", "code");
  code.append(element("summary", null, "Show the code Wonder wrote"));
  const pre = element("pre");
  code.append(pre);
  code.ontoggle = async () => {
    if (code.open && !pre.textContent) pre.textContent = await (await fetch(`/api/rule/${rule.id}/code`)).text();
  };
  nodes.push(code);

  nodes.push(element("h2", "section-title", "Activity"));
  const feed = element("ol", "feed");
  feed.id = "detail-feed";
  nodes.push(feed);

  box.replaceChildren(...nodes);
  renderValues(rule);
  (rule.sensors || []).forEach((sensor) => renderSensor(rule.id, sensor.id));
  renderDetailFeed(rule);
  loadJournal(true);
}

function deleteButton(rule) {
  const wrap = element("span");
  const ask = button("Delete", "pill-button ghost", () => {
    const confirm = element("span", "confirm");
    confirm.append(element("span", null, "Delete it for good?"),
      button("Delete", "pill-button danger small", async () => {
        await fetch("/api/rule/" + rule.id, { method: "DELETE" });
        location.hash = "#/whenevers";
        refreshState();
      }),
      button("Keep it", "link-button", () => wrap.replaceChildren(ask)));
    wrap.replaceChildren(confirm);
  }, "trash");
  wrap.append(ask);
  return wrap;
}

function renderValues(rule) {
  const box = $("values-" + rule.id);
  if (!box) return;
  const entries = Object.entries(rule.values || {});
  const key = JSON.stringify(entries);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(...entries.map(([name, value]) => {
    const tile = element("div", "value");
    tile.append(element("b", null, value), element("span", null, name));
    return tile;
  }));
}

function renderDetailFeed(rule) {
  const feed = $("detail-feed");
  if (!feed) return;
  const rows = journal.filter((r) => r.rule === rule.name && shown(r, "all")).slice(-40).reverse();
  feed.replaceChildren(...(rows.length ? rows.map(entryNode) : [element("li", "note", "Nothing yet. Events appear here with their proof.")]));
}

/* ---------- Create ---------- */

const TEMPLATES = [
  { group: "Home", hue: "indigo", when: "a car blocks the gate for 5 minutes", then: ["phone", "light"], extra: "" },
  { group: "Home", hue: "indigo", when: "a parcel is left at the door", then: ["phone"], extra: "" },
  { group: "Home", hue: "indigo", when: "someone is at the gate after 11 PM", then: ["light", "phone"], extra: "" },
  { group: "Shop and café", hue: "jade", when: "a table waits 5 minutes and no staff comes to it", then: ["phone"], extra: "say which table" },
  { group: "Shop and café", hue: "jade", when: "a table is empty but still has plates on it", then: ["phone"], extra: "tell me to clean it" },
  { group: "Shop and café", hue: "jade", when: "more than 4 people wait at the counter", then: ["phone"], extra: "" },
  { group: "Reports", hue: "ink", when: "it is 9 AM on a Monday", then: ["phone"], extra: "send me last week's busy hours as a chart" },
  { group: "Reports", hue: "ink", when: "it is 10 PM", then: ["phone"], extra: "send me a short summary of today" },
  { group: "Gestures", hue: "plum", when: "I show a thumbs up", then: ["screen"], extra: "count how many times today" },
  { group: "Gestures", hue: "plum", when: "I point at the lamp and close my fist", then: [], extra: "turn off the lamp" },
];

// Each choice under "then" is a phrase that joins the sentence. Actions come from the server.
function thenOptions() {
  const options = [];
  const phone = (state?.actions || []).find((a) => a.kind === "phone");
  options.push({ id: "phone", label: "Alert my phone", icon: "phone", phrase: "alert my phone with a picture",
    ready: !!(phone && !phone.missing.length && state?.phone_paired), pair: true });
  options.push({ id: "screen", label: "Show it here", icon: "screen", phrase: "show it on the screen", ready: true });
  if (state?.platform === "darwin") options.push({ id: "speak", label: "Say it out loud", icon: "speaker", phrase: "say it out loud", ready: true });
  for (const camera of state?.controls || []) {
    if (camera.light) options.push({ id: `light:${camera.name}`, label: `${camera.name} light`, icon: "bulb", phrase: `turn on the ${camera.name} light`, ready: true, light: true });
    if (camera.moves) options.push({ id: `aim:${camera.name}`, label: `Turn ${camera.name}`, icon: "move", phrase: `turn ${camera.name} to look closer`, ready: true });
  }
  for (const action of state?.actions || []) {
    if (action.kind === "phone") continue;
    options.push({ id: `act:${action.name}`, label: action.name, icon: "globe", phrase: `use ${action.name}`, ready: !action.missing.length });
  }
  options.push({ id: "record", label: "Keep a record", icon: "note", phrase: "keep a record of it for reports", ready: true });
  return options;
}

function renderThenChips() {
  const options = thenOptions();
  const key = JSON.stringify([options.map((o) => [o.id, o.ready]), [...thenPicked]]);
  if (key === keys.then) return;
  keys.then = key;
  const row = $("then-chips");
  row.replaceChildren(...options.map((o) => {
    const chip = button(o.label, "chip" + (o.ready ? "" : " wait"), () => {
      if (!o.ready && o.pair) {
        openPairing(() => { thenPicked.add(o.id); keys.then = ""; renderThenChips(); updatePreview(); });
        return;
      }
      if (!o.ready) {
        location.hash = "#/actions";
        flash("This action needs a key first. See Actions.");
        return;
      }
      if (thenPicked.has(o.id)) thenPicked.delete(o.id);
      else thenPicked.add(o.id);
      keys.then = "";
      renderThenChips();
      updatePreview();
    }, o.icon);
    chip.setAttribute("aria-pressed", String(thenPicked.has(o.id)));
    if (!o.ready) chip.title = o.pair ? "Pair your phone first" : "Needs a key in Actions";
    return chip;
  }));
}

function renderCameraChips() {
  const cameras = state?.cameras || [];
  const key = JSON.stringify([cameras, [...camerasPicked]]);
  if (key === keys.cameras) return;
  keys.cameras = key;
  const row = $("camera-chips");
  row.hidden = cameras.length < 2;
  if (row.hidden) return;
  const all = button("All cameras", "chip", () => { camerasPicked.clear(); keys.cameras = ""; renderCameraChips(); updatePreview(); }, "live");
  all.setAttribute("aria-pressed", String(!camerasPicked.size));
  row.replaceChildren(all, ...cameras.map((name) => {
    const chip = button(name, "chip", () => {
      if (camerasPicked.has(name)) camerasPicked.delete(name);
      else camerasPicked.add(name);
      keys.cameras = "";
      renderCameraChips();
      updatePreview();
    });
    chip.setAttribute("aria-pressed", String(camerasPicked.has(name)));
    return chip;
  }));
}

function renderTemplates() {
  const groups = new Map();
  for (const t of TEMPLATES) groups.set(t.group, [...(groups.get(t.group) || []), t]);
  $("templates").replaceChildren(...[...groups].map(([group, items]) => {
    const block = element("div", "template-group");
    block.append(element("h3", null, group));
    const row = element("div", "template-row");
    for (const t of items) row.append(templateCard(t, () => useTemplate(t)));
    block.append(row);
    return block;
  }));
}

function templateCard(t, onclick) {
  const card = element("button", "card template");
  card.type = "button";
  card.dataset.hue = t.hue;
  card.append(sentenceNode(`When ${t.when}`));
  const then = element("div", "card-then");
  const labels = t.then.map((id) => ({ phone: ["phone", "Phone"], light: ["bulb", "Light"], screen: ["screen", "Screen"] }[id]));
  for (const [iconName, label] of labels) {
    const chip = element("span");
    chip.append(icon(iconName), label);
    then.append(chip);
  }
  if (t.extra) then.append(element("span", null, t.extra));
  card.append(then);
  card.addEventListener("click", onclick);
  return card;
}

function useTemplate(t) {
  endEdit();
  $("sentence").value = t.when;
  thenPicked.clear();
  for (const id of t.then) {
    if (id === "light") {
      const light = thenOptions().find((o) => o.light);
      if (light) thenPicked.add(light.id);
      else $("then-extra").value = "turn on a light";
    } else thenPicked.add(id);
  }
  $("then-extra").value = t.extra || $("then-extra").value;
  keys.then = "";
  renderThenChips();
  updatePreview();
  showStep("compose");
  $("rule-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("sentence").focus({ preventScroll: true });
}

function joinList(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// The two slots become one sentence for Wonder: "When <trigger> in <cameras>, then <actions>."
function composeSentence() {
  let trigger = $("sentence").value.trim().replace(/[.\s]+$/, "");
  if (!trigger) return "";
  const phrases = thenOptions().filter((o) => thenPicked.has(o.id)).map((o) => o.phrase);
  const extra = $("then-extra").value.trim().replace(/[.\s]+$/, "");
  if (extra) phrases.push(extra);
  if (editing) return phrases.length ? `${trigger}. Also ${joinList(phrases)}.` : `${trigger}.`;
  trigger = trigger.replace(/^(when|whenever|if)\s+/i, "");
  const cams = [...camerasPicked];
  const where = cams.length ? ` in ${joinList(cams)}` : "";
  const then = phrases.length ? `, then ${joinList(phrases)}` : "";
  return `When ${trigger}${where}${then}.`;
}

// The two slots, read back as one sentence with the chosen cameras and actions filled in.
function updatePreview() {
  const box = $("sentence-preview");
  const trigger = $("sentence").value.trim().replace(/[.\s]+$/, "").replace(/^(when|whenever|if)\s+/i, "");
  const phrases = thenOptions().filter((o) => thenPicked.has(o.id)).map((o) => o.phrase);
  const extra = $("then-extra").value.trim().replace(/[.\s]+$/, "");
  if (extra) phrases.push(extra);
  const slot = (text, empty) => element("span", empty ? "slot-fill is-empty" : "slot-fill", text);
  const joint = (text) => element("span", "joint", text);
  const nodes = [];
  if (editing) {
    nodes.push(slot(trigger || "your words", !trigger));
  } else {
    nodes.push(joint("When "), slot(trigger || "something happens", !trigger));
    const cams = [...camerasPicked];
    cams.forEach((name, i) => nodes.push(joint(i ? (i === cams.length - 1 ? " and " : ", ") : " on "), slot(name)));
  }
  if (phrases.length) {
    nodes.push(joint(editing ? ". Also " : ", then "));
    phrases.forEach((text, i) => {
      if (i) nodes.push(joint(i === phrases.length - 1 ? " and " : ", "));
      nodes.push(slot(text));
    });
  } else if (!editing) {
    nodes.push(joint(", then "), slot("choose what happens", true));
  }
  nodes.push(joint("."));
  box.replaceChildren(...nodes);
}

function showStep(step) {
  createStep = step;
  document.querySelectorAll("[data-step]").forEach((s) => { s.hidden = s.dataset.step !== step; });
}

function renderCreate() {
  renderThenChips();
  renderCameraChips();
  updatePreview();
  if (!$("templates").children.length) renderTemplates();
  $("templates").closest(".templates").hidden = !!editing;
}

function startEdit(rule) {
  editing = rule;
  $("create-title").textContent = "Change this Whenever";
  $("sentence-label").textContent = "The words";
  $("sentence").value = rule.sentence;
  $("cancel-edit").hidden = false;
  thenPicked.clear();
  $("then-extra").value = "";
  keys.then = "";
  showStep("compose");
  note("form-note", "The old Whenever keeps running until the new one passes its tests.");
  location.hash = "#/create";
}

function endEdit() {
  editing = null;
  $("create-title").textContent = "Create a Whenever";
  $("sentence-label").textContent = "When";
  $("cancel-edit").hidden = true;
}

function resetCreate() {
  endEdit();
  $("sentence").value = "";
  $("then-extra").value = "";
  thenPicked.clear();
  camerasPicked.clear();
  example = null;
  $("example-box").hidden = true;
  setPointed(null);
  keys.then = keys.cameras = "";
  note("form-note", "");
  showStep("compose");
}

$("rule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const sentence = composeSentence();
  if (!sentence && !example) {
    note("form-note", "Write what should happen, or show an example.", "error");
    $("sentence").focus();
    return;
  }
  pendingSentence = sentence;
  if (!sentence) return writeIt();
  $("check").disabled = true;
  note("form-note", "Spark is reading it");
  try {
    const summary = await postJson("/api/understand", { sentence });
    renderCheck(sentence, summary);
    showStep("check");
    note("form-note", "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    note("form-note", error.message, "error");
  } finally {
    $("check").disabled = false;
  }
});

function renderCheck(sentence, summary) {
  const card = element("div", "card check-card");
  card.dataset.hue = "indigo";
  // Spark's reading, in the same grammar as every card: light joints, bold slots.
  const read = element("p", "sentence-big");
  const joint = (text) => element("span", "joint", text);
  const lower = (text) => text.charAt(0).toLowerCase() + text.slice(1);
  const when = (summary.when || sentence).replace(/^(when|whenever)\s+/i, "").replace(/[.\s]+$/, "");
  const startsEvery = /^every\b/i.test(when);
  read.append(joint(startsEvery ? "" : "When "), startsEvery ? when : lower(when));
  const items = (summary.then?.length ? summary.then : ["Show it on the screen"]).map((x) => lower(x.replace(/[.\s]+$/, "")));
  read.append(joint(", then "));
  items.forEach((item, i) => {
    if (i) read.append(joint(i === items.length - 1 ? " and " : ", "));
    read.append(item);
  });
  read.append(joint("."));
  card.append(read);
  const facts = element("div", "check-facts");
  if (summary.watch?.length) facts.append(element("span", null, `Watches ${joinList(summary.watch)}`));
  if (pointed) facts.append(element("span", null, `Knows "this" means the ${pointed.noun}`));
  if (example) facts.append(element("span", null, `Tests itself on your ${example.frames.length}-frame example`));
  facts.append(element("span", null, "Writing it costs about $0.15 to $0.35 and takes about a minute."));
  card.append(facts);
  if (summary.missing) {
    const warn = element("div", "check-warn");
    const text = element("span");
    const link = element("a", null, "Connect an action");
    link.href = "#/actions";
    text.append(`${summary.missing} `, link, " first, or Wonder will stop.");
    warn.append(icon("alert"), text);
    card.append(warn);
  }
  if (/phone/i.test((summary.then || []).join(" ")) && !state?.phone_paired) {
    const warn = element("div", "check-warn");
    const text = element("span", null, "Your phone is not paired yet, so its alerts would go nowhere. ");
    const pair = button("Pair your phone", "link-button", () => openPairing());
    text.append(pair);
    warn.append(icon("alert"), text);
    card.append(warn);
  }
  if (summary.question) {
    const ask = element("div", "check-warn");
    ask.append(icon("alert"), element("span", null, summary.question));
    card.append(ask);
  }
  $("check-card").replaceChildren(card);
  note("check-note", `Spark read your words for $${summary.cost}. If this is not what you mean, change the words.`);
}

$("check-back").addEventListener("click", () => showStep("compose"));
$("sentence").addEventListener("input", updatePreview);
$("then-extra").addEventListener("input", updatePreview);
$("add").addEventListener("click", () => writeIt());

async function writeIt() {
  const sentence = pendingSentence;
  showStep("writing");
  $("writing-title").textContent = editing ? "Wonder is changing your Whenever" : "Wonder is writing your Whenever";
  const started = Date.now();
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    $("writing-time").textContent = `${s} s so far. It writes the code, tests it, and a reviewer checks it against your words.`;
  }, 1000);
  try {
    const data = await postJson(editing ? `/api/rule/${editing.id}/edit` : "/api/rule", {
      sentence,
      example: example?.frames,
      idle: example?.idle,
      motions: example?.motions,
      gap_s: example?.gapS,
      point: pointed?.id,
    });
    const job = await followJob(data.job);
    if (job.status === "failed") throw new Error(job.error);
    const wasEdit = !!editing;
    await refreshState();
    renderDone(job, wasEdit);
    resetCreate();
    showStep("done");
  } catch (error) {
    const box = element("div", "empty");
    box.append(element("h2", null, "It did not work this time"), element("p", null, error.message));
    const row = element("div", "form-row");
    row.style.justifyContent = "center";
    row.append(button("Change the words", "pill-button", () => showStep("compose"), "pencil"));
    box.append(row);
    $("done-card").replaceChildren(box);
    showStep("done");
  } finally {
    clearInterval(timer);
  }
}

function renderDone(job, wasEdit) {
  const rule = rules.find((r) => r.id === job.rule);
  const nodes = [element("h2", "done-title", wasEdit ? "Updated. It follows your new words." : "It's on. Whenever is watching.")];
  if (rule) nodes.push(ruleCard(rule));
  const row = element("div", "form-row");
  const open = element("a", "pill-button big", "Open it");
  open.href = rule ? `#/whenever/${rule.id}` : "#/whenevers";
  const live = element("a", "pill-button ghost", "Watch live");
  live.href = "#/live";
  row.append(open, live, button("Create another", "link-button", () => { resetCreate(); window.scrollTo(0, 0); }));
  nodes.push(row, element("p", "note", `Writing it cost about $${job.cost}.`));
  $("done-card").replaceChildren(...nodes);
}

async function followJob(id) {
  const steps = $("steps");
  while (true) {
    const job = await (await fetch("/api/job/" + id)).json();
    const items = job.steps.filter((text) => !/^Ran |^Wrote |^Installing /.test(text) || job.steps.length < 6);
    const key = items.join("\n");
    if (steps.dataset.key !== key) {
      steps.dataset.key = key;
      steps.replaceChildren(...items.slice(-9).map((text) => element("li", null, text)));
    }
    if (job.status !== "working") return job;
    await wait(POLL_MS);
  }
}

$("cancel-edit").addEventListener("click", () => {
  const id = editing?.id;
  resetCreate();
  location.hash = id ? `#/whenever/${id}` : "#/whenevers";
});

$("clear-pointed").addEventListener("click", () => setPointed(null));
$("clear-example").addEventListener("click", () => {
  example = null;
  $("example-box").hidden = true;
});

// Pointing and recording happen in the live view, then the person returns to the sentence.
$("point-start").addEventListener("click", () => {
  $("point-text").textContent = "Tap the thing you mean.";
  location.hash = "#/live?point=1";
});
$("point-done").addEventListener("click", () => { location.hash = "#/create"; });
$("record").addEventListener("click", async () => {
  location.hash = "#/live";
  await wait(350);
  await recordExample();
  location.hash = "#/create";
});

/* ---------- Activity ---------- */

const KIND_ICON = { event: "eye", notify: "bell", draw: "photo", image: "photo", gallery: "photo", action: "bolt",
  record: "note", error: "alert", say: "speaker" };
const ALERT_KINDS = new Set(["notify", "draw", "image", "gallery", "event", "say"]);

function shown(row, kind) {
  if (row.kind === "end" || row.kind === "log") return false;
  if (kind === "alerts") return ALERT_KINDS.has(row.kind);
  if (kind === "action") return row.kind === "action";
  if (kind === "error") return row.kind === "error" || (row.kind === "action" && row.values?.ok === false);
  return true;
}

// Names that code chose, such as car_parked, read as words.
function words(name) {
  const text = String(name || "").replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function article(noun) {
  return /^[aeiou]/i.test(noun) ? "an" : "a";
}

// Which camera a row came from: the tile that the server measured, or the one camera its Whenever watches.
function cameraOf(row) {
  if (row.values?.camera) return row.values.camera;
  const rule = rules.find((r) => r.name === row.rule);
  return rule?.cameras?.length === 1 ? rule.cameras[0] : "";
}

// A fact that a rule kept, said plainly: the camera and the number first, any other facts after.
function recordNote(values) {
  const parts = [];
  const rest = { ...values };
  if (rest.camera) parts.push(String(rest.camera));
  delete rest.camera;
  if (rest.count != null) parts.push(`number ${rest.count}`);
  delete rest.count;
  for (const [key, value] of Object.entries(rest)) parts.push(`${words(key).toLowerCase()} ${value}`);
  return parts.length ? `Noted for reports (${parts.join(", ")})` : "Noted for reports";
}

function entryTitle(row) {
  switch (row.kind) {
    case "event": {
      if (row.text) return row.text.charAt(0).toUpperCase() + row.text.slice(1);
      const where = cameraOf(row) ? ` in ${cameraOf(row)}` : "";
      if (row.values?.what) return `Saw ${article(row.values.what)} ${row.values.what}${where}`;
      const said = words(row.name);
      return said.toLowerCase().includes(cameraOf(row).toLowerCase()) ? said : `${said}${where}`;
    }
    case "action":
      if (row.name === "Phone") return row.values?.ok === false ? "Phone alert failed" : "Phone alert sent";
      return row.values?.ok === false ? `${row.name} failed: ${row.text}` : `${row.name}: done`;
    case "record": return words(row.name);
    default: return row.text || row.name || row.kind;
  }
}

function entryNode(row) {
  const li = element("li", "entry");
  li.dataset.t = row.t;
  if (["notify", "draw", "image", "gallery"].includes(row.kind)) li.classList.add("alert");
  if (row.kind === "error" || (row.kind === "action" && row.values?.ok === false)) li.classList.add("problem");
  const badge = element("span", "entry-icon");
  badge.append(icon(KIND_ICON[row.kind] || "pulse"));
  const text = element("div", "entry-text");
  const meta = [clock(row.t), row.rule || "Whenever"];
  if (row.kind === "record") meta.push(recordNote(row.values || {}));
  text.append(element("b", null, entryTitle(row)), element("div", "entry-meta", meta.join(" · ")));
  if (row.values?.check) {
    const buttons = element("div", "entry-buttons");
    buttons.append(button("Why?", "chip", () => showEvidence(row.values.check), "eye"),
      labelButton(row.values.check, "right", "Right"), labelButton(row.values.check, "wrong", "Wrong"));
    text.append(buttons);
  }
  li.append(badge, text);
  if (row.values?.picture) li.append(proofPicture(row));
  return li;
}

// A wall frame shows six cameras, so the proof shows only the tile of the camera that fired.
function tileOf(row) {
  if (!["event", "draw"].includes(row.kind)) return null;
  const [columns, rows] = row.values?.wall || state?.wall || [1, 1];
  if (columns * rows <= 1) return null;
  let index = row.values?.tile;
  if (index == null) {
    const name = cameraOf(row);
    index = name ? (state?.cameras || []).indexOf(name) : -1;
  }
  if (index == null || index < 0 || index >= columns * rows) return null;
  return { columns, rows, col: index % columns, row: Math.floor(index / columns) };
}

function proofPicture(row) {
  const url = row.values.picture;
  const tile = tileOf(row);
  const pic = element("button", "entry-picture");
  pic.type = "button";
  pic.setAttribute("aria-label", `Open the proof frame: ${entryTitle(row)}`);
  if (tile) {
    pic.classList.add("tiled");
    pic.style.backgroundImage = `url("${url}")`;
    pic.style.backgroundSize = `${tile.columns * 100}% ${tile.rows * 100}%`;
    pic.style.backgroundPosition = `${tile.columns > 1 ? (tile.col / (tile.columns - 1)) * 100 : 0}% ${tile.rows > 1 ? (tile.row / (tile.rows - 1)) * 100 : 0}%`;
  } else {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.loading = "lazy";
    pic.append(img);
  }
  const box = tile ? { x1: tile.col, y1: tile.row, x2: tile.col + 1, y2: tile.row + 1, w: tile.columns, h: tile.rows } : null;
  pic.addEventListener("click", () => openFrame(url, box, `${cameraOf(row) || row.rule}, ${clock(row.t)}`));
  return pic;
}

function renderFeed(force) {
  const rows = journal.filter((r) => shown(r, feedKind)).slice(-250).reverse();
  const key = JSON.stringify([feedKind, rows.length, rows[0]?.t]);
  if (!force && key === keys.feed) return;
  keys.feed = key;
  const list = $("activity-list");
  if (!rows.length) {
    const empty = element("li", "empty");
    empty.append(element("h2", null, "Quiet so far"), element("p", null, "When a Whenever sees something, it shows up here with its proof frame."));
    list.replaceChildren(empty);
    $("activity-guide").textContent = "";
    return;
  }
  const nodes = [];
  let day = "";
  for (const row of rows) {
    const d = dayName(row.t);
    if (d !== day) {
      day = d;
      nodes.push(element("li", "day", d));
    }
    nodes.push(entryNode(row));
  }
  list.replaceChildren(...nodes);
  updateGuide();
}

// Guide words: the time span in view stays pinned while the person scrolls.
function updateGuide() {
  if (screen !== "activity") return;
  const entries = [...$("activity-list").querySelectorAll(".entry")];
  const inView = entries.filter((e) => {
    const r = e.getBoundingClientRect();
    return r.bottom > 60 && r.top < innerHeight - 80;
  });
  if (!inView.length) return;
  const newest = Number(inView[0].dataset.t);
  const oldest = Number(inView[inView.length - 1].dataset.t);
  $("activity-guide").textContent = `${dayName(newest)}, ${clock(oldest)} to ${clock(newest)}`;
}

window.addEventListener("scroll", () => requestAnimationFrame(updateGuide), { passive: true });

$("activity-filter").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-kind]");
  if (!chip) return;
  feedKind = chip.dataset.kind;
  $("activity-filter").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === chip));
  renderFeed(true);
});

function showActivityView(view) {
  $("activity-tabs").querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  document.querySelectorAll("[data-view-panel]").forEach((p) => { p.hidden = p.dataset.viewPanel !== view; });
  if (view === "reports") renderReports();
}

$("activity-tabs").addEventListener("click", (event) => {
  const b = event.target.closest("[data-view]");
  if (!b) return;
  location.hash = b.dataset.view === "reports" ? "#/activity/reports" : "#/activity";
});

async function loadJournal(force) {
  const now = Date.now();
  const every = ["activity", "whenever"].includes(screen) ? JOURNAL_MS : JOURNAL_IDLE_MS;
  if (!force && now - journalAt < every) return;
  journalAt = now;
  try {
    const data = await (await fetch("/api/journal?limit=1000")).json();
    journal = data.rows || [];
  } catch {
    return;
  }
  const seen = Number(localStorage.getItem("whenever-seen") || 0);
  const fresh = journal.some((r) => r.t > seen && ["notify", "draw", "image", "gallery"].includes(r.kind));
  $("activity-dot").hidden = !fresh || screen === "activity";
  if (screen === "activity") {
    renderFeed();
    if (!document.querySelector('[data-view-panel="reports"]').hidden) renderReports();
  }
  if (screen === "whenever") {
    const id = location.hash.split("/")[2];
    const rule = rules.find((r) => r.id === id);
    if (rule) renderDetailFeed(rule);
  }
}

function renderReports() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const since = start.getTime() / 1000;
  const today = journal.filter((r) => r.t >= since);
  const events = today.filter((r) => r.kind === "event");
  const hours = Array(24).fill(0);
  for (const r of events) hours[new Date(r.t * 1000).getHours()]++;
  const top = Math.max(1, ...hours);
  const nowHour = new Date().getHours();

  const hourCard = element("section", "report");
  hourCard.append(element("h2", null, "Events by hour, today"),
    element("p", "note", `${events.length} ${events.length === 1 ? "event" : "events"} so far. The darkest bar is this hour.`));
  const chart = element("div", "hours");
  chart.setAttribute("role", "img");
  chart.setAttribute("aria-label", hours.map((n, h) => `${h}:00 ${n}`).join(", "));
  hours.forEach((n, h) => {
    const bar = element("i", n ? (h === nowHour ? "now" : "") : "zero");
    bar.style.height = `${Math.max(2, (n / top) * 100)}%`;
    bar.title = `${String(h).padStart(2, "0")}:00, ${n} ${n === 1 ? "event" : "events"}`;
    chart.append(bar);
  });
  const axis = element("div", "hour-axis");
  ["00:00", "06:00", "12:00", "18:00"].forEach((t) => axis.append(element("span", null, t)));
  hourCard.append(chart, axis);

  const byRule = new Map();
  for (const r of events) byRule.set(r.rule, (byRule.get(r.rule) || 0) + 1);
  // A row of a live Whenever takes its card's colour and opens its page. A deleted one says so.
  const ruleCardEl = barCard("Events by Whenever, today", byRule, "No Whenever has seen anything today.", (name) => {
    const rule = rules.find((r) => r.name === name);
    return rule ? { href: `#/whenever/${rule.id}`, hue: hueOf(rule) } : { label: `${name} (removed)` };
  });

  const byAction = new Map();
  for (const r of today.filter((x) => x.kind === "action")) byAction.set(r.name, (byAction.get(r.name) || 0) + 1);
  const actionCard = barCard("Actions that ran, today", byAction, "No action has run today.");

  const spend = element("section", "report");
  spend.append(element("h2", null, "Model spend, today"),
    element("p", "note", `$${(state?.spend_usd || 0).toFixed(3)} of the $${state?.spend_limit_usd || 5} daily limit. Reports read the journal and cost nothing.`));

  $("reports").replaceChildren(hourCard, ruleCardEl, actionCard, spend);
}

function barCard(title, counts, emptyText, describe = () => ({})) {
  const card = element("section", "report");
  card.append(element("h2", null, title));
  const sorted = [...counts].sort((a, b) => b[1] - a[1]);
  if (!sorted.length) {
    card.append(element("p", "note", emptyText));
    return card;
  }
  const top = sorted[0][1];
  const bars = element("div", "bars");
  for (const [name, n] of sorted.slice(0, 8)) {
    const about = describe(name);
    const row = element(about.href ? "a" : "div", "bar-row" + (about.label ? " removed" : ""));
    if (about.href) row.href = about.href;
    const track = element("span", "track");
    const fill = element("i");
    fill.style.width = `${(n / top) * 100}%`;
    if (about.hue) fill.style.background = `var(--hue-${about.hue})`;
    track.append(fill);
    row.append(element("span", null, about.label || name || "Whenever"), element("span", null, String(n)), track);
    bars.append(row);
  }
  card.append(bars);
  return card;
}

/* ---------- Pairing a phone ---------- */

let pairDone = null;

// Three steps: install ntfy, follow the private topic, and see a test alert arrive.
async function openPairing(onDone) {
  pairDone = onDone || null;
  const body = $("pair-body");
  body.replaceChildren(element("p", "note", "Getting your private topic"));
  $("pair").showModal();
  let topic;
  try {
    topic = (await postJson("/api/phone/topic")).topic;
  } catch (error) {
    body.replaceChildren(element("p", "note error", error.message));
    return;
  }
  keys.phone = "";
  const link = `https://ntfy.sh/${topic}`;
  const steps = element("ol", "pair-steps");

  const one = element("li");
  const apps = element("p", "pair-text");
  const ios = element("a", null, "iPhone");
  ios.href = "https://apps.apple.com/app/ntfy/id1625396347";
  ios.target = "_blank";
  const android = element("a", null, "Android");
  android.href = "https://play.google.com/store/apps/details?id=io.heckel.ntfy";
  android.target = "_blank";
  apps.append("It is free, and it shows each alert with its picture. Get it for ", ios, " or ", android, ".");
  one.append(element("b", null, "Install the ntfy app"), apps);

  const two = element("li");
  two.append(element("b", null, "Follow your private topic"));
  if (phone.matches) {
    const open = element("a", "pill-button", "Open in ntfy");
    open.href = link;
    open.prepend(icon("arrow"));
    two.append(element("p", "pair-text", "Open it in the app, then tap Subscribe."), open);
  } else {
    two.append(element("p", "pair-text", "On your phone, scan this code with the camera, or add the topic below in the app."));
    const qr = element("div", "qr pair-qr");
    qr.setAttribute("role", "img");
    qr.setAttribute("aria-label", `QR code for ${link}`);
    two.append(qr);
    loadQr().then((make) => {
      if (!make) return qr.remove();
      const code = make(0, "M");
      code.addData(link);
      code.make();
      qr.innerHTML = code.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
    });
  }
  const topicRow = element("div", "topic pair-topic");
  topicRow.append(element("code", null, topic), button("Copy", "pill-button small ghost", async (event) => {
    await navigator.clipboard.writeText(topic).catch(() => {});
    event.currentTarget.textContent = "Copied";
  }, "copy"));
  two.append(topicRow);

  const three = element("li");
  const result = element("div", "pair-result");
  const send = button("Send a test alert", "pill-button", async () => {
    send.disabled = true;
    result.replaceChildren(element("p", "note", "Sending"));
    try {
      const reply = await postJson("/api/saved-action/Phone/test", { inputs: { text: "Whenever is paired. Alerts will look like this.", title: "Whenever" } });
      if (!reply.ok) throw new Error(`ntfy answered ${reply.status}. Try again.`);
      const yes = button("Yes, it arrived", "pill-button", async () => {
        await postJson("/api/phone/paired", { paired: true });
        $("pair").close();
        flash("Your phone is paired.");
        await refreshState();
        const done = pairDone;
        pairDone = null;
        if (done) done();
      }, "check");
      const no = button("Not yet", "link-button", () => {
        result.replaceChildren(element("p", "pair-text",
          "Check that the app follows exactly this topic, and that notifications are on for ntfy. Then send another test."));
        send.disabled = false;
      });
      result.replaceChildren(element("p", "pair-text", "Did the alert arrive on your phone?"), yes, no);
    } catch (error) {
      result.replaceChildren(element("p", "note error", error.message));
      send.disabled = false;
    }
  }, "bell");
  three.append(element("b", null, "Send a test alert"), element("p", "pair-text", "When it arrives, your Whenevers can alert you too."), send, result);

  steps.append(one, two, three);
  body.replaceChildren(steps);
}

$("pair-close").addEventListener("click", () => { pairDone = null; $("pair").close(); });
$("pair").addEventListener("click", (event) => { if (event.target === $("pair")) { pairDone = null; $("pair").close(); } });

/* ---------- Actions ---------- */

let qrLoading = null;

function loadQr() {
  qrLoading ??= new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js";
    script.onload = () => resolve(window.qrcode);
    script.onerror = () => resolve(null);
    document.head.append(script);
  });
  return qrLoading;
}

async function renderPhoneCard(force) {
  const key = JSON.stringify([state?.phone_topic || "", !!state?.phone_paired]);
  if (!force && keys.phone === key) return;
  keys.phone = key;
  const card = element("section", "card phone-card");
  card.dataset.hue = "indigo";
  const paired = !!state?.phone_paired;
  card.append(element("h2", null, paired ? "Your phone is paired" : "Get alerts on your phone"));
  card.append(element("p", null, paired
    ? "Every notification from a Whenever also reaches your phone, with its picture. Tap an alert to open its proof here."
    : "Pair a phone once, in about a minute. Then any Whenever can alert you, with the picture that proves it."));
  const row = element("div", "form-row");
  if (paired) {
    const test = button("Send a test alert", "pill-button small", async () => {
      test.disabled = true;
      try {
        const reply = await postJson("/api/saved-action/Phone/test", { inputs: { text: "Test from Whenever.", title: "Whenever" } });
        flash(reply.ok ? "Sent. Check your phone." : `ntfy answered ${reply.status}.`);
      } catch (error) {
        flash(error.message);
      } finally {
        test.disabled = false;
      }
    }, "bell");
    row.append(test, button("Pair another phone", "pill-button small ghost", () => openPairing(), "phone"));
  } else {
    row.append(button("Pair your phone", "pill-button", () => openPairing(), "phone"));
  }
  card.append(row);
  $("phone-card").replaceChildren(card);
}

function inputsText(action) {
  return action.inputs.map((input) => input.name).join(", ") || "no inputs";
}

function typed(text) {
  // A test input of true, false or a number keeps its type, as rule code would send it.
  if (text === "true" || text === "false") return text === "true";
  return String(text).trim() !== "" && !isNaN(Number(text)) ? Number(text) : text;
}

function actionItem({ name, badgeIcon, hue, ready, stateText, lines, buttons = [] }) {
  const item = element("li", "action-item");
  const badge = element("span", "badge");
  badge.style.setProperty("--hue", `var(--hue-${hue})`);
  badge.append(icon(badgeIcon));
  const head = element("h3", null, name);
  const chip = element("span", "state " + (ready ? "ready" : "waiting"));
  chip.append(stateText);
  head.append(chip);
  item.append(badge, head);
  for (const line of lines) item.append(element("p", null, line));
  if (buttons.length) {
    const row = element("div", "action-buttons");
    row.append(...buttons);
    item.append(row);
  }
  return item;
}

function renderActions() {
  const actions = state?.actions || [];
  const controls = state?.controls || [];
  const key = JSON.stringify([actions, controls.map((c) => [c.name, c.moves, c.light]), !!state?.phone_paired]);
  if (key === actionsKey) return;
  actionsKey = key;
  const items = [];
  for (const action of actions) {
    const isPhone = action.kind === "phone";
    const ready = !action.missing.length && (!isPhone || !!state?.phone_paired);
    const buttons = [];
    if (isPhone && !ready) buttons.push(button("Pair", "pill-button small", () => openPairing(), "phone"));
    if (action.kind !== "phone" && ready) {
      buttons.push(button("Test it", "pill-button small ghost", async () => {
        const inputs = Object.fromEntries(action.inputs.map((i) => [i.name, typed(i.example || "")]));
        try {
          const reply = await postJson(`/api/saved-action/${encodeURIComponent(action.name)}/test`, { inputs });
          flash(reply.ok ? `${action.name} worked: status ${reply.status}.` : `${action.name} answered ${reply.status}.`);
        } catch (error) {
          flash(error.message);
        }
      }, "play"));
    }
    if (action.kind !== "phone") {
      buttons.push(button("Remove", "link-button", async () => {
        await fetch("/api/action/" + encodeURIComponent(action.name), { method: "DELETE" });
        refreshState();
      }));
    }
    items.push(actionItem({
      name: action.name,
      badgeIcon: action.kind === "phone" ? "phone" : "globe",
      hue: action.kind === "phone" ? "indigo" : "jade",
      ready,
      stateText: ready ? "Ready" : isPhone ? "Not paired" : `Needs ${action.missing.join(", ")}`,
      lines: [action.description, `${action.method} to ${action.host}. Inputs: ${inputsText(action)}.`],
      buttons,
    }));
  }
  for (const camera of controls) {
    const can = [camera.moves && "turns to a point or a thing", camera.light && "turns its light on and off"].filter(Boolean);
    items.push(actionItem({
      name: camera.name, badgeIcon: camera.light ? "bulb" : "move", hue: "plum", ready: true, stateText: "Camera",
      lines: [`Built in: it ${joinList(can)}.`],
    }));
  }
  $("actions").replaceChildren(...items);
}

function showDraft(draft) {
  const action = draft.action;
  const box = $("action-draft");
  const fields = action.inputs.map((input) => {
    const label = element("label", "draft-input");
    const field = element("input");
    field.value = input.example;
    field.dataset.name = input.name;
    label.append(element("span", null, `${input.name}: ${input.description}`), field);
    return label;
  });
  const request = element("pre", "draft-request",
    `${action.method} ${action.url}` +
    Object.entries(action.headers).map(([name, value]) => `\n${name}: ${value}`).join("") +
    (action.body_type !== "none" ? `\n\n${action.body}` : ""));
  const result = element("p", "note");
  const test = button("Test it", "pill-button ghost", async () => {
    const inputs = Object.fromEntries(fields.map((label) => {
      const field = label.querySelector("input");
      return [field.dataset.name, typed(field.value)];
    }));
    result.className = "note";
    result.textContent = "Sending the test request";
    try {
      const reply = await postJson(`/api/action/${draft.id}/test`, { inputs });
      result.className = reply.ok ? "note ok" : "note error";
      result.textContent = `Status ${reply.status}. ${reply.reply}`.slice(0, 300);
    } catch (error) {
      result.className = "note error";
      result.textContent = error.message;
    }
  }, "play");
  const save = button("Save action", "pill-button", async () => {
    try {
      const saved = await postJson(`/api/action/${draft.id}/save`);
      box.hidden = true;
      $("action-words").value = "";
      note("action-note", `${saved.saved} is ready. New Whenevers can use it.`, "ok");
      refreshState();
    } catch (error) {
      result.className = "note error";
      result.textContent = error.message;
    }
  }, "check");
  const row = element("div", "form-row");
  row.append(save, test);
  box.replaceChildren(element("b", null, action.name), element("p", null, action.description), request, ...fields);
  if (action.missing.length) {
    box.append(element("p", "note error", `Before a test, put ${action.missing.join(", ")} in actions.env. ` +
      action.secrets.map((secret) => `${secret.name}: ${secret.description}`).join(" ")));
  }
  box.append(row, result);
  box.hidden = false;
}

$("action-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const words = $("action-words").value.trim();
  if (!words) {
    note("action-note", "Say what the action should do.", "error");
    return;
  }
  $("action-add").disabled = true;
  $("action-draft").hidden = true;
  note("action-note", "Wonder is writing the request");
  try {
    const draft = await postJson("/api/action/draft", { words });
    note("action-note", `Check the request, test it, then save it. Wonder's draft cost $${draft.cost}.`);
    showDraft(draft);
  } catch (error) {
    note("action-note", error.message, "error");
  } finally {
    $("action-add").disabled = false;
  }
});

/* ---------- Live, focused on one Whenever ---------- */

// Which tile of the wall a Whenever watches, when it watches one camera.
function ruleTile(rule) {
  const [columns, rows] = state?.wall || [1, 1];
  if (!liveCamera || columns * rows <= 1 || rule.cameras?.length !== 1) return null;
  const index = (state?.cameras || []).indexOf(rule.cameras[0]);
  return index < 0 ? null : { columns, rows, col: index % columns, row: Math.floor(index / columns) };
}

function renderFocus(force) {
  const box = $("focus");
  const spot = $("tile-focus");
  const rule = focusId && rules.find((r) => r.id === focusId);
  const seen = rule && rule.sensors.some((s) => s.on);
  const key = JSON.stringify(rule ? [rule.id, rule.paused, seen, rule.fired, rule.cameras, state?.wall] : null);
  if (!force && key === keys.focus) return;
  keys.focus = key;
  if (!rule) {
    box.hidden = true;
    spot.hidden = true;
    return;
  }
  const card = element("div", "card focus-card" + (rule.paused ? " stopped" : ""));
  card.dataset.hue = hueOf(rule);
  const top = element("div", "card-top");
  const state_ = element("span", "focus-state" + (seen ? " seen" : ""));
  state_.append(element("i", "live-dot"), rule.paused ? "Stopped" : seen ? "Seen now" : `Looking · ${watchingText(rule).replace(/^Watching/, "on")}`);
  top.append(state_, toggleSwitch(rule));
  card.append(top, sentenceNode(rule.sentence || rule.name, "sentence-big focus-sentence"));
  for (const sensor of rule.sensors) card.append(sensorRow(rule, sensor));
  const foot = element("div", "card-foot");
  const open = element("a", null, "Open the Whenever");
  open.href = `#/whenever/${rule.id}`;
  const all = element("a", null, "Show all cameras");
  all.href = "#/live";
  foot.append(element("span", null, rule.fired ? `Ran ${rule.fired} ${rule.fired === 1 ? "time" : "times"}` : "Not run yet"), open, all);
  card.append(foot);
  box.replaceChildren(card);
  box.hidden = false;
  rule.sensors.forEach((sensor) => renderSensor(rule.id, sensor.id));
  // The camera it watches stays bright, and the rest of the wall dims.
  const tile = ruleTile(rule);
  spot.hidden = !tile;
  if (tile) {
    spot.style.left = `${(tile.col / tile.columns) * 100}%`;
    spot.style.top = `${(tile.row / tile.rows) * 100}%`;
    spot.style.width = `${100 / tile.columns}%`;
    spot.style.height = `${100 / tile.rows}%`;
    const wrap = $("frame-wrap");
    wrap.scrollTo({ left: Math.max(0, (tile.col / tile.columns) * $("frame").clientWidth - 12), behavior: "smooth" });
  }
}

/* ---------- Live strip ---------- */

function renderLiveStrip() {
  const key = JSON.stringify(rules.map((r) => [r.id, r.name, r.paused, r.sensors.some((s) => s.on)]));
  if (key === keys.strip) return;
  keys.strip = key;
  $("live-strip").replaceChildren(...rules.map((rule) => {
    const chip = element("a", "chip" + (rule.sensors.some((s) => s.on) ? " on" : ""));
    chip.href = `#/live?rule=${rule.id}`;
    const dot = element("i", "live-dot");
    chip.append(dot, rule.paused ? `${rule.name} (stopped)` : rule.name);
    return chip;
  }));
}

/* ---------- State ---------- */

async function refreshState() {
  let response;
  try {
    response = await fetch(`/api/state?after=${lastFeed}&results=${lastResult}`);
  } catch {
    return;
  }
  if (response.status === 401) return location.reload();
  state = await response.json();
  rules = state.rules;
  stopped = state.stopped;
  captureFps = state.capture_fps;
  holdCapture = state.hold_capture;
  if (state.live_camera && !liveCamera) showLiveCamera();
  if (liveCamera) {
    liveLost = !state.live_camera;
    setStatus(liveLost ? "The camera stream stopped" : liveLabel(state), liveLost ? "warn" : "live");
  }
  const spent = state.spend_usd;
  const limit = state.spend_limit_usd;
  $("spend-text").textContent = `$${spent.toFixed(2)} of $${limit} today`;
  $("spend-fill").style.width = `${Math.min(100, (spent / limit) * 100)}%`;
  $("s-spend").textContent = "$" + spent.toFixed(3);
  renderControls(state.controls);
  if (keys.wall !== JSON.stringify(state.wall)) {
    keys.wall = JSON.stringify(state.wall);
    shapeFrame();
  }
  renderActions();
  renderLiveStrip();
  if (screen === "live") renderFocus();
  if (screen === "whenevers") renderList();
  if (screen === "whenever") renderDetail(location.hash.split("/")[2]);
  if (screen === "create") renderCreate();
  if (screen === "actions") renderPhoneCard();
  handleResults(state.results);
  for (const item of state.feed) {
    lastFeed = item.id;
    if (item.kind === "outline") {
      showOutline(item);
    } else if (item.kind === "draw") {
      // After a reload, old draws stay in the list and do not freeze the view.
      if (!firstState) showDraw(item);
    } else if ((item.kind === "notify" || item.kind === "say") && !firstState) {
      flash(`${item.rule}: ${item.text}`);
    } else if (item.kind === "error" && !firstState) {
      flash(`${item.rule}: ${item.text.slice(0, 140)}`);
    }
  }
  if (stopped) setStatus("Stopped at the spend limit", "warn");
  if (firstState) {
    firstState = false;
    route();
  }
  loadJournal();
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
    if (r.error) flash(r.error);
    if (r.skipped) continue;
    record(r);
    renderSensor(r.rule, r.sensor);
    if (r.sam_s != null) $("s-sam").textContent = seconds(r.sam_s);
    if (r.spark_s != null) $("s-spark").textContent = seconds(r.spark_s);
  }
}

/* ---------- Capture ---------- */

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
  if (!rules.length) {
    setStatus(source === "clip" ? "Demo clip" : "Camera on", "live");
    return;
  }

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
  if (!video.videoWidth || recording) {
    flash("The camera is not ready yet.");
    return;
  }
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
  note("form-note", `Recorded ${event.frames.length} frames. Wonder tests the rule on them.`);
}

/* ---------- Camera sources ---------- */

function liveLabel(s) {
  const count = s.cameras?.length || 1;
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

async function startVideo(kind) {
  if (kind === "clip") {
    if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
    video.srcObject = null;
    video.src = "/testdata/clip.webm";
    $("frame").classList.remove("mirror");
    $("notice").hidden = true;
    return;
  }
  if (liveCamera) return;
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
    video.removeAttribute("src");
    video.srcObject = stream;
    $("frame").classList.add("mirror");
    $("notice").hidden = true;
  } catch (error) {
    setStatus("No camera", "warn");
    showNotice('This page cannot use the camera. Allow camera access and reload, or <a href="#/start">choose the demo clip</a>.');
  }
}

function chooseSource(kind) {
  source = kind;
  try { localStorage.setItem("whenever-source", kind); } catch {}
  startVideo(kind);
  location.hash = rules.length ? "#/whenevers" : "#/create";
}

document.querySelectorAll("[data-source]").forEach((b) => b.addEventListener("click", () => chooseSource(b.dataset.source)));

/* ---------- Keys and wiring ---------- */

$("frame").addEventListener("click", pointAt);
$("talk").addEventListener("pointerdown", startTalk);
$("talk").addEventListener("pointerup", stopTalk);
$("talk").addEventListener("pointerleave", () => { if (talkHeld) stopTalk(); });

const typing = (target) => target.closest("textarea, input, button, dialog, select");
window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || typing(event.target) || screen !== "create") return;
  event.preventDefault();
  startTalk();
});
window.addEventListener("keyup", (event) => {
  if (event.code === "Space" && talkHeld) stopTalk();
});
$("evidence-close").addEventListener("click", () => $("evidence").close());
$("viewer-close").addEventListener("click", () => $("viewer").close());
for (const dialog of [$("evidence"), $("viewer")]) {
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}

// Enter writes nothing by mistake: it moves to the check, and Shift+Enter makes a new line.
$("sentence").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("rule-form").requestSubmit();
  }
});

/* ---------- Start ---------- */

paintIcons();
try { source = params.has("clip") ? "clip" : localStorage.getItem("whenever-source"); } catch { source = null; }
if (params.has("clip")) source = "clip";

fetch("/api/claim", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ page: pageId }),
});
if (source) startVideo(source);
refreshState();
setInterval(tick, TICK_MS);
setInterval(refreshState, POLL_MS);
setInterval(() => { if (screen === "whenevers") renderList(); }, 30000);
