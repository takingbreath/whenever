const FRAME_WIDTH = 640;     // small frames can hide what a person holds
const SHARP_WIDTH = 1280;    // a larger copy for rule code and saved pictures
const SMALL_W = 64;
const SMALL_H = 36;
const TICK_MS = 80;
const CELL = 8;              // compare blocks, so a small change in one place still counts
const CHANGE_LEVEL = 5;      // mean grey difference (0-255) in a block that counts as a change
const SETTLE_MS = 2500;      // keep checking this long after the last change
const IDLE_CHECK_MS = 60000; // check a still picture once a minute: movement sends frames at once
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
// A phone in camera mode renames itself camera-..., so the server lets it keep the camera while it sends.
let pageId = crypto.randomUUID();
let cameraMode = false;
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
  "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  chart: '<path d="M4 20V11M10 20V5M16 20v-7M21 20H3"/>',
  send: '<path d="M21 3L3 10.5l7.2 3.3L13.5 21 21 3z"/><path d="M10.2 13.8L21 3"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M4 7.5l8 5.5 8-5.5"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
  share: '<path d="M12 3v12M8 7l4-4 4 4"/><path d="M6 11H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1"/>',
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

// A short message. With href, it also links to the place that explains it.
function flash(text, href, linkText = "See why") {
  const el = $("flash");
  el.replaceChildren(element("span", null, text));
  if (href) {
    const link = element("a", "toast-link", linkText);
    link.href = href;
    link.addEventListener("click", () => { el.hidden = true; });
    el.append(link);
  }
  el.hidden = false;
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.hidden = true; }, href ? 7000 : 3600);
}

// Errors from the camera engine reach the person as a short message.
function log(text) {
  if (text) flash(text);
}

/* ---------- Problems ---------- */

// Most trouble passes by itself: a missed check is followed by the next one. Only a problem that needs
// the person makes a message, once. Activity > Problems keeps everything, with the technical detail.
let problemSeen = null;
let problemsData = null;
let problemsAt = 0;
let problemFilter = "all";
const LEVEL_WORD = { error: "Needs you", warn: "Warning", note: "Note" };

function openProblems(rule) {
  return (state?.problems || []).filter((g) => !rule || g.rule_id === rule.id);
}

function problemSignals() {
  const open = openProblems();
  const newest = Math.max(0, ...open.map((g) => g.id));
  if (problemSeen === null) problemSeen = newest;
  const fresh = open.filter((g) => g.id > problemSeen && g.level === "error");
  if (fresh.length) flash(fresh[fresh.length - 1].title, "#/activity/problems");
  problemSeen = Math.max(problemSeen, newest);
  const count = $("problems-count");
  count.textContent = open.length ? String(open.length) : "";
  count.hidden = !open.length;
}

async function loadProblems(force) {
  if (!force && Date.now() - problemsAt < JOURNAL_MS) return;
  problemsAt = Date.now();
  try {
    problemsData = await (await fetch("/api/problems")).json();
  } catch {
    return;
  }
  renderProblems();
}

function timeSpan(g) {
  return g.count > 1 && g.last - g.first > 60 ? `${clock(g.first)} to ${clock(g.last)}` : `${dayName(g.last)}, ${clock(g.last)}`;
}

function healthCard(rows) {
  const card = element("section", "health");
  const head = element("div", "health-head");
  head.append(element("h2", null, "How the models answer"), element("span", null, "Last hour"));
  card.append(head);
  if (!rows.length) {
    card.append(element("p", "health-say", "No live checks in the last hour."));
    return card;
  }
  const worst = rows.map((r) => ({ ...r, share: r.failed / r.calls })).sort((a, b) => b.share - a.share)[0];
  const slow = worst.share > 0.05;
  const say = slow
    ? `${worst.model} is slow right now: ${worst.failed} of ${worst.calls} checks got no answer in time. One missed check changes nothing, because the next check looks again.`
    : worst.failed ? `All models answer. ${worst.failed} of ${worst.calls} ${worst.model} checks got no answer in time, which is normal.`
      : "All models answer normally.";
  card.append(element("p", "health-say" + (slow ? " slow" : ""), say));
  const table = element("table", "health-table");
  const headRow = element("tr");
  for (const word of ["Model", "Checks", "Missed", "Typical", "Slowest"]) headRow.append(element("th", null, word));
  const thead = element("thead");
  thead.append(headRow);
  const tbody = element("tbody");
  for (const r of rows) {
    const row = element("tr");
    const missed = r.failed ? `${r.failed} (${Math.round((r.failed / r.calls) * 100)}%)` : "0";
    for (const cell of [r.model, String(r.calls), missed, r.median_s != null ? `${r.median_s} s` : "–", r.slowest_s != null ? `${r.slowest_s} s` : "–"]) {
      row.append(element("td", null, cell));
    }
    tbody.append(row);
  }
  table.append(thead, tbody);
  card.append(table);
  return card;
}

function problemNode(g, where) {
  const item = element("details", `problem level-${g.level}`);
  item.dataset.id = `${where}-${g.id}`;
  const summary = element("summary");
  const words = element("span", "problem-words");
  const meta = [g.source, timeSpan(g), g.count > 1 ? `${g.count} times` : "", !g.open && g.level !== "note" ? "passed" : ""];
  words.append(element("b", null, g.title), element("small", null, meta.filter(Boolean).join(" · ")));
  summary.append(element("i", "level-dot"), words, element("span", "level-word", LEVEL_WORD[g.level] || "Note"));
  const body = element("div", "problem-body");
  if (g.hint) body.append(element("p", null, g.hint));
  if (g.detail) body.append(element("pre", "code-block", g.detail));
  const row = element("div", "problem-buttons");
  row.append(button("Copy details", "pill-button small ghost", async (event) => {
    const b = event.currentTarget;
    await navigator.clipboard.writeText(JSON.stringify(g, null, 1)).catch(() => {});
    b.lastChild.textContent = "Copied";
  }, "copy"));
  const rule = rules.find((r) => r.id === g.rule_id);
  if (rule) {
    const open = element("a", "pill-button small ghost", "Open the Whenever");
    open.href = `#/whenever/${rule.id}`;
    row.append(open);
  }
  if (g.open) {
    row.append(button("Mark as read", "link-button", async () => {
      await postJson(`/api/problems/${g.id}/dismiss`).catch(() => {});
      await refreshState();
      loadProblems(true);
    }));
  }
  body.append(row);
  item.append(summary, body);
  return item;
}

function renderProblems() {
  if (!problemsData) return;
  const key = JSON.stringify([problemsData, problemFilter, rules.map((r) => r.id)]);
  if (key === keys.problems) return;
  keys.problems = key;
  const box = $("problems");
  const opened = new Set([...box.querySelectorAll("details[open]")].map((d) => d.dataset.id));
  const groups = problemsData.groups || [];
  const open = groups.filter((g) => g.open);

  const needs = element("section", "needs");
  needs.append(element("h2", "section-title", "Needs you"));
  if (open.length) {
    const list = element("div", "problem-list");
    list.append(...open.map((g) => problemNode(g, "open")));
    needs.append(list);
  } else {
    const good = element("p", "all-good");
    good.append(icon("check"), element("span", null, "Nothing needs you right now."));
    needs.append(good);
  }

  const log = element("section", "problem-log");
  const chips = element("div", "chips filter-chips");
  for (const [kind, label] of [["all", "Everything"], ["warn", "Warnings and errors"], ["note", "Notes"]]) {
    chips.append(button(label, "chip" + (problemFilter === kind ? " on" : ""), () => { problemFilter = kind; renderProblems(); }));
  }
  const shown = groups.filter((g) => problemFilter === "all" || (problemFilter === "note" ? g.level === "note" : g.level !== "note"));
  const list = element("div", "problem-list");
  list.append(...shown.slice(0, 150).map((g) => problemNode(g, "log")));
  if (!shown.length) list.append(element("p", "note", "Nothing here."));
  const tools = element("div", "log-tools");
  tools.append(button("Copy the whole log", "pill-button small ghost", async (event) => {
    const b = event.currentTarget;
    await navigator.clipboard.writeText(JSON.stringify(problemsData, null, 1)).catch(() => {});
    b.lastChild.textContent = "Copied";
  }, "copy"));
  const raw = element("a", "link-button", "Open the raw log");
  raw.href = "/api/problems";
  raw.target = "_blank";
  raw.rel = "noopener";
  tools.append(raw);
  log.append(element("h2", "section-title", "Everything that went wrong"),
    element("p", "note", "Repeats are grouped. Notes are small things that fixed themselves, such as one missed check."),
    chips, list, tools);

  box.replaceChildren(healthCard(problemsData.health || []), needs, log);
  box.querySelectorAll("details").forEach((d) => { if (opened.has(d.dataset.id)) d.open = true; });
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
  // The tag form tags the person in this picture: the marked one, or else the largest.
  $("viewer").dataset.url = url;
  $("viewer").dataset.box = box && box.w ? JSON.stringify([box.x1 / box.w, box.y1 / box.h, box.x2 / box.w, box.y2 / box.h]) : "";
  $("tag-note").textContent = "";
  $("tag-name").value = "";
  roleChips($("tag-role"), "");
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

$("tag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("tag-name").value.trim();
  if (!name) {
    $("tag-note").textContent = "Write a name or a label first.";
    return;
  }
  const viewer = $("viewer");
  $("tag-send").disabled = true;
  $("tag-note").textContent = "Reading the face…";
  try {
    const reply = await postJson("/api/people", {
      picture: viewer.dataset.url, name, box: viewer.dataset.box ? JSON.parse(viewer.dataset.box) : null,
      role: $("tag-role").dataset.role || "",
    });
    const person = reply.person;
    $("tag-note").textContent = person.faces
      ? `Tagged ${person.name}. Whenever will know them by face from now on.`
      : `Tagged ${person.name} by look only, for today. A picture with a clear face lets Whenever know them on other days.`;
    renderPeople();
    if (screen === "actions") renderFacesToday();
  } catch (error) {
    $("tag-note").textContent = error.message;
  } finally {
    $("tag-send").disabled = false;
  }
});

async function renderPeople() {
  let data;
  try {
    data = await (await fetch("/api/people")).json();
  } catch (error) {
    return;
  }
  const people = data.people || [];
  $("people-names").replaceChildren(...people.map((p) => Object.assign(document.createElement("option"), { value: p.name })));
  const card = element("section", "card phone-card people-card");
  card.dataset.hue = "peacock";
  if (!data.ready) {
    card.append(element("h2", null, "Know people again"),
      element("p", null, "The face models are not installed on this server yet."));
    $("people-card").replaceChildren(card);
    return;
  }
  if (!people.length) {
    card.append(element("h2", null, "Know people again"),
      element("p", null, "Open any picture of a person, in Activity or in an alert, and tag them with a name or a label. Whenever then knows them by face on other days, and your Whenevers can follow them by name, such as “tell me when Ravi arrives”."));
    $("people-card").replaceChildren(card);
    return;
  }
  card.append(element("h2", null, "Tagged people"),
    element("p", null, "Known by face across days and weeks, and by look within a day. Your Whenevers can name them."));
  const list = element("ul", "places");
  for (const person of people) {
    const item = element("li", "place");
    const badge = element("span", "badge person-face");
    if (person.picture) {
      const img = document.createElement("img");
      img.src = `/people-file/${person.id}/${person.picture}`;
      img.alt = person.name;
      badge.append(img);
    } else {
      badge.append(icon("eye"));
    }
    // The name opens the person's page: their visits, sightings and role.
    const words = element("a", "place-words person-link");
    words.href = `#/person/${encodeURIComponent(person.id)}`;
    const seen = person.seen
      ? `Seen ${person.times_seen} ${person.times_seen === 1 ? "time" : "times"}, last ${dayName(person.seen.t).replace(/^(Today|Yesterday)$/, (d) => d.toLowerCase())} at ${clock(person.seen.t)}${person.seen.camera ? ` on ${person.seen.camera}` : ""}`
      : "Not seen since the tag";
    const name = element("b", null, person.name);
    if (person.role) name.append(roleBadge(person.role));
    words.append(name, element("small", null, [seen, person.faces ? "" : "no face yet"].filter(Boolean).join(" · ")));
    const rename = button("Rename", "pill-button small ghost", async () => {
      const name = prompt("A new name or label", person.name);
      if (!name || name.trim() === person.name) return;
      try {
        await postJson(`/api/people/${person.id}`, { name });
        renderPeople();
      } catch (error) {
        flash(error.message);
      }
    });
    let armed = false;
    const remove = button("Forget", "link-button", async () => {
      if (!armed) {
        armed = true;
        remove.textContent = "Tap again to forget";
        setTimeout(() => { armed = false; remove.textContent = "Forget"; }, 3500);
        return;
      }
      await fetch(`/api/people/${person.id}`, { method: "DELETE" });
      renderPeople();
    });
    const buttons = element("span", "place-buttons");
    buttons.append(rename, remove);
    item.append(badge, words, buttons);
    list.append(item);
  }
  card.append(list);
  $("people-card").replaceChildren(card);
}

/* ---------- Faces seen today, finding a person, and one person's page ---------- */

// Each person or visitor seen today, once. A tap names a visitor or opens a person's page. Two ticked
// faces of one person become one with Same person.
const facesPicked = new Set();
let facesData = [];
let naming = null;

function groupName(group) {
  if (group.name) return group.name;
  const n = String(group.id ?? "").match(/\d+/);
  return n ? `Visitor ${n[0]}` : "Visitor";
}

async function renderFacesToday() {
  try {
    const response = await fetch("/api/people/today");
    if (!response.ok) throw new Error();
    facesData = (await response.json()).groups || [];
  } catch {
    $("faces-today").hidden = true;
    return;
  }
  for (const id of [...facesPicked]) if (!facesData.some((g) => g.id === id)) facesPicked.delete(id);
  drawFaces();
}

function drawFaces() {
  const box = $("faces-today");
  box.hidden = false;
  const head = element("div", "faces-head");
  head.append(element("h3", null, "Faces seen today"), element("span", "faces-count", facesData.length ? plural(facesData.length, "face") : ""));
  if (!facesData.length) {
    box.replaceChildren(head, element("p", "note", "Nobody yet today. Faces show up here as the cameras see them."));
    return;
  }
  const grid = element("ul", "faces-grid");
  for (const group of facesData) {
    const picked = facesPicked.has(group.id);
    const item = element("li", "face-tile" + (picked ? " picked" : ""));
    const open = element("button", "face-open");
    open.type = "button";
    const img = element("img");
    img.src = group.face;
    img.alt = "";
    img.loading = "lazy";
    const name = element("b", null, groupName(group));
    if (group.role) name.append(roleBadge(group.role));
    const span = group.last - group.first > 60 ? `${clock(group.first)} to ${clock(group.last)}` : clock(group.last);
    open.append(img, name, element("small", null, `${plural(group.count, "time")} · ${span}`),
      element("small", "face-where", joinList(group.cameras || [])));
    open.setAttribute("aria-label", `${groupName(group)}, seen ${plural(group.count, "time")}, ${span}. `
      + (group.kind === "person" ? "Open their page." : "Name them."));
    open.addEventListener("click", () => {
      if (group.kind === "person") location.hash = `#/person/${encodeURIComponent(group.id)}`;
      else openNameSheet(group);
    });
    const pick = element("button", "face-pick");
    pick.type = "button";
    pick.setAttribute("aria-pressed", String(picked));
    pick.setAttribute("aria-label", `Tick ${groupName(group)}, to join with another face of the same person`);
    pick.append(icon("check"));
    pick.addEventListener("click", () => {
      if (picked) facesPicked.delete(group.id);
      else {
        facesPicked.add(group.id);
        if (facesPicked.size > 2) facesPicked.delete(facesPicked.values().next().value);
      }
      drawFaces();
    });
    item.append(open, pick);
    grid.append(item);
  }
  const bar = element("div", "faces-bar");
  const chosen = [...facesPicked].map((id) => facesData.find((g) => g.id === id)).filter(Boolean);
  if (chosen.length === 2) {
    const [a, b] = chosen;
    // A tagged person keeps their name. Between two visitors, the one seen more often stays.
    const into = a.kind === "person" ? a : b.kind === "person" ? b : a.count >= b.count ? a : b;
    const from = into === a ? b : a;
    bar.append(element("span", null, `${groupName(from)} joins ${groupName(into)}`),
      button("Same person", "pill-button small", async () => {
        try {
          await postJson("/api/people/merge", { from: from.id, into: into.id });
          facesPicked.clear();
          flash(`Joined as ${groupName(into)}.`);
          renderFacesToday();
          renderPeople();
        } catch (error) {
          flash(error.message);
        }
      }, "check"),
      button("Clear", "link-button", () => {
        facesPicked.clear();
        drawFaces();
      }));
  } else {
    bar.append(element("span", null, chosen.length ? "Tick one more face of the same person."
      : "Tap a face to name it. Tick two faces of one person to join them."));
  }
  box.replaceChildren(head, grid, bar);
}

function openNameSheet(group) {
  naming = group;
  $("name-face").src = group.face || "";
  $("name-face").alt = groupName(group);
  $("name-title").textContent = group.name ? `Rename ${group.name}` : `Who is ${groupName(group)}?`;
  $("name-seen").textContent = `Seen ${plural(group.count, "time")} today, ${clock(group.first)} to ${clock(group.last)}`
    + `${group.cameras?.length ? `, on ${joinList(group.cameras)}` : ""}.`;
  $("name-input").value = group.name || "";
  $("name-note").textContent = "";
  roleChips($("name-role"), group.role || "");
  $("name-sheet").showModal();
}

$("name-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("name-input").value.trim();
  if (!name) {
    $("name-note").textContent = "Write a name or a label first.";
    $("name-input").focus();
    return;
  }
  $("name-save").disabled = true;
  try {
    const reply = await postJson("/api/people", { visitor: naming.id, name, role: $("name-role").dataset.role || "" });
    $("name-sheet").close();
    flash(`Named ${reply.person?.name || name}. Whenever knows them from now on.`);
    renderFacesToday();
    renderPeople();
  } catch (error) {
    $("name-note").textContent = error.message;
  } finally {
    $("name-save").disabled = false;
  }
});
$("name-close").addEventListener("click", () => $("name-sheet").close());
$("name-sheet").addEventListener("click", (event) => { if (event.target === $("name-sheet")) $("name-sheet").close(); });

// Find a person by words, such as "man in a white checked shirt". Each crop opens its frame in the
// viewer, where the person can be tagged.
$("people-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const words_ = $("people-words").value.trim();
  if (!words_) return $("people-words").focus();
  const note = $("people-note");
  $("people-find").disabled = true;
  note.className = "note";
  note.textContent = "Looking through the people of the last day";
  $("people-results").replaceChildren();
  try {
    const response = await fetch(`/api/people/search?q=${encodeURIComponent(words_)}&hours=24`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The search did not work. Try again.");
    const results = data.results || [];
    note.textContent = results.length ? `${plural(results.length, "person", "people")} like that, the closest first. Tap one to see the picture and tag them.`
      : "Nobody like that in the last day.";
    $("people-results").replaceChildren(...results.map((r) => {
      const title = [r.name, r.camera, `${dayName(r.t) === "Today" ? "" : `${dayName(r.t)} `}${clock(r.t)}`].filter(Boolean).join(", ");
      const open = element("button", "result-tile");
      open.type = "button";
      open.setAttribute("aria-label", `Open: ${title}`);
      const img = element("img");
      img.src = r.picture;
      img.alt = "";
      img.loading = "lazy";
      const said = element("span", "result-words");
      said.append(element("b", null, r.name || clock(r.t)), element("small", null, r.name ? `${clock(r.t)} · ${r.camera}` : r.camera || ""));
      open.append(img, said);
      open.addEventListener("click", () => openFrame(r.picture, null, title));
      return open;
    }));
  } catch (error) {
    note.className = "note error";
    note.textContent = error.message;
  } finally {
    $("people-find").disabled = false;
  }
});

// One person: their face and role, the days they came in the last 30, how long they stay, where they
// were seen last, and each sighting, which opens its frame.
async function renderPersonPage(id) {
  const box = $("person-page");
  if (box.dataset.id !== id) box.replaceChildren(element("p", "note", "Loading"));
  box.dataset.id = id;
  let data;
  try {
    const response = await fetch(`/api/people/${encodeURIComponent(id)}/page`);
    data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "There is no such person.");
  } catch (error) {
    const empty = element("div", "empty");
    empty.append(element("h2", null, "Not found"), element("p", null, error.message));
    box.replaceChildren(empty);
    return;
  }
  if (screen !== "person" || box.dataset.id !== id) return;
  const person = data.person || {};
  const head = element("header", "person-head");
  const face = element("img", "person-face-big");
  face.src = person.face || (person.picture ? `/people-file/${person.id}/${person.picture}` : "");
  face.alt = person.name || "";
  const side = element("div", "person-side");
  const title = element("h1", null, person.name || "Someone");
  const roles = roleChips(element("div", "chips role-chips"), person.role || "", async (role) => {
    try {
      await postJson(`/api/people/${encodeURIComponent(id)}`, { role });
      flash(role ? `${person.name} is ${ROLE_WORD[role]} now.` : `${person.name} has no role now.`);
      renderPeople();
    } catch (error) {
      flash(error.message);
    }
  });
  const tools = element("div", "person-tools");
  tools.append(button("Rename", "pill-button small ghost", async () => {
    const name = prompt("A new name or label", person.name);
    if (!name || name.trim() === person.name) return;
    try {
      await postJson(`/api/people/${encodeURIComponent(id)}`, { name });
      renderPersonPage(id);
      renderPeople();
    } catch (error) {
      flash(error.message);
    }
  }, "pencil"));
  let armed = false;
  const forget = button("Forget", "link-button", async () => {
    if (!armed) {
      armed = true;
      forget.textContent = "Tap again to forget";
      setTimeout(() => { armed = false; forget.textContent = "Forget"; }, 3500);
      return;
    }
    await fetch(`/api/people/${encodeURIComponent(id)}`, { method: "DELETE" });
    flash(`Whenever forgot ${person.name}.`);
    location.hash = "#/actions";
  });
  tools.append(forget);
  side.append(title, roles, tools);
  head.append(face, side);

  const visits = data.visits || [];
  const days = visits.filter((v) => v.count > 0).length;
  const numbers = element("div", "person-numbers");
  const tile = (value, label) => {
    const t = element("div", "person-number");
    t.append(element("b", null, value), element("span", null, label));
    return t;
  };
  numbers.append(tile(String(days), `${days === 1 ? "day" : "days"} here, last 30`),
    tile(data.avg_stay_min != null ? stayWords(data.avg_stay_min * 60) : "–", "average stay"),
    tile(data.last ? ago(data.last.t) : "–", data.last?.camera ? `last seen, ${data.last.camera}` : "last seen"));

  // The last 30 days as a calendar, Monday first, darker on longer visits.
  const calendar = element("div", "visit-calendar");
  calendar.setAttribute("role", "img");
  calendar.setAttribute("aria-label", `Came in on ${plural(days, "day")} of the last 30`);
  for (const letter of ["M", "T", "W", "T", "F", "S", "S"]) calendar.append(element("span", "visit-weekday", letter));
  const first = visits.length ? new Date(`${visits[0].day}T12:00:00`) : null;
  for (let i = 0; first && i < (first.getDay() + 6) % 7; i++) calendar.append(element("span", "visit-day blank"));
  for (const v of visits) {
    const when = new Date(`${v.day}T12:00:00`);
    const cell = element("span", "visit-day");
    cell.dataset.level = String(!v.count ? 0 : v.minutes < 20 ? 1 : v.minutes < 45 ? 2 : 3);
    if (when.toDateString() === new Date().toDateString()) cell.classList.add("today");
    cell.title = `${when.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}: `
      + (v.count ? `${plural(v.count, "visit")}, ${v.minutes} min` : "not seen");
    cell.textContent = String(when.getDate());
    calendar.append(cell);
  }

  const nodes = [head, numbers, element("h2", "section-title small", "Visits, last 30 days"), calendar];
  if (data.last?.picture) {
    const last = element("button", "person-last");
    last.type = "button";
    const img = element("img");
    img.src = data.last.picture;
    img.alt = `${person.name}, last seen`;
    const title_ = `${data.last.camera || ""}, ${dayName(data.last.t)} ${clock(data.last.t)}`.replace(/^, /, "");
    last.append(img, element("span", null, `Last seen · ${title_}`));
    last.addEventListener("click", () => openFrame(data.last.picture, null, `${person.name}, ${title_}`));
    nodes.push(element("h2", "section-title small", "Last seen"), last);
  }
  const sightings = data.sightings || [];
  if (sightings.length) {
    const list = element("ul", "sightings");
    for (const s of sightings) {
      const row = element("li");
      const open = element("button", "sighting");
      open.type = "button";
      const img = element("img");
      img.src = s.picture;
      img.alt = "";
      img.loading = "lazy";
      const said = element("span", "place-words");
      const when = `${dayName(s.t)}, ${clock(s.t)}`;
      said.append(element("b", null, when), element("small", null,
        [s.camera, s.sure != null ? `${Math.round(s.sure * 100)}% sure${s.by ? ` by ${s.by}` : ""}` : ""].filter(Boolean).join(" · ")));
      open.append(img, said, icon("arrow"));
      open.addEventListener("click", () => openFrame(s.picture, s.box || null, `${person.name}, ${s.camera || ""} ${when}`.trim()));
      row.append(open);
      list.append(row);
    }
    nodes.push(element("h2", "section-title small", "Sightings"), list);
  }
  box.replaceChildren(...nodes);
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
  if (!answers.length && check.target) {
    const count = (check.boxes || []).length;
    const sure = check.conf == null ? "" : `, ${Math.round(check.conf * 100)}% sure`;
    facts.push({ pass: count > 0, text: count ? `Found ${count === 1 ? article(check.target) : count} ${check.target}${sure}`
      : `Found no ${check.target}` });
  }
  return facts;
}

async function showEvidence(id, t) {
  const reply = await fetch(`/api/check/${encodeURIComponent(id)}${t ? `?t=${t}` : ""}`).catch(() => null);
  const check = reply && reply.ok ? await reply.json() : null;
  const body = $("evidence-body");
  body.replaceChildren();
  if (!check) {
    body.append(element("p", "sentence", "The proof of this one is no longer kept. Whenever keeps proof for a while, "
      + "and the oldest goes first."));
    $("evidence").showModal();
    return;
  }
  // A check that saw only the cameras that matter keeps that picture: the Why view shows what the model saw.
  const framePath = (name) => `/evidence/${check.session}/${name}${check.focus ? "?view=focus" : ""}`;

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

  // An outline is drawn on the whole wall, so a picture of only some cameras goes without it.
  if (check.focus) return;
  const outline = check.outline || (await (await fetch(`/api/check/${encodeURIComponent(id)}/outline`, { method: "POST" })
    .catch(() => null))?.json().catch(() => ({})))?.outline;
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

function pointedWhere(value) {
  const views = [value?.camera, ...(value?.also || [])].filter(Boolean);
  return views.length ? `seen in ${joinList(views)}` : "";
}

function setPointed(value) {
  pointed = value;
  $("pointed-box").hidden = !value;
  $("pointed-name").textContent = value?.noun || "";
  $("pointed-where").textContent = value ? pointedWhere(value) : "";
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
  const wrap = $("frame-wrap");
  const ratio = video.videoWidth / video.videoHeight;
  const columns = liveCamera ? (state?.wall?.[0] || 1) : 1;
  frame.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
  // One camera shown large: the whole wall grows behind a window the size of one tile. A click still
  // lands on the wall, so pointing, aiming and outlines keep the wall coordinates the server expects.
  const tile = shownTile();
  wrap.classList.toggle("zoomed", !!tile);
  if (tile) {
    const tileRatio = (ratio * tile.rows) / tile.columns;
    wrap.style.aspectRatio = String(tileRatio);
    wrap.style.maxWidth = `calc(64vh * ${tileRatio.toFixed(4)})`;
    frame.style.width = `${tile.columns * 100}%`;
    frame.style.maxWidth = "none";
    frame.style.transform = `translate(${(-100 * tile.col) / tile.columns}%, ${(-100 * tile.row) / tile.rows}%)`;
    return;
  }
  wrap.style.aspectRatio = "";
  wrap.style.maxWidth = "";
  frame.style.transform = "";
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
  openPointSheet(spot);
}

/* ---------- Point at a thing ---------- */

// A still picture of every camera, at full size. The person taps a thing in it, checks the name,
// and stays where they were: in Create, or on Live.
let pointShot = null;
let pointRun = 0;

function snapshot() {
  const source = liveCamera && liveCanvas.width ? liveCanvas : video;
  const width = source.videoWidth || source.width;
  const height = source.videoHeight || source.height;
  if (!width || !height) return null;
  const scale = Math.min(1, 1920 / width);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return { url: canvas.toDataURL("image/jpeg", 0.88), w: canvas.width, h: canvas.height };
}

function resetPointSheet(text = "Tap the thing you mean.") {
  pointRun++;
  $("point-say").textContent = text;
  $("point-result").replaceChildren();
  $("point-tag").hidden = true;
  const ink = $("point-ink");
  ink.getContext("2d").clearRect(0, 0, ink.width, ink.height);
}

// "create" points for a new Whenever. "ask" points at a thing to ask about.
let pointMode = "create";

function openPointSheet(spot, mode = "create") {
  pointMode = mode;
  const shot = snapshot();
  if (!shot) return flash("The camera is not ready yet.");
  pointShot = shot;
  $("point-image").src = shot.url;
  resetPointSheet();
  if (!$("point-sheet").open) $("point-sheet").showModal();
  if (spot) $("point-image").decode().catch(() => {}).then(() => pointHere(spot));
}

function pointTag(text, box) {
  const tag = $("point-tag");
  tag.textContent = text;
  tag.hidden = false;
  tag.style.left = box ? `${(box.x1 / box.w) * 100}%` : "50%";
  tag.style.top = box ? `${(box.y1 / box.h) * 100}%` : "50%";
  tag.classList.toggle("centered", !box);
}

async function pointHere(spot) {
  resetPointSheet("Looking at what you tapped.");
  const run = pointRun;
  pointTag("Looking", null);
  try {
    const result = await postJson("/api/point", {
      frame: pointShot.url.split(",")[1],
      x: Math.round(spot.x * (pointShot.w - 1)),
      y: Math.round(spot.y * (pointShot.h - 1)),
      w: pointShot.w,
      h: pointShot.h,
    });
    if (run !== pointRun) return;
    if (!result.found) {
      pointTag("Nothing there", null);
      $("point-say").textContent = "SAM found no single thing there. Tap the middle of the thing.";
      return;
    }
    const image = $("point-image");
    paintOutline($("point-ink"), result.box, { left: 0, top: 0, width: image.clientWidth, height: image.clientHeight });
    pointTag("Naming it", result.box);
    const named = await postJson(`/api/point/${result.id}/name`);
    if (run !== pointRun) return;
    pointTag(named.noun || "What is it?", result.box);
    $("point-say").textContent = result.camera
      ? `You tapped something in ${result.camera}${result.kind === "moving" ? ", a camera that moves. Rules find it there by its look" : ""}.`
      : "You tapped this.";
    showFound(result, named, run);
  } catch (error) {
    if (run !== pointRun) return;
    $("point-tag").hidden = true;
    $("point-say").textContent = error.message;
  }
}

function alsoChoice(item) {
  const choice = element("label", "also-choice");
  const box = element("input");
  box.type = "checkbox";
  box.checked = true;
  box.value = item.camera;
  const picture = element("img");
  picture.src = `data:image/jpeg;base64,${item.crop}`;
  picture.alt = `The same thing in ${item.camera}`;
  const words = element("span", "also-words");
  words.append(element("span", null, item.camera));
  if (item.kind === "moving") words.append(element("small", null, "Moves, so found by its look"));
  choice.append(box, picture, words);
  return choice;
}

async function lookElsewhere(id, run, area) {
  if ((state?.cameras?.length || 0) < 2) return area.replaceChildren();
  area.replaceChildren(waitingNode("Looking for it in the other cameras"));
  try {
    const reply = await postJson(`/api/point/${id}/elsewhere`);
    if (run !== pointRun) return;
    if (!reply.also.length) {
      area.replaceChildren(element("p", "note", "No other camera shows it right now."));
      return;
    }
    const list = element("div", "also-list");
    list.append(...reply.also.map(alsoChoice));
    area.replaceChildren(element("b", "point-label", "Also seen in"), list,
      element("p", "note", "The rule watches it from each view that stays ticked. Untick a view that shows something else."));
  } catch (error) {
    if (run !== pointRun) return;
    area.replaceChildren(element("p", "note", error.message));
  }
}

function showFound(result, named, run) {
  const card = element("div", "point-found");
  if (named.crop) {
    const crop = element("img", "point-crop");
    crop.src = `data:image/jpeg;base64,${named.crop}`;
    crop.alt = "A close view of what you tapped";
    card.append(crop);
  }
  const words = element("div", "point-words");
  const nameLabel = element("label", "point-label", "What is it?");
  const name = element("input", "point-input");
  name.id = "point-name";
  name.value = named.noun || "";
  name.maxLength = 40;
  name.placeholder = "Name it, such as gate";
  nameLabel.htmlFor = name.id;
  const said = element("p", "note", named.noun
    ? `Spark thinks it is a ${named.noun}. Change the name if that is wrong.`
    : "Spark could not tell what it is. Type its name.");
  const noteLabel = element("label", "point-label", "Anything Wonder should know about it?");
  const note = element("input", "point-input");
  note.id = "point-note";
  note.maxLength = 300;
  note.placeholder = "Optional. For example: the sliding gate, it opens to the left.";
  noteLabel.htmlFor = note.id;
  words.append(nameLabel, name, said, noteLabel, note);
  card.append(words);

  const also = element("div", "point-also");
  let searched = named.noun || "";
  const again = button("Look again with this name", "link-button", async () => {
    again.hidden = true;
    try {
      await postJson(`/api/point/${result.id}/confirm`, { noun: name.value });
      searched = name.value.trim();
      lookElsewhere(result.id, run, also);
    } catch (error) {
      also.replaceChildren(element("p", "note error", error.message));
    }
  });
  again.hidden = true;
  name.addEventListener("input", () => {
    again.hidden = !name.value.trim() || name.value.trim().toLowerCase() === searched.toLowerCase() || (state?.cameras?.length || 0) < 2;
  });

  const error = element("p", "note error", "");
  const use = button("Use it", "pill-button", async () => {
    if (!name.value.trim()) {
      error.textContent = "Give it a name first.";
      name.focus();
      return;
    }
    use.disabled = true;
    const cameras = [...also.querySelectorAll("input[type=checkbox]")].filter((b) => b.checked).map((b) => b.value);
    try {
      const reply = await postJson(`/api/point/${result.id}/confirm`, {
        noun: name.value, note: note.value, cameras: also.querySelector(".also-list") ? cameras : null,
      });
      setPointed({ id: result.id, noun: reply.noun, camera: reply.camera, also: reply.also });
      $("point-sheet").close();
      if (screen === "create") updatePreview();
      flash(`Got it: ${reply.noun}.`, screen === "create" ? null : "#/create", "Write a Whenever");
    } catch (err) {
      error.textContent = err.message;
      use.disabled = false;
    }
  }, "check");
  const askIt = button("Ask about it", pointMode === "ask" ? "pill-button" : "pill-button ghost", async () => {
    if (!name.value.trim()) {
      error.textContent = "Give it a name first.";
      name.focus();
      return;
    }
    try {
      const reply = await postJson(`/api/point/${result.id}/confirm`, { noun: name.value, note: note.value, cameras: null });
      askContext.thing = { id: result.id, noun: reply.noun, camera: reply.camera || result.camera, crop: named.crop };
      askContext.occurrence = null;
      $("point-sheet").close();
      if (!ASK_SCREENS.has(screen)) location.hash = "#/live";
      renderAskBar();
      setTimeout(() => { $("ask-input").focus(); showAskIdeas(); }, 80);
    } catch (err) {
      error.textContent = err.message;
    }
  }, "search");
  const row = element("div", "pair-result");
  if (pointMode === "ask") row.append(askIt, use);
  else row.append(use, askIt);
  row.append(button("Tap something else", "link-button", () => resetPointSheet()));
  if (pointMode === "ask") use.className = "pill-button ghost";
  $("point-result").replaceChildren(card, also, again, error, row);
  if (!named.noun) name.focus();
  if (named.noun) lookElsewhere(result.id, run, also);
}

$("point-image").addEventListener("click", (event) => {
  const rect = event.currentTarget.getBoundingClientRect();
  pointHere({ x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height });
});
$("point-refresh").addEventListener("click", () => {
  const shot = snapshot();
  if (!shot) return;
  pointShot = shot;
  $("point-image").src = shot.url;
  resetPointSheet();
});
$("point-close").addEventListener("click", () => { pointRun++; $("point-sheet").close(); });
$("point-sheet").addEventListener("click", (event) => { if (event.target === $("point-sheet")) { pointRun++; $("point-sheet").close(); } });

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
  // Plain words for the person. The model's name and timing are in the Why view and under Problems.
  if (r.spark_s != null || r.sam_s != null) {
    h.text = `Last look at ${clockSeconds(Date.now() / 1000)}: ${r.hit ? "yes" : "no"}`
      + (r.left_out?.length ? ` (${joinList(r.left_out)} left out for now)` : "");
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
  if (parts[0] === "phone-camera") {
    enterCameraMode();
    parts[0] = "live";
  } else if (cameraMode) {
    leaveCameraMode();
  }
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
  const wide = matchMedia("(min-width: 960px)").matches;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const current = a.dataset.nav === name || (name === "whenever" && a.dataset.nav === "whenevers")
      || (name === "cameras" && !wide && a.dataset.nav === "live") || (name === "person" && a.dataset.nav === "actions");
    if (current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  focusId = name === "live" ? (location.hash.match(/rule=([a-z0-9]+)/) || [])[1] || null : null;
  renderFocus(true);
  shapeFrame();
  if (name === "live") {
    renderViewBar(true);
    pollOverlay();
  }
  if (changed) window.scrollTo(0, 0);
  if (name === "whenever") renderDetail(parts[1], true);
  if (name === "person") renderPersonPage(parts[1]);
  if (name === "activity") {
    showActivityView(["reports", "problems"].includes(parts[1]) ? parts[1] : "feed");
    loadJournal(true);
    localStorage.setItem("whenever-seen", String(Date.now() / 1000));
    $("activity-dot").hidden = true;
  }
  if (name === "actions") {
    renderPhoneCard(true);
    renderPeople();
    renderFacesToday();
  }
  if (name === "cameras") renderCamerasScreen(true);
  if (name === "whenevers") renderList(true);
  if (name === "create") renderCreate();
  if (changed && name !== "whenever") askContext.cameras = null;
  renderAskBar();
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

function numberTile(value, label) {
  const tile = element("span", "number-tile");
  tile.append(element("b", null, String(value)), element("span", null, label));
  return tile;
}

// The server says whether the Whenever can see what it needs right now, not only that it is on.
function whereLine(rule) {
  const where = element("span", "card-where");
  if (rule.schedules?.length && !rule.sensors?.length) {
    const s = rule.schedules[0];
    where.append(icon("clock"), element("span", null, s.days?.length ? `${s.when} on ${s.days.join(", ")}` : s.when));
    return where;
  }
  const status = rule.status || { level: rule.paused ? "off" : "ok", text: rule.paused ? "Stopped" : watchingText(rule) };
  where.classList.add(`status-${status.level}`);
  if (status.level === "ok") where.classList.add("watching");
  where.append(status.level === "warn" ? icon("alert") : element("i", "live-dot"), element("span", null, status.text));
  return where;
}

// What each tile on a card means, in a word or two.
const ICON_WORDS = { live: "Cameras", clock: "On a schedule", phone: "Alerts", bulb: "Light", move: "Turns a camera",
  screen: "Shows it", speaker: "Speaks", chart: "Counts", globe: "Web action" };

// The trigger and the actions as labelled tiles, so a list can be scanned without reading every card.
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
  for (const name of [...new Set(names)].slice(0, 4)) {
    const tile = element("span", "icon-tile");
    tile.append(icon(name), element("span", null, ICON_WORDS[name] || ""));
    tile.title = ICON_WORDS[name] || "";
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
  // The name says what it is. The person's own words follow, small, for the details.
  card.append(top, element(tag === "a" ? "h3" : "h1", "card-name", rule.name),
    sentenceNode(rule.sentence || rule.name, "sentence-small"));
  const trouble = openProblems(rule);
  if (trouble.length) {
    const line = element("span", "card-trouble");
    const waiting = trouble.every((g) => g.level === "warn" && ["Muse Spark", "SAM 3.1"].includes(g.source));
    line.append(icon("alert"), element("span", null, waiting ? "Checks wait for Meta to answer"
      : trouble.some((g) => g.level === "error") ? "Needs a look" : "Has a warning"));
    card.append(line);
  }
  const foot = element("div", "card-foot");
  foot.append(whereLine(rule));
  const numbers = element("div", "card-numbers");
  const today = rule.today ?? 0;
  numbers.append(numberTile(today, today === 1 ? "time today" : "times today"));
  for (const [name, value] of Object.entries(rule.values || {}).slice(0, 2)) numbers.append(numberTile(value, name));
  if (rule.last_fired) numbers.append(numberTile(clock(rule.last_fired), dayName(rule.last_fired) === "Today" ? "last time" : `last time, ${dayName(rule.last_fired)}`));
  card.append(numbers, foot);
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
  const key = JSON.stringify([filter, visible.map((r) => [r.id, r.paused, r.fired, r.last_fired, r.sentence, r.today,
    r.status, r.values]),
    Math.floor(Date.now() / 60000), state?.cameras?.length, openProblems().map((g) => [g.id, g.rule_id, g.level])]);
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
  const key = JSON.stringify(rule ? [rule.id, rule.paused, rule.fired, rule.last_fired, rule.sentence, rule.today,
    rule.status, rule.values, rule.plan, rule.schedules.map((s) => s.next), openProblems(rule).map((g) => [g.id, g.count])] : null);
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
  for (const g of openProblems(rule)) {
    const notice = element("div", `detail-trouble level-${g.level}`);
    const words = element("div");
    words.append(element("b", null, g.title), element("p", null, g.hint || ""));
    const more = element("a", "link-button", "See details");
    more.href = "#/activity/problems";
    notice.append(icon("alert"), words, more);
    nodes.push(notice);
  }
  const tab = firedTab(rule);
  if (tab) nodes.push(tab);

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

  // What happened comes first. How it works is for the curious, so it waits behind one tap.
  nodes.push(element("h2", "section-title", "What happened"));
  const feed = element("ol", "feed");
  feed.id = "detail-feed";
  nodes.push(feed);

  const works = element("details", "how-it-works");
  works.append(element("summary", null, "How it works"));
  works.append(element("h3", "section-title small", "What it does"));
  const plan = element("ol", "plan");
  for (const step of rule.plan || []) plan.append(element("li", null, step));
  works.append(plan);

  works.append(element("h3", "section-title small", "How it watches"));
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
  works.append(how);
  const code = element("details", "code");
  code.append(element("summary", null, "Show the code Wonder wrote"));
  const pre = element("pre");
  code.append(pre);
  code.ontoggle = async () => {
    if (code.open && !pre.textContent) pre.textContent = await (await fetch(`/api/rule/${rule.id}/code`)).text();
  };
  works.append(code);
  nodes.push(works);

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
  const items = feedItems(journal.filter((r) => r.rule === rule.name)).filter((item) => cardMatches(item, "all")).slice(0, 30);
  const key = JSON.stringify([rule.id, items.length, items[0]?.t, journal.length]);
  if (feed.dataset.key === key) return;
  feed.dataset.key = key;
  feed.replaceChildren(...(items.length ? feedNodes(items)
    : [element("li", "note", "Nothing yet. Each time it happens, it shows up here with its proof.")]));
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
  options.push({ id: "phone", label: "Alert my phone", icon: "phone", phrase: "alert my phone with a picture",
    ready: !!state?.alerts?.places?.length, pair: true });
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
    if (!o.ready) chip.title = o.pair ? "Pair your phone first. It takes about a minute." : "Needs a key in Actions";
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
  if (pointed) facts.append(element("span", null, `Knows "this" means the ${pointed.noun}${pointedWhere(pointed) ? `, ${pointedWhere(pointed)}` : ""}`));
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

// Pointing opens over Create, so the person never leaves the sentence. Recording uses the live view.
$("point-start").addEventListener("click", () => openPointSheet());
$("change-pointed").addEventListener("click", () => openPointSheet());
$("record").addEventListener("click", async () => {
  location.hash = "#/live";
  await wait(350);
  await recordExample();
  location.hash = "#/create";
});

/* ---------- Activity ---------- */

const KIND_ICON = { event: "eye", notify: "bell", draw: "photo", image: "photo", gallery: "photo", action: "bolt",
  record: "note", error: "alert", say: "speaker" };
const ALERT_KINDS = new Set(["notify", "image", "gallery", "event", "say"]);

// A row outside any occurrence card. Ends, logs, drawings and noted facts belong to a card's details.
function shown(row, kind) {
  if (["end", "log", "draw", "record"].includes(row.kind)) return false;
  if (row.kind === "ask") return kind === "all" || kind === "asked";
  if (kind === "asked") return false;
  if (row.kind === "gallery" && !(row.values?.pictures || []).length) return false;
  if (kind === "alerts") return row.kind === "notify" || (row.kind === "action" && row.name === "Phone");
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
    case "gallery": return (row.text || "Pictures").replace(/\s*\(\d+\)$/, "");
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
  if (row.values?.cameras?.length) meta.push(joinList(row.values.cameras));
  if (row.kind === "record") meta.push(recordNote(row.values || {}));
  text.append(element("b", null, entryTitle(row)), element("div", "entry-meta", meta.join(" · ")));
  if (row.values?.check && row.kind === "event") {
    const buttons = element("div", "entry-buttons");
    buttons.append(button("Why?", "chip", () => showEvidence(row.values.check, row.t), "eye"),
      labelButton(row.values.check, "right", "Right"), labelButton(row.values.check, "wrong", "Wrong"));
    text.append(buttons);
  }
  li.append(badge, text);
  if (row.values?.clip) li.append(clipNode(row.values.clip, row.values.picture, false, clipInfo(row.values)));
  else if (row.values?.picture) li.append(proofPicture(row));
  return li;
}

// A clip of only the cameras where it happened. In the feed it waits for a tap. The alert card plays it.
// With info (its cameras and the second it starts), the clip shows the boxes and names that Live showed.
function clipNode(url, poster, autoplay, info) {
  const box = element("div", "entry-clip");
  const clip = document.createElement("video");
  clip.src = url;
  if (poster) clip.poster = poster;
  clip.muted = true;
  clip.loop = true;
  clip.playsInline = true;
  clip.preload = autoplay ? "auto" : "none";
  clip.setAttribute("aria-label", "A clip of what happened");
  if (autoplay) {
    clip.autoplay = true;
  } else {
    const play = button("", "clip-play", () => {
      play.remove();
      clip.controls = true;
      clip.play().catch(() => {});
    }, "play");
    play.setAttribute("aria-label", "Play the clip");
    box.append(play);
  }
  box.prepend(clip);
  if (info) clipOverlay(box, clip, info);
  return box;
}

// A clip's cameras and the second it starts. A server that does not say clip_start still names the clip
// file after the second of its moment, and the clip starts CLIP_BEFORE_S before that.
function clipInfo(values) {
  if (!values?.clip) return null;
  const cameras = values.clip_cameras || values.cameras || (values.camera ? [values.camera] : []);
  let start = values.clip_start ?? values.clip?.start;
  if (start == null) {
    const second = Number((String(values.clip).match(/\/(\d{9,11})-[a-z0-9]+\.mp4/i) || [])[1]);
    if (second) start = second - CLIP_BEFORE_S;
  }
  return cameras.length && start ? { cameras, start } : null;
}

// Draws what the analyzer kept for the clip's moment over the clip, in step with the clip's own time.
// Several cameras play side by side at the same height, in the order of the alert's picture.
function clipOverlay(box, clip, info) {
  const canvas = element("canvas", "clip-hud");
  canvas.setAttribute("aria-hidden", "true");
  box.append(canvas);
  let kept = null;
  let loading = false;
  let running = false;
  const load = async () => {
    if (kept || loading) return;
    loading = true;
    const span = Number.isFinite(clip.duration) && clip.duration > 0 ? clip.duration : 12;
    const from = (info.start - 1).toFixed(2);
    const to = (info.start + span + 1).toFixed(2);
    kept = await Promise.all(info.cameras.map((name) => fetch(`/api/overlay/history?camera=${encodeURIComponent(name)}&from=${from}&to=${to}`)
      .then((r) => (r.ok ? r.json() : null)).then((r) => r && { size: r.size || [16, 9], frames: (r.frames || []).sort((a, b) => a.t - b.t) })
      .catch(() => null)));
    draw();
  };
  const draw = () => {
    if (!kept || !clip.videoWidth || !clip.clientWidth) return;
    const scale = Math.min(clip.clientWidth / clip.videoWidth, clip.clientHeight / clip.videoHeight);
    const area = { w: clip.videoWidth * scale, h: clip.videoHeight * scale };
    area.x = clip.offsetLeft + (clip.clientWidth - area.w) / 2;
    area.y = clip.offsetTop + (clip.clientHeight - area.h) / 2;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    Object.assign(canvas.style, { left: `${area.x}px`, top: `${area.y}px`, width: `${area.w}px`, height: `${area.h}px` });
    canvas.width = Math.round(area.w * ratio);
    canvas.height = Math.round(area.h * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const t = info.start + clip.currentTime;
    const aspects = kept.map((k) => (k ? k.size[0] / k.size[1] : 16 / 9));
    const total = aspects.reduce((a, b) => a + b, 0);
    const chips = [];
    let x = 0;
    kept.forEach((track, i) => {
      const rect = { x, y: 0, w: (area.w * aspects[i]) / total, h: area.h };
      x += rect.w;
      if (!track?.frames.length) return;
      let a = null;
      let b = null;
      for (const f of track.frames) {
        if (f.t <= t) a = f;
        else {
          b = f;
          break;
        }
      }
      a = a || b;
      // Nothing kept near this moment: the clip plays plain.
      if (!a || Math.abs(a.t - t) > 2.5) return;
      const f = b && b !== a ? clamp((t - a.t) / Math.max(0.05, b.t - a.t), 0, 1) : 0;
      const level = Math.min(2, detailOf(rect.w));
      const size = clamp(rect.w / 46, 9.5, 12.5);
      ctx.save();
      ctx.beginPath();
      ctx.rect(rect.x, rect.y, rect.w, rect.h);
      ctx.clip();
      const step = (list, id) => (b && b !== a ? (list || []).find((q) => q.id === id) : null);
      for (const thing of a.things || []) {
        const next = step(b?.things, thing.id);
        const shape = { item: thing, alpha: 1, box: next ? thing.box.map((v, k) => lerp(v, next.box[k], f)) : thing.box };
        drawThing(ctx, shape, boxRect(shape.box, rect, false), size, level, 0, chips, rect);
      }
      for (const person of a.people || []) {
        const next = step(b?.people, person.id);
        const trail = track.frames.filter((fr) => fr.t <= t && fr.t > t - 3)
          .map((fr) => (fr.people || []).find((q) => q.id === person.id)).filter(Boolean)
          .map((q) => [(q.box[0] + q.box[2]) / 2, q.box[3]]);
        const shape = { item: { ...person, trail }, alpha: 1, box: next ? person.box.map((v, k) => lerp(v, next.box[k], f)) : person.box };
        if (hud.layers.trails) drawTrail(ctx, shape, rect, false);
        drawPerson(ctx, shape, boxRect(shape.box, rect, false), size, level, 0, chips, rect);
      }
      ctx.restore();
    });
    flushChips(ctx, chips);
  };
  const loop = () => {
    if (clip.paused || !clip.isConnected) {
      running = false;
      return;
    }
    draw();
    requestAnimationFrame(loop);
  };
  clip.addEventListener("loadedmetadata", load);
  clip.addEventListener("seeked", draw);
  clip.addEventListener("timeupdate", () => { if (!running) draw(); });
  clip.addEventListener("play", () => {
    load();
    if (!running) {
      running = true;
      requestAnimationFrame(loop);
    }
  });
}

/* ---------- Who it is: the alert's person, and the story before the alert ---------- */

const EVIDENCE_WORD = { face: "Face", clothes: "Clothes", tracker: "Tracker", look: "Look" };
const answeredWho = new Map();

function roleBadge(role) {
  const badge = element("span", "role-badge", ROLE_WORD[role] || words(role));
  badge.dataset.role = role;
  return badge;
}

// The four roles a person can have, as chips. Tapping the chosen one again clears it.
function roleChips(box, current, onPick) {
  box.replaceChildren(...ROLES.map(([role, label]) => {
    const chip = button(label, "chip role-chip", () => {
      const next = chip.getAttribute("aria-pressed") === "true" ? "" : role;
      box.querySelectorAll(".role-chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip && next !== "")));
      box.dataset.role = next;
      onPick?.(next);
    });
    chip.dataset.role = role;
    chip.setAttribute("aria-pressed", String(current === role));
    return chip;
  }));
  box.dataset.role = current || "";
  return box;
}

// Who Whenever thinks this is: the tagged face beside the face now, how sure it is, and why. Each answer
// teaches it the face, and how far each kind of score can be trusted with these cameras.
function whoNode(who) {
  const box = element("div", "who");
  const head = element("div", "who-head");
  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  // A visitor was never tagged: ask whether it is the same person as when Whenever first saw them.
  const question = who.visitor && who.first_seen ? `Same person as at ${clock(who.first_seen)}?`
    : who.name ? `Is this ${who.name}?` : "Is this the same person?";
  head.append(element("b", null, question));
  if (who.role) head.append(roleBadge(who.role));
  box.append(head);
  const faces = element("div", "who-faces");
  const face = (url, label) => {
    const figure = element("figure", "who-face");
    const img = element("img");
    img.src = url;
    img.alt = `${who.name || "The person"}, ${label.toLowerCase()}`;
    img.loading = "lazy";
    img.addEventListener("click", () => openFrame(url, null, `${who.name || "The person"}, ${label.toLowerCase()}`));
    figure.append(img, element("figcaption", null, label));
    return figure;
  };
  const day = who.tagged ? ` ${new Date(who.tagged * 1000).toLocaleDateString([], { day: "numeric", month: "short" })}` : "";
  const tagged = who.visitor ? (who.first_seen ? `First seen ${clock(who.first_seen)}` : "First seen")
    : who.tag_body ? `Tagged by clothes${day}` : `Tagged${day}`;
  if (who.tag_face) faces.append(face(who.tag_face, tagged));
  if (who.now_face) faces.append(face(who.now_face, "Now"));
  if (faces.childNodes.length) box.append(faces);
  if (who.sure != null) {
    const percent = Math.round(who.sure * 100);
    const sure = element("div", "who-sure");
    const label = element("div", "who-sure-label");
    label.append(element("span", null, "How sure"), element("b", null, `${percent}%`));
    const bar = element("div", "who-bar");
    bar.setAttribute("role", "meter");
    bar.setAttribute("aria-label", "How sure");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(percent));
    const fill = element("i");
    fill.style.width = `${percent}%`;
    bar.append(fill);
    sure.append(label, bar);
    box.append(sure);
  }
  if (who.evidence?.length) {
    const why = element("dl", "who-why");
    for (const e of who.evidence) {
      const row = element("div");
      row.append(element("dt", null, EVIDENCE_WORD[e.kind] || words(e.kind)), element("dd", null, e.text || ""),
        element("dd", "who-score", e.score != null ? `match ${Number(e.score).toFixed(2)}` : "kept"));
      why.append(row);
    }
    box.append(why);
  }
  if (!who.person_id) return box;
  const note = element("p", "note who-note", "Each answer teaches Whenever this face, and how sure each score really is here.");
  // The feed redraws often, so an answer given on this page stays shown.
  const given = answeredWho.get(`${who.person_id}|${who.sighting}`) ?? who.confirmed;
  const show = (same, text) => {
    yes.disabled = true;
    no.disabled = true;
    (same ? yes : no).classList.add("chosen");
    note.className = "note who-note ok";
    note.textContent = text || (same ? `Saved: this is ${who.name || "them"}. Whenever knows the face a little better now.`
      : `Saved: not ${who.name || "them"}. Whenever will not count this face as them.`);
  };
  const answer = async (same) => {
    yes.disabled = true;
    no.disabled = true;
    note.className = "note who-note";
    note.textContent = "Saving";
    try {
      const reply = await postJson(`/api/people/${encodeURIComponent(who.person_id)}/confirm`, { sighting: who.sighting, yes: same });
      answeredWho.set(`${who.person_id}|${who.sighting}`, same);
      show(same, reply.text);
    } catch (error) {
      yes.disabled = false;
      no.disabled = false;
      note.className = "note who-note error";
      note.textContent = error.message;
    }
  };
  const yes = button("Yes, same person", "pill-button small who-yes", () => answer(true), "check");
  const no = button("Not this person", "pill-button small ghost", () => answer(false), "x");
  const row = element("div", "who-buttons");
  row.append(yes, no);
  box.append(row, note);
  if (given === true || given === false) show(given);
  return box;
}

// The moments before the alert: a few pictures, each with its time and what happened.
function storyNode(story) {
  const strip = element("div", "story");
  for (const step of story.slice(0, 4)) {
    const open = element("button", "story-step");
    open.type = "button";
    const img = element("img");
    img.src = step.picture;
    img.alt = step.text || "";
    img.loading = "lazy";
    const said = element("span", "story-words");
    said.append(element("b", null, clock(step.t)), element("span", null, step.text || ""));
    open.append(img, said);
    open.addEventListener("click", () => openFrame(step.picture, null, `${clock(step.t)}: ${step.text || ""}`));
    strip.append(open);
  }
  return strip;
}

function whoNodes(values) {
  return [...(values?.people || []).map(whoNode), ...(values?.story?.length ? [storyNode(values.story)] : [])];
}

// An alert in the page: the cameras where it happened, playing, over whatever screen is open.
let alertTimer = null;

function showAlertCard(item) {
  const card = $("alert-card");
  const head = element("div", "alert-head");
  const words = element("div", "place-words");
  words.append(element("b", null, item.rule), element("small", null, [joinList(item.cameras || []), clock(Date.now() / 1000)].filter(Boolean).join(" · ")));
  head.append(words, button("", "icon-button", hideAlertCard, "x"));
  head.lastChild.setAttribute("aria-label", "Close the alert");
  const info = clipInfo({ clip: item.clip, clip_start: item.clip_start, cameras: item.cameras });
  const media = item.clip ? clipNode(item.clip, item.url, true, info) : item.url ? Object.assign(element("img", "alert-picture"), { src: item.url, alt: "" }) : null;
  const see = element("a", "pill-button small", "See it in Activity");
  see.href = "#/activity";
  see.addEventListener("click", hideAlertCard);
  card.replaceChildren(head, ...(media ? [media] : []), element("p", null, item.text), ...whoNodes(item), see);
  card.hidden = false;
  clearTimeout(alertTimer);
  alertTimer = setTimeout(hideAlertCard, 20000);
}

function hideAlertCard() {
  clearTimeout(alertTimer);
  $("alert-card").hidden = true;
  $("alert-card").replaceChildren();
}

$("alert-card").addEventListener("pointerenter", () => clearTimeout(alertTimer));
$("alert-card").addEventListener("pointerleave", () => { alertTimer = setTimeout(hideAlertCard, 8000); });

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

/* ---------- Occurrences: one card for each thing that happened ---------- */

// An event and every step that its job took share one occurrence: the event's check. Older rows
// carry no occurrence, so a row joins its rule's newest event of the minute before it.
const JOIN_OLD_S = 60;

function occurrenceOf(row) {
  return row.values?.occurrence || (row.kind === "event" && !row.values?.step ? row.values?.check : null);
}

function feedItems(rows) {
  const cards = new Map();
  const loose = [];
  const newest = {};
  for (const row of rows) {
    let id = occurrenceOf(row);
    if (!id && row.kind !== "notify" && row.kind !== "gallery" && newest[row.rule] && row.t - newest[row.rule].t <= JOIN_OLD_S) {
      id = newest[row.rule].id;
    }
    if (!id) {
      loose.push({ t: row.t, row });
      continue;
    }
    if (!cards.has(id)) cards.set(id, { id, t: row.t, rows: [] });
    const card = cards.get(id);
    card.rows.push(row);
    if (row.kind === "event" && !row.values?.step) {
      card.t = row.t;
      newest[row.rule] = { id, t: row.t };
    }
  }
  return [...[...cards.values()].map((card) => ({ t: card.t, card })), ...loose].sort((a, b) => b.t - a.t);
}

function sentRows(card) {
  return card.rows.filter((r) => ((r.kind === "action" && r.name === "Phone") || r.kind === "notify") && r.values?.how !== "repeat");
}

function cardMatches(item, kind) {
  if (!item.card) return shown(item.row, kind);
  if (kind === "asked") return false;
  if (kind === "alerts") return sentRows(item.card).length > 0;
  if (kind === "action") return item.card.rows.some((r) => r.kind === "action");
  return true;
}

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
}

function sentNote(row) {
  const places = row.values?.places || [];
  const how = row.values?.how || row.values?.mode;
  if (row.kind === "action" && row.values?.ok === false) return `The alert did not go out at ${clock(row.t)}`;
  if (how === "quiet") return `Shown here at ${clock(row.t)}, not sent: the same alert went out a few minutes ago`;
  if (how === "here" || (!places.length && row.kind === "notify")) return `Shown here at ${clock(row.t)}`;
  const where = places.length ? joinList(places) : "your phone";
  // The picture goes out at once. The row is written when the clip has followed it.
  return `${how === "reply" ? "Added to the alert on" : "Sent to"} ${where} at ${clockSeconds(row.values?.sent_at || row.t)}`;
}

function sentText(row) {
  return row.kind === "action" ? row.values?.inputs?.text || "" : row.text || "";
}

function occurrenceCard(card) {
  const rows = [...card.rows].sort((a, b) => a.t - b.t);
  const event = rows.find((r) => r.kind === "event" && !r.values?.step) || rows[0];
  const end = rows.find((r) => r.kind === "end" && !r.values?.step);
  const sent = rows.filter((r) => ((r.kind === "action" && r.name === "Phone") || r.kind === "notify") && r.values?.how !== "repeat");
  const first = sent.find((r) => r.values?.how !== "reply" && r.values?.mode !== "reply") || sent[0];
  const li = element("li", "occurrence");
  li.dataset.t = event.t;
  if (sent.some((r) => r.values?.ok === false)) li.classList.add("problem");

  const head = element("div", "occ-head");
  const title = first ? sentText(first) : entryTitle(event);
  const when = end ? `${clock(event.t)} to ${clock(end.t)} · ${duration(end.seconds ?? end.t - event.t)}`
    : event.kind === "event" ? `${clock(event.t)} · still going` : clock(event.t);
  const named = element("div", "occ-words");
  named.append(element("b", null, title || entryTitle(event)),
    element("span", "entry-meta", [when, event.rule, ...(first?.values?.cameras?.length ? [joinList(first.values.cameras)]
      : cameraOf(event) ? [cameraOf(event)] : [])].join(" · ")));
  head.append(named);
  li.append(head);

  // The cameras where it happened, as the phone got them: the clip, or else the picture.
  const media = rows.find((r) => r.values?.clip) || first || event;
  if (media.values?.clip) li.append(clipNode(media.values.clip, media.values.picture, false, clipInfo(media.values)));
  else if (media.values?.picture) li.append(proofPicture(media));
  // Who the alert is about, and the moments before it.
  const told = rows.find((r) => r.values?.people?.length || r.values?.story?.length);
  if (told) li.append(...whoNodes(told.values));

  // Steps that matter to the person: what the armed sensors saw, and the pictures found.
  const steps = element("div", "occ-steps");
  for (const row of rows) {
    if (row.kind === "event" && row.values?.step) {
      steps.append(element("p", "occ-step", `${entryTitle(row)} at ${clock(row.t)}`));
    } else if (row.kind === "gallery" && (row.values?.pictures || []).length) {
      const pictures = element("div", "gallery");
      for (const url of row.values.pictures) {
        const open = document.createElement("button");
        open.type = "button";
        open.title = row.text;
        open.onclick = () => openFrame(url, null, row.text);
        const img = document.createElement("img");
        img.src = url;
        img.alt = row.text;
        img.loading = "lazy";
        open.append(img);
        pictures.append(open);
      }
      steps.append(element("p", "occ-step", row.text.replace(/\s*\(\d+\)$/, "")), pictures);
    }
  }
  for (const row of sent) {
    const line = element("p", "occ-sent");
    line.append(icon(row.values?.ok === false ? "alert" : (row.values?.places || []).length ? "send" : "bell"),
      element("span", null, `${sentNote(row)}${row !== first && sentText(row) ? `: ${sentText(row)}` : ""}`));
    steps.append(line);
  }
  if (steps.childNodes.length) li.append(steps);

  const check = event.values?.check;
  const buttons = element("div", "entry-buttons");
  if (check) {
    buttons.append(labelButton(check, "right", "Right"), labelButton(check, "wrong", "Wrong"),
      button("Why?", "chip", () => showEvidence(check, event.t), "eye"));
  }
  if (event.kind === "event") {
    buttons.append(button("Ask about this", "chip", () => askAboutOccurrence({
      id: card.id, rule: event.rule, t: event.t, end: end?.t || null,
    }), "search"));
  }
  const details = element("details", "occ-details");
  details.append(element("summary", null, "Details"));
  const lines = element("ol", "occ-lines");
  for (const row of rows) {
    let text = entryTitle(row);
    if (row.kind === "event") text = `${row.values?.step ? "Step" : "What the model saw"}: ${row.text || entryTitle(row)}`;
    if (row.kind === "record") text = `${words(row.name)}: ${recordNote(Object.fromEntries(
      Object.entries(row.values || {}).filter(([k]) => k !== "occurrence")))}`;
    if (row.kind === "end") text = `${row.values?.step ? "Step ended" : "Ended"} after ${duration(row.seconds || 0)}`;
    if (row.kind === "draw") text = `Drew "${row.text}" on the picture`;
    if (row.kind === "action" && row.name !== "Phone") text = entryTitle(row);
    lines.append(element("li", null, `${clockSeconds(row.t)}  ${text}`));
  }
  details.append(lines);
  li.append(buttons, details);
  return li;
}

function clockSeconds(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function feedNodes(items) {
  const nodes = [];
  let day = "";
  for (const item of items) {
    const d = dayName(item.t);
    if (d !== day) {
      day = d;
      nodes.push(element("li", "day", d));
    }
    nodes.push(item.card ? occurrenceCard(item.card) : item.row.kind === "ask" ? answerEntry(item.row) : entryNode(item.row));
  }
  return nodes;
}

function renderFeed(force) {
  const items = feedItems(journal.slice(-1000)).filter((item) => cardMatches(item, feedKind)).slice(0, 120);
  const key = JSON.stringify([feedKind, journal.length, journal[journal.length - 1]?.t]);
  if (!force && key === keys.feed) return;
  keys.feed = key;
  const list = $("activity-list");
  if (!items.length) {
    const empty = element("li", "empty");
    empty.append(element("h2", null, feedKind === "alerts" ? "No alerts yet" : "Quiet so far"),
      element("p", null, feedKind === "alerts" ? "Each alert that reaches you shows up here, with the cameras where it happened."
        : "When a Whenever sees something, it shows up here with its proof."));
    list.replaceChildren(empty);
    $("activity-guide").textContent = "";
    return;
  }
  list.replaceChildren(...feedNodes(items));
  updateGuide();
}

// Guide words: the time span in view stays pinned while the person scrolls.
function updateGuide() {
  if (screen !== "activity") return;
  const entries = [...$("activity-list").querySelectorAll(".entry, .occurrence")];
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
  if (view === "problems") loadProblems(true);
}

$("activity-tabs").addEventListener("click", (event) => {
  const b = event.target.closest("[data-view]");
  if (!b) return;
  location.hash = b.dataset.view === "feed" ? "#/activity" : `#/activity/${b.dataset.view}`;
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
  const fresh = journal.some((r) => r.t > seen && (r.kind === "notify" || (r.kind === "action" && r.name === "Phone")
    || (r.kind === "event" && !r.values?.step)));
  const trouble = openProblems().length > 0;
  $("activity-dot").hidden = !(fresh || trouble) || screen === "activity";
  $("activity-dot").classList.toggle("warn", trouble && !fresh);
  if (screen === "activity") {
    renderFeed();
    if (!document.querySelector('[data-view-panel="reports"]').hidden) renderReports();
    if (!document.querySelector('[data-view-panel="problems"]').hidden) loadProblems();
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
  // A step that an armed sensor saw inside one event is not another event.
  const events = today.filter((r) => r.kind === "event" && !r.values?.step);
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

/* ---------- Where alerts go: pairing ---------- */

// The phone, the computer, a Telegram chat, an inbox or the ntfy app. Each alert goes to every one.
const PLACE_ICON = { push: "bell", telegram: "send", email: "mail", ntfy: "phone" };
const PLACE_KIND = { push: "Browser alerts", telegram: "Telegram", email: "Email", ntfy: "ntfy app" };
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
let pairToken = null;
let pairTimer = null;

function onPhone() {
  return matchMedia("(pointer: coarse)").matches && Math.min(innerWidth, screen.width || innerWidth) < 820;
}

function canPushHere() {
  return isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window
    && !(isIOS && !standalone);
}

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? "Android" : isIOS ? (/iPad/.test(ua) ? "iPad" : "iPhone")
    : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "this device";
  const app = /Edg\//.test(ua) ? "Edge" : /Firefox|FxiOS/.test(ua) ? "Firefox" : /CriOS|Chrome/.test(ua) ? "Chrome"
    : /Safari/.test(ua) ? "Safari" : "A browser";
  return isIOS && standalone ? `Whenever on ${os}` : `${app} on ${os}`;
}

function keyBytes(text) {
  const raw = atob((text + "=".repeat((4 - (text.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function setPairHead(title, back) {
  $("pair-title").textContent = title;
  $("pair-back").hidden = !back;
}

function showPair(...nodes) {
  $("pair-body").replaceChildren(...nodes);
  $("pair-body").scrollTop = 0;
}

function stopWaiting() {
  clearTimeout(pairTimer);
  pairToken = null;
}

function openPairing(onDone, kind) {
  pairDone = onDone || null;
  if (!$("pair").open) $("pair").showModal();
  if (kind) PAIR_FLOWS[kind]();
  else showChoices();
}

function closePairing() {
  stopWaiting();
  pairDone = null;
  if ($("pair").open) $("pair").close();
}

function finishPairing() {
  const done = pairDone;
  closePairing();
  if (done) done();
}

function para(text, className = "pair-text") {
  return element("p", className, text);
}

function makeQr(url, label) {
  const qr = element("div", "qr pair-qr");
  qr.setAttribute("role", "img");
  qr.setAttribute("aria-label", label || `QR code for ${url}`);
  loadQr().then((make) => {
    if (!make) return qr.replaceWith(para("The QR code did not load. Use the link instead.", "note"));
    const code = make(0, "M");
    code.addData(url);
    code.make();
    qr.innerHTML = code.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
  });
  return qr;
}

// The same link, to open, copy or send. A phone opens it in a new tab.
function linkRow(url, openText) {
  const row = element("div", "link-row");
  const open = element("a", "pill-button small ghost", null);
  open.href = url;
  open.target = "_blank";
  open.rel = "noopener";
  open.append(icon("external"), openText || "Open the link");
  const copy = button("Copy link", "pill-button small ghost", async (event) => {
    const b = event.currentTarget;
    await navigator.clipboard.writeText(url).catch(() => {});
    b.lastChild.textContent = "Copied";
  }, "copy");
  row.append(open, copy);
  if (navigator.share && onPhone()) {
    row.append(button("Send", "pill-button small ghost", () => navigator.share({ title: "Whenever", url }).catch(() => {}), "share"));
  }
  return row;
}

function waitingNode(text) {
  const box = element("p", "pair-wait");
  box.setAttribute("role", "status");
  box.append(element("span", "wait-dot"), element("span", null, text));
  return box;
}

// The page on the computer asks until the phone, the bot or the email link finishes the pairing.
function waitForPairing(token, statusNode, again, onDone) {
  stopWaiting();
  pairToken = token;
  const tick = async () => {
    if (pairToken !== token) return;
    try {
      const found = await (await fetch(`/api/pair/${token}`)).json();
      if (pairToken !== token) return;
      if (found.done) {
        stopWaiting();
        await refreshState();
        return onDone ? onDone(found) : showPaired(found.place);
      }
      if (found.expired) {
        stopWaiting();
        statusNode.replaceWith(element("div", "pair-old", null));
        const old = $("pair-body").querySelector(".pair-old");
        old.append(para("This link is old now. A link works for 15 minutes."), button("Make a new link", "pill-button small", again, "arrow"));
        return;
      }
    } catch {
      // A short network gap is not an answer. Ask again.
    }
    pairTimer = setTimeout(tick, 1500);
  };
  pairTimer = setTimeout(tick, 1200);
}

function showChoices() {
  stopWaiting();
  setPairHead("Where should alerts reach you?", false);
  const alerts = state?.alerts || { places: [] };
  const hosted = !!state?.hosted;
  const count = (kind) => alerts.places.filter((p) => p.kind === kind).length;
  const options = [
    { kind: "push", title: hosted ? "Browser alerts" : "This computer",
      text: hosted ? "No app to install. Chrome on Android, or Safari on iPhone." : "Alerts from this browser, on this computer.",
      tag: hosted ? "Quickest" : "" },
    { kind: "telegram", title: "Telegram", text: "Each alert in a chat, with its picture and an Open button.",
      tag: alerts.telegram_key ? "" : "About 2 minutes" },
    { kind: "email", title: "Email", text: "A message with the picture. Good for reports and quiet things.",
      tag: alerts.email_ready ? "" : "Needs a mail server" },
    { kind: "ntfy", title: "ntfy app", text: "A free app for Android and iPhone that only shows alerts.", tag: "" },
  ];
  const list = element("div", "place-choices");
  for (const o of options) {
    const choice = button("", "place-choice", () => PAIR_FLOWS[o.kind]());
    choice.replaceChildren();
    const badge = element("span", "badge");
    badge.append(icon(PLACE_ICON[o.kind]));
    const words = element("span", "place-words");
    const head = element("b", null, o.title);
    if (count(o.kind)) head.append(element("span", "state ready", count(o.kind) > 1 ? `${count(o.kind)} on` : "On"));
    else if (o.tag) head.append(element("span", "state waiting", o.tag));
    words.append(head, element("small", null, o.text));
    choice.append(badge, words, icon("arrow"));
    list.append(choice);
  }
  const intro = para(alerts.places.length
    ? "Add another place. Each alert goes to every place that you pair."
    : "Pick one. You can add more later. Each alert goes to every place that you pair.");
  const nodes = [intro, list];
  if (!hosted) nodes.push(para("A phone cannot open Whenever on this computer. To get browser alerts on a phone, use Whenever online at wheneverwhatever.si.", "note"));
  showPair(...nodes);
}

async function subscribeHere(result) {
  result.replaceChildren(para("Your browser asks once. Choose Allow.", "note"));
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      result.replaceChildren(para(isIOS
        ? "Alerts are blocked. Open Settings, then Notifications, then Whenever, and allow them."
        : "Alerts are blocked for this site. Allow notifications in the site settings, next to the address, then try again.", "note error"));
      return;
    }
    const start = await postJson("/api/pair/start", { kind: "push" });
    const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    const old = await registration.pushManager.getSubscription();
    if (old) await old.unsubscribe();
    const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(start.push_key) });
    const reply = await postJson("/api/push/subscribe", { subscription: subscription.toJSON(), label: deviceLabel() });
    await refreshState();
    showPaired({ kind: "push", ...reply.place });
  } catch (error) {
    result.replaceChildren(para(error.message || "This browser could not turn on alerts.", "note error"));
  }
}

async function pairPush() {
  setPairHead(state?.hosted ? "Browser alerts" : "This computer", true);
  showPair(waitingNode("Making a pairing link"));
  const here = element("div", "pair-result");
  const hereButton = (text, cls = "pill-button") => button(text, cls, () => subscribeHere(here), "bell");
  if (!state?.hosted) {
    if (!canPushHere()) {
      return showPair(para("This browser cannot show alerts. Open Whenever in Chrome, Edge or Firefox.", "note error"));
    }
    return showPair(para("Alerts appear on this computer, also when this tab is in the background."),
      hereButton("Turn on alerts here"), here,
      para("For a phone, use Whenever online at wheneverwhatever.si, or pick Telegram, email or the ntfy app.", "note"));
  }
  let start;
  try {
    start = await postJson("/api/pair/start", { kind: "push" });
  } catch (error) {
    return showPair(para(error.message, "note error"));
  }
  const status = waitingNode("Waiting for your phone");
  if (onPhone() && canPushHere()) {
    const other = element("details", "pair-other");
    other.append(element("summary", null, "Pair a different phone"),
      para("Open this link on the other phone, then tap Turn on alerts."), linkRow(start.url, "Open"), status);
    other.addEventListener("toggle", () => { if (other.open) waitForPairing(start.token, status, pairPush); });
    return showPair(para("Each alert shows on this phone, with the picture that proves it. You need no app."),
      hereButton("Turn on alerts", "pill-button big"), here, other);
  }
  if (onPhone()) {
    // An iPhone shows web alerts only for an app on its Home Screen. The link opens a page that shows how.
    const steps = element("ol", "pair-steps");
    const one = element("li");
    const open = element("a", "pill-button", null);
    open.href = start.url;
    open.target = "_blank";
    open.rel = "noopener";
    open.append(icon("external"), "Open the pairing page");
    one.append(element("b", null, "Open the pairing page"), para("It opens in a new tab."), open);
    const two = element("li");
    two.append(element("b", null, isIOS ? "Add it to your Home Screen" : "Open it in Chrome"),
      para(isIOS ? "Tap Share, then Add to Home Screen. iPhone needs this for web alerts (iOS 16.4 or later)."
        : "This browser cannot show alerts. Chrome can."));
    const three = element("li");
    three.append(element("b", null, "Tap Turn on alerts"), para(isIOS ? "Open Whenever from your Home Screen first." : "Then tap Allow."));
    steps.append(one, two, three);
    waitForPairing(start.token, status, pairPush);
    return showPair(steps, status);
  }
  const grid = element("div", "pair-grid");
  const words = element("div");
  const steps = element("ol", "mini-steps");
  steps.append(element("li", null, "Point your phone's camera at the code."),
    element("li", null, "Open the link, then tap Turn on alerts."),
    element("li", null, "Tap Allow. A test alert arrives."));
  words.append(element("b", "pair-lead", "Scan this with your phone"), steps,
    para("On iPhone, the page first shows how to add Whenever to the Home Screen.", "note"));
  grid.append(makeQr(start.url, "QR code that opens the pairing page"), words);
  const send = element("div", "pair-send");
  send.append(para("No camera? Send the link to your phone:", "note"), linkRow(start.url));
  const local = element("div", "pair-alt");
  if (canPushHere()) local.append(button("Get alerts on this computer instead", "link-button", () => subscribeHere(here)), here);
  waitForPairing(start.token, status, pairPush);
  showPair(grid, status, send, local);
}

function telegramProgress(step) {
  const bar = element("ol", "pair-progress");
  ["Make your bot", "Connect your chat"].forEach((name, i) => {
    const item = element("li", i + 1 < step ? "done" : i + 1 === step ? "now" : "", name);
    bar.append(item);
  });
  return bar;
}

function botName() {
  const chars = "abcdefghjkmnpqrstuvwxyz23456789";
  let tail = "";
  for (let i = 0; i < 4; i++) tail += chars[Math.floor(Math.random() * chars.length)];
  return `whenever_${tail}_bot`;
}

function copyLine(label, value) {
  const row = element("div", "copy-line");
  row.append(element("span", null, label), element("code", null, value), button("Copy", "pill-button small ghost", async (event) => {
    await navigator.clipboard.writeText(value).catch(() => {});
    event.currentTarget.lastChild.textContent = "Copied";
  }, "copy"));
  return row;
}

// The same two patterns as the server. A pasted line break can vanish, so the second one finds a token
// that touches the next word: BotFather's tokens have 35 characters after the colon, from AA.
function findBotTokens(text) {
  const alone = [...text.matchAll(/(?<![0-9])(\d{5,16}:[A-Za-z0-9_-]{30,60})(?![A-Za-z0-9_-])/g)].map((m) => m[1]);
  const exact = alone.filter((t) => t.split(":")[1].length === 35);
  const glued = [...text.matchAll(/(?<![0-9])(\d{5,16}:AA[A-Za-z0-9_-]{33})/g)].map((m) => m[1]);
  return [...new Set(exact.length ? exact : glued.length ? glued : alone)];
}

function telegramMakeBot() {
  stopWaiting();
  setPairHead("Telegram", true);
  const father = "https://t.me/BotFather?text=%2Fnewbot";
  const steps = element("ol", "pair-steps");

  const one = element("li");
  one.append(element("b", null, "Open BotFather"), para("BotFather is Telegram's own tool for bots. The message /newbot is typed for you: tap Send."));
  if (onPhone()) {
    const open = element("a", "pill-button", null);
    open.href = father;
    open.target = "_blank";
    open.rel = "noopener";
    open.append(icon("send"), "Open BotFather");
    one.append(open);
  } else {
    const row = element("div", "qr-row");
    const side = element("div");
    side.append(para("Scan with your phone's camera, or open Telegram on this computer.", "note"), linkRow(father, "Open Telegram"));
    row.append(makeQr(father, "QR code that opens BotFather in Telegram"), side);
    one.append(row);
  }

  const two = element("li");
  two.append(element("b", null, "Answer its two questions"), para("Copy each answer, paste it in the chat, and send it."),
    copyLine("Name", "Whenever"), copyLine("Username", botName()),
    para("If BotFather says that the username is taken, add a number before _bot.", "note"));

  const three = element("li");
  const form = element("form", "token-form");
  const field = element("textarea");
  field.rows = 3;
  field.placeholder = "Paste BotFather's answer here. The whole message is fine.";
  field.autocomplete = "off";
  field.spellcheck = false;
  field.setAttribute("aria-label", "BotFather's answer, or only the token");
  const found = element("p", "token-found");
  found.setAttribute("role", "status");
  const connect = element("button", "pill-button", "Connect");
  connect.type = "submit";
  const result = element("p", "note", "");
  let tried = "";

  // The page reads the token out of the pasted text, so nobody has to select it exactly.
  const read = () => {
    const tokens = findBotTokens(field.value);
    const name = (field.value.match(/t\.me\/([A-Za-z0-9_]{5,32})/i) || [])[1];
    found.className = "token-found";
    result.textContent = "";
    if (!field.value.trim()) {
      found.replaceChildren();
    } else if (tokens.length === 1) {
      found.classList.add("ok");
      const masked = `${tokens[0].slice(0, 6)}…${tokens[0].slice(-4)}`;
      found.replaceChildren(icon("check"), element("span", null, `Found the token${name ? ` for @${name}` : ""}: ${masked}`));
    } else if (tokens.length > 1) {
      found.replaceChildren(element("span", null, "This text has more than one token. Paste the answer about your new bot only."));
    } else {
      found.replaceChildren(element("span", null, "No token here yet. In BotFather's answer, it is the line under \"Use this token\"."));
    }
    connect.disabled = tokens.length !== 1;
    return tokens;
  };

  const send = async () => {
    const tokens = read();
    if (tokens.length !== 1) return;
    tried = tokens[0];
    connect.disabled = true;
    result.className = "note";
    result.textContent = "Asking Telegram";
    try {
      await postJson("/api/telegram/key", { key: tokens[0] });
      await refreshState();
      telegramConnect();
    } catch (error) {
      result.className = "note error";
      result.textContent = error.message;
      connect.disabled = false;
    }
  };

  connect.disabled = true;
  form.append(field, found, connect);
  field.addEventListener("input", read);
  // A paste that holds one new token connects at once. Typing waits for the button.
  field.addEventListener("paste", () => setTimeout(() => {
    const tokens = read();
    if (tokens.length === 1 && tokens[0] !== tried) send();
  }, 0));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  three.append(element("b", null, "Paste BotFather's answer here"),
    para("Copy its whole answer, or only the token. Whenever finds the token by itself."), form, result,
    para("Whenever keeps the token on its server. Rules and this page cannot read it.", "note"));

  steps.append(one, two, three);
  showPair(telegramProgress(1), steps);
}

async function telegramConnect() {
  setPairHead("Telegram", true);
  if (!state?.alerts?.telegram_key) return telegramMakeBot();
  showPair(telegramProgress(2), waitingNode("Making a link to your bot"));
  let start;
  try {
    start = await postJson("/api/pair/start", { kind: "telegram" });
  } catch (error) {
    return showPair(telegramProgress(2), para(error.message, "note error"),
      button("Use a different bot", "pill-button small ghost", telegramMakeBot, "send"));
  }
  const name = `@${start.bot.username}`;
  const status = waitingNode(`Waiting for you to tap Start in ${name}`);
  const nodes = [telegramProgress(2)];
  if (onPhone()) {
    const open = element("a", "pill-button big", null);
    open.href = start.url;
    open.target = "_blank";
    open.rel = "noopener";
    open.append(icon("send"), `Open ${name}`);
    nodes.push(para(`Open your bot and tap Start. Then come back here.`), open, status);
  } else {
    const grid = element("div", "pair-grid");
    const words = element("div");
    const steps = element("ol", "mini-steps");
    steps.append(element("li", null, "Point your phone's camera at the code."),
      element("li", null, `Telegram opens ${name}.`), element("li", null, "Tap Start."));
    words.append(element("b", "pair-lead", "Say hello to your bot"), steps, linkRow(start.url, "Open Telegram on this computer"));
    grid.append(makeQr(start.url, `QR code that opens ${name} in Telegram`), words);
    nodes.push(grid, status);
  }
  nodes.push(button("Use a different bot", "link-button", telegramMakeBot));
  waitForPairing(start.token, status, telegramConnect);
  showPair(...nodes);
}

function pairEmail() {
  stopWaiting();
  setPairHead("Email", true);
  if (!state?.alerts?.email_ready) {
    const more = element("details", "pair-other");
    more.append(element("summary", null, "For the person who runs this server"),
      para("Add these lines to actions.env. The sender must be verified in Amazon SES.", "note"),
      element("pre", "code-block", "AWS_SES_ACCESS_KEY_ID=…\nAWS_SES_SECRET_ACCESS_KEY=…\nAWS_SES_REGION=ap-south-1\nWHENEVER_MAIL_FROM=Whenever <alerts@your.domain>"),
      para("On a Mac, AWS_SES_PROFILE=name can use a profile from ~/.aws in place of the two keys.", "note"));
    return showPair(para("Email needs a mail server on this Whenever, and this one has none yet. Pick another place for now."), more);
  }
  const form = element("form", "email-form");
  const field = element("input");
  field.type = "email";
  field.required = true;
  field.autocomplete = "email";
  field.placeholder = "you@example.com";
  field.setAttribute("aria-label", "Your email address");
  const send = element("button", "pill-button", "Send the link");
  send.type = "submit";
  const result = element("p", "note", "");
  form.append(field, send);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    send.disabled = true;
    result.className = "note";
    result.textContent = "Sending";
    try {
      const reply = await postJson("/api/pair/email", { address: field.value });
      emailSent(field.value, reply.token);
    } catch (error) {
      result.className = "note error";
      result.textContent = error.message;
      send.disabled = false;
    }
  });
  showPair(para("Whenever sends one message with a button. Alerts start when you tap it, so a typo reaches nobody."),
    form, result);
  field.focus();
}

function emailSent(address, token) {
  const status = waitingNode("Waiting for you to tap the button in the email");
  const head = element("div", "pair-check");
  head.append(icon("mail"), element("b", null, "Check your inbox"));
  const row = element("div", "pair-result");
  row.append(button("Use another address", "link-button", pairEmail));
  waitForPairing(token, status, pairEmail);
  const where = state?.hosted ? "Open it on any device" : "Open it on this computer, because its button links here";
  showPair(head, para(`We sent a message to ${address}. ${where}, and tap Yes, send alerts here.`), status,
    para("Not there after a minute? Look in Spam or Promotions.", "note"), row);
}

async function pairNtfy() {
  stopWaiting();
  setPairHead("ntfy app", true);
  showPair(waitingNode("Getting your private topic"));
  let topic;
  try {
    topic = (await postJson("/api/phone/topic")).topic;
  } catch (error) {
    return showPair(para(error.message, "note error"));
  }
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
  if (onPhone()) {
    const open = element("a", "pill-button", null);
    open.href = link;
    open.append(icon("external"), "Open in ntfy");
    two.append(para("Open it in the app, then tap Subscribe."), open);
  } else {
    two.append(para("Scan this with your phone's camera, or add the topic below in the app."), makeQr(link));
  }
  two.append(copyLine("Topic", topic));
  const three = element("li");
  const result = element("div", "pair-result");
  const send = button("Send a test alert", "pill-button", async () => {
    send.disabled = true;
    result.replaceChildren(para("Sending", "note"));
    try {
      await postJson("/api/phone/paired", { paired: true });
      const reply = await postJson("/api/places/ntfy/test");
      if (!reply.ok) throw new Error(reply.results?.[0]?.detail || "ntfy did not take it. Try again.");
      const yes = button("Yes, it arrived", "pill-button", async () => {
        await refreshState();
        showPaired({ kind: "ntfy", id: "ntfy", label: "ntfy app" });
      }, "check");
      const no = button("Not yet", "link-button", async () => {
        await fetch("/api/places/ntfy", { method: "DELETE" });
        result.replaceChildren(para("Check that the app follows exactly this topic, and that notifications are on for ntfy. Then send another test."));
        send.disabled = false;
      });
      result.replaceChildren(para("Did the alert arrive on your phone?"), yes, no);
    } catch (error) {
      result.replaceChildren(para(error.message, "note error"));
      send.disabled = false;
    }
  }, "bell");
  three.append(element("b", null, "Send a test alert"), para("When it arrives, your Whenevers can alert you too."), send, result);
  steps.append(one, two, three);
  showPair(steps);
}

function showPaired(place) {
  stopWaiting();
  setPairHead("Paired", false);
  const head = element("div", "pair-done");
  const mark = element("span", "done-mark");
  mark.append(icon("check"));
  head.append(mark, element("h3", null, `Alerts reach ${place?.label || "you"} now`));
  const text = place?.kind === "email" ? "Each alert arrives as a message, with its picture."
    : place?.kind === "ntfy" ? "Each alert shows in the ntfy app, with its picture."
      : "A test alert went there just now. Each alert shows what happened, with the picture that proves it.";
  const row = element("div", "pair-result");
  const test = button("Send another test", "pill-button small ghost", async () => {
    test.disabled = true;
    try {
      const reply = await postJson(`/api/places/${encodeURIComponent(place.id)}/test`);
      flash(reply.ok ? "Sent." : reply.results?.[0]?.detail || "It did not work.");
    } catch (error) {
      flash(error.message);
    } finally {
      test.disabled = false;
    }
  }, "bell");
  row.append(button("Done", "pill-button", finishPairing, "check"), test, button("Add another place", "link-button", showChoices));
  showPair(head, para(text), row);
}

const PAIR_FLOWS = { push: pairPush, telegram: () => telegramConnect(), email: pairEmail, ntfy: pairNtfy };

$("pair-close").addEventListener("click", closePairing);
$("pair-back").addEventListener("click", showChoices);
$("pair").addEventListener("click", (event) => { if (event.target === $("pair")) closePairing(); });
$("pair").addEventListener("close", stopWaiting);

// A tap on a browser alert focuses this page and names the screen to show.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (typeof event.data?.open === "string" && event.data.open.startsWith("#/")) location.hash = event.data.open;
  });
}

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

function sinceText(t) {
  return t ? `since ${dayName(t)}` : "";
}

function renderPhoneCard(force) {
  const places = state?.alerts?.places || [];
  const key = JSON.stringify(places);
  if (!force && keys.phone === key) return;
  keys.phone = key;
  const card = element("section", "card phone-card");
  card.dataset.hue = "indigo";
  if (!places.length) {
    card.append(element("h2", null, "Get alerts on your phone"),
      element("p", null, "Pair once, in about a minute: a browser, Telegram, email or the ntfy app. Then any Whenever can alert you, with the picture that proves it."));
    const row = element("div", "form-row");
    row.append(button("Pair your phone", "pill-button", () => openPairing(), "phone"));
    card.append(row);
    $("phone-card").replaceChildren(card);
    return;
  }
  card.append(element("h2", null, "Where alerts go"),
    element("p", null, "Each alert from a Whenever goes to every place here, with its picture. Name a place, such as Waiter or Manager, and a Whenever can alert only that person."));
  const list = element("ul", "places");
  for (const place of places) {
    const item = element("li", "place");
    const badge = element("span", "badge");
    badge.append(icon(PLACE_ICON[place.kind] || "bell"));
    const words = element("span", "place-words");
    const kind = PLACE_KIND[place.kind] !== place.label ? PLACE_KIND[place.kind] : "";
    words.append(element("b", null, place.label), element("small", null, [kind, sinceText(place.t)].filter(Boolean).join(" · ")));
    const test = button("Test", "pill-button small ghost", async () => {
      test.disabled = true;
      try {
        const reply = await postJson(`/api/places/${encodeURIComponent(place.id)}/test`);
        flash(reply.ok ? `Sent to ${place.label}.` : reply.results?.[0]?.detail || "It did not work.");
      } catch (error) {
        flash(error.message);
      } finally {
        test.disabled = false;
      }
    }, "bell");
    let armed = false;
    const remove = button("Remove", "link-button", async () => {
      if (!armed) {
        armed = true;
        remove.textContent = "Tap again to remove";
        setTimeout(() => { armed = false; remove.textContent = "Remove"; }, 3500);
        return;
      }
      await fetch(`/api/places/${encodeURIComponent(place.id)}`, { method: "DELETE" });
      refreshState();
    });
    const name = button("Name", "link-button", async () => {
      const words = prompt("Name this place, such as Waiter or Manager. A Whenever can send alerts to it by this name.", place.label);
      if (words == null) return;
      try {
        await postJson(`/api/places/${encodeURIComponent(place.id)}/name`, { name: words });
        renderPhoneCard(true);
        refreshState();
      } catch (error) {
        flash(error.message);
      }
    });
    const buttons = element("span", "place-buttons");
    buttons.append(test, name, remove);
    item.append(badge, words, buttons);
    list.append(item);
  }
  const row = element("div", "form-row");
  row.append(button("Add a place", "pill-button small", () => openPairing(), "plus"));
  card.append(list, row);
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
  const places = state?.alerts?.places || [];
  const key = JSON.stringify([actions, controls.map((c) => [c.name, c.moves, c.light]), places.length]);
  if (key === actionsKey) return;
  actionsKey = key;
  const items = [];
  for (const action of actions) {
    const isPhone = action.kind === "phone";
    const ready = !action.missing.length;
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
      stateText: isPhone ? (ready ? `${places.length} ${places.length === 1 ? "place" : "places"}` : "Not paired")
        : ready ? "Ready" : `Needs ${action.missing.join(", ")}`,
      lines: isPhone ? [action.description, ...(places.length ? [] : ["Pair a phone, Telegram, email or ntfy first."])]
        : [action.description, `${action.method} to ${action.host}. Inputs: ${inputsText(action)}.`],
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

// The cameras of the chosen Whenever, as places on the wall.
function focusTiles(rule) {
  const [columns, rows] = state?.wall || [1, 1];
  if (!liveCamera || columns * rows <= 1) return [];
  const names = rule.focus_cameras?.length ? rule.focus_cameras : rule.cameras || [];
  if (!names.length || names.length >= (state?.cameras || []).length) return [];
  return names.map((name) => (state?.cameras || []).indexOf(name)).filter((index) => index >= 0)
    .map((index) => ({ index, name: state.cameras[index], col: index % columns, row: Math.floor(index / columns), columns, rows }));
}

// Copies the chosen cameras out of the live wall, side by side, while Live shows them.
let focusDrawing = false;
function drawFocusView() {
  if (focusDrawing) return;
  focusDrawing = true;
  let last = 0;
  const step = (now) => {
    const view = $("focus-view");
    if (!view || screen !== "live" || !liveCanvas.width) {
      focusDrawing = false;
      return;
    }
    if (now - last > 60) {
      last = now;
      const tiles = JSON.parse(view.dataset.tiles || "[]");
      const { columns, rows } = tiles[0];
      const tileW = liveCanvas.width / columns;
      const tileH = liveCanvas.height / rows;
      const across = Math.min(2, tiles.length);
      const width = Math.round(tileW * across);
      const height = Math.round(tileH * Math.ceil(tiles.length / across));
      if (view.width !== width || view.height !== height) {
        view.width = width;
        view.height = height;
      }
      const context = view.getContext("2d");
      tiles.forEach((tile, i) => {
        context.drawImage(liveCanvas, tile.col * tileW, tile.row * tileH, tileW, tileH,
          (i % across) * tileW, Math.floor(i / across) * tileH, tileW, tileH);
      });
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function renderFocus(force) {
  const box = $("focus");
  const spot = $("tile-focus");
  const rule = focusId && rules.find((r) => r.id === focusId);
  const seen = rule && rule.sensors.some((s) => s.on);
  const tiles = rule ? focusTiles(rule) : [];
  const key = JSON.stringify(rule ? [rule.id, rule.paused, seen, rule.fired, rule.today, rule.status, rule.cameras,
    state?.wall, tiles.map((t) => t.index)] : null);
  if (!force && key === keys.focus) return;
  keys.focus = key;
  // With a Whenever chosen, Live shows only its cameras, large. The whole wall returns with Show all cameras.
  document.querySelector('[data-screen="live"]').classList.toggle("focused", tiles.length > 0);
  if (!rule) {
    box.hidden = true;
    spot.hidden = true;
    return;
  }
  const card = element("div", "card focus-card" + (rule.paused ? " stopped" : ""));
  card.dataset.hue = hueOf(rule);
  const top = element("div", "card-top");
  const state_ = element("span", "focus-state" + (seen ? " seen" : ""));
  state_.append(element("i", "live-dot"), rule.paused ? "Stopped" : seen ? "Seen now"
    : rule.status?.level === "warn" ? rule.status.text : `Looking · ${watchingText(rule).replace(/^Watching/, "on")}`);
  top.append(state_, toggleSwitch(rule));
  card.append(top, element("h2", "card-name", rule.name));
  for (const sensor of rule.sensors) card.append(sensorRow(rule, sensor));
  const foot = element("div", "card-foot");
  const open = element("a", null, "Open the Whenever");
  open.href = `#/whenever/${rule.id}`;
  const all = element("a", null, "Show all cameras");
  all.href = "#/live";
  const today = rule.today ?? 0;
  foot.append(element("span", null, `${today} ${today === 1 ? "time" : "times"} today`), open, all);
  card.append(foot);
  const nodes = [card];
  if (tiles.length) {
    const view = document.createElement("canvas");
    view.id = "focus-view";
    view.className = "focus-view";
    view.dataset.tiles = JSON.stringify(tiles);
    view.setAttribute("aria-label", `Live: ${joinList(tiles.map((t) => t.name))}`);
    nodes.push(view);
  }
  box.replaceChildren(...nodes);
  box.hidden = false;
  if (tiles.length) drawFocusView();
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

// No camera says whether it moves, so Whenever judges it and the person can set it.
const MAKE_WORDS = { ezviz: "EZVIZ", rtsp: "RTSP camera", video: "Video file" };

// Each camera's own picture, cut from the live wall, so a person knows which camera is which.
function paintCameraThumbs() {
  if (!liveCanvas.width) return;
  const [columns, rows] = state?.wall || [1, 1];
  const tileW = liveCanvas.width / columns;
  const tileH = liveCanvas.height / rows;
  for (const thumb of document.querySelectorAll(".camera-thumb")) {
    const index = (state?.cameras || []).indexOf(thumb.dataset.camera);
    if (index < 0) continue;
    thumb.width = 192;
    thumb.height = 108;
    thumb.getContext("2d").drawImage(liveCanvas, (index % columns) * tileW, Math.floor(index / columns) * tileH,
      tileW, tileH, 0, 0, 192, 108);
  }
}
setInterval(() => { if (screen === "cameras") paintCameraThumbs(); }, 3000);

function renderCameraKinds() {
  const cameras = state?.camera_kinds || [];
  const key = JSON.stringify(cameras);
  if (key === keys.kinds) return;
  keys.kinds = key;
  setTimeout(paintCameraThumbs, 50);
  $("kinds-block").hidden = cameras.length < 1;
  $("camera-kinds").replaceChildren(...cameras.map((camera) => {
    const row = element("li", "camera-kind" + (camera.removed ? " removed" : ""));
    if (camera.removed) {
      const words = element("span", "place-words");
      words.append(element("b", null, camera.name), element("small", null, "Removed. Whenever does not watch it."));
      row.append(words, button("Add back", "pill-button small ghost", async () => {
        await postJson("/api/cameras/restore", { camera: camera.name }).catch((e) => flash(e.message));
        flash(camera.name === "Phone camera" ? "Scan a new code with the phone to use it again." : `${camera.name} comes back in a few seconds.`);
        refreshState();
      }, "plus"));
      return row;
    }
    const words = element("span", "place-words");
    const live = element("small", camera.live === false ? "camera-state off" : "camera-state on");
    live.append(element("i", "live-dot"), camera.live === false ? "Offline" : "Live");
    words.append(element("b", null, camera.name), live, element("small", null,
      [MAKE_WORDS[camera.make], camera.why].filter(Boolean).join(" · ")));
    const thumb = document.createElement("canvas");
    thumb.className = "camera-thumb";
    thumb.dataset.camera = camera.name;
    thumb.setAttribute("aria-hidden", "true");
    row.append(thumb);
    const choice = element("div", "segmented small-seg");
    choice.setAttribute("role", "radiogroup");
    choice.setAttribute("aria-label", `${camera.name}: fixed or moving`);
    for (const [kind, label] of [["fixed", "Stays still"], ["moving", "Moves"]]) {
      const option = button(label, "", async () => {
        try {
          await postJson("/api/cameras/kind", { camera: camera.name, kind });
          refreshState();
        } catch (error) {
          flash(error.message);
        }
      });
      option.setAttribute("role", "radio");
      option.setAttribute("aria-selected", String(camera.kind === kind));
      option.setAttribute("aria-checked", String(camera.kind === kind));
      choice.append(option);
    }
    let armed = false;
    const remove = button("Remove", "link-button", async () => {
      if (!armed) {
        armed = true;
        remove.textContent = `Tap again to remove ${camera.name}`;
        setTimeout(() => { armed = false; remove.textContent = "Remove"; }, 3500);
        return;
      }
      await postJson("/api/cameras/remove", { camera: camera.name }).catch((e) => flash(e.message));
      flash(`${camera.name} stops in a few seconds.`);
      refreshState();
    });
    row.append(words, choice, remove);
    if (camera.by === "person") {
      row.append(button("Let Whenever judge", "link-button", async () => {
        await postJson("/api/cameras/kind", { camera: camera.name, kind: "auto" }).catch(() => {});
        refreshState();
      }));
    }
    return row;
  }));
}

/* ---------- Live overlay: who and what Whenever sees, drawn on the picture ---------- */

// The analyzer says about twice a second where each person, table and thing is in each camera. The page
// keeps the last seconds of it and draws the moment that the picture shows: a camera's HLS video runs a
// few seconds behind the server, and its player says by how much. The live wall has no such delay.
const HUD_POLL_MS = 450;
const HUD_KEEP_S = 12;
const HLS_BEHIND_S = 0.3;      // the analyzer's frame is a little older than the live edge it was read at
const HEAT_KEEP_MS = 30000;
const FLOOR_MS = 2000;
const CLIP_BEFORE_S = 4;       // the server's clips start this long before their moment
const ROLE_COLOR = { staff: "#b69dff", vip: "#f6c445", banned: "#ff5b4f", regular: "#3ed8b6", "": "#3ed8b6" };
const ROLE_WORD = { staff: "Staff", vip: "VIP", banned: "Banned", regular: "Regular", "": "Guest" };
const ROLES = [["staff", "Staff"], ["regular", "Regular"], ["vip", "VIP"], ["banned", "Banned"]];
const PLACE_COLOR = { free: "#5fd690", busy: "#f6b13c", waiting: "#f6b13c", help: "#ff5b4f" };
const PLACE_WORD = { free: "Free", busy: "Busy", waiting: "Waiting", help: "Needs help" };
const EVENT_COLOR = { seated: "#5fd690", left: "#9a9ca3", back: "#3ed8b6", hand_up: "#ff5b4f", told: "#b69dff", alert: "#f6b13c" };
const HUD_LAYERS = [["people", "People"], ["names", "Names"], ["tables", "Tables"], ["trails", "Trails"], ["heat", "Heat"],
  ["clean", "Clean view"]];
const HUD_INK = "#f4f3ef";
const HUD_DIM = "rgba(244, 243, 239, 0.64)";
const stillMotion = matchMedia("(prefers-reduced-motion: reduce)");
const livePlayers = new Map();   // each camera's own player, while the page plays the media server's streams
const hudCanvas = $("hud");

// A person's choices on this device. Storage can be off, so each read and write may fail quietly.
function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

// True when this device saved a choice for the key, even the choice "none".
function chosenBefore(key) {
  try { return localStorage.getItem(key) !== null; } catch { return false; }
}

const hud = {
  layers: { people: true, names: true, tables: true, trails: true, heat: false, clean: false, ...stored("whenever-hud", {}) },
  camera: stored("whenever-camera", null),   // the camera shown large, or null for the whole wall
  view: stored("whenever-live-view", "picture"),
  snaps: [], events: new Map(), totals: null, offset: null, after: 0,
  ok: false, busy: false, failures: 0, failedAt: 0,
  tracks: new Map(), heat: new Map(), floor: null, floorT: 0, floorAt: 0, floorBusy: false,
  drawing: false, lastDraw: 0, moment: null, clock: "", keys: {},
};

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const lerp = (a, b, f) => a + (b - a) * f;

function mmss(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n) => String(n).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${Math.floor(s / 60)}:${pad(s % 60)}`;
}

function ordinal(n) {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th";
  return `${n}${tail}`;
}

function stayWords(seconds) {
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

function plural(count, one, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

function serverNow() {
  return Date.now() / 1000 + (hud.offset || 0);
}

function siteOf(name) {
  return Object.entries(state?.sites || {}).find(([, names]) => names.includes(name))?.[0] || null;
}

// The camera shown large. The whole wall stays behind it, so pointing and outlines keep wall coordinates.
function shownTile() {
  const names = state?.cameras || [];
  const [columns, rows] = state?.wall || [1, 1];
  const index = names.indexOf(hud.camera);
  if (!liveCamera || focusId || columns * rows <= 1 || index < 0) return null;
  return { index, name: hud.camera, columns, rows, col: index % columns, row: Math.floor(index / columns) };
}

// Where a camera sits on the wall, as parts of the wall's width and height.
function wallSpot(name) {
  const names = state?.cameras || [];
  const [columns, rows] = liveCamera ? state?.wall || [1, 1] : [1, 1];
  if (columns * rows <= 1 || names.length <= 1) return { x: 0, y: 0, w: 1, h: 1 };
  const index = names.indexOf(name);
  if (index < 0) return null;
  return { x: (index % columns) / columns, y: Math.floor(index / columns) / rows, w: 1 / columns, h: 1 / rows };
}

function hasPlaces() {
  return !!hud.floor?.length || hud.snaps.some((s) => Object.values(s.cameras || {}).some((c) => c.places?.length));
}

function mapShown() {
  return hud.view === "map" && hud.ok && hasPlaces();
}

async function pollOverlay() {
  if (screen !== "live" || document.hidden || hud.busy || cameraMode) return;
  // A server without an overlay is asked again now and then, not twice a second.
  if (hud.failedAt && Date.now() - hud.failedAt < 15000) return;
  hud.busy = true;
  const tile = mapShown() ? null : shownTile();
  const sent = Date.now() / 1000;
  try {
    const response = await fetch(`/api/overlay?cameras=${encodeURIComponent(tile ? tile.name : "")}&after=${hud.after}`);
    if (response.status === 401) return location.reload();
    if (response.status === 404) {
      hud.ok = false;
      hud.failedAt = Date.now();
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    const got = Date.now() / 1000;
    if (!Number.isFinite(data.t)) data.t = got;
    const offset = data.t - (sent + got) / 2;
    hud.offset = hud.offset == null ? offset : hud.offset * 0.85 + offset * 0.15;
    for (const camera of Object.values(data.cameras || {})) camera.t ??= data.t;
    hud.snaps.push(data);
    while (hud.snaps.length > 2 && hud.snaps[0].t < data.t - HUD_KEEP_S) hud.snaps.shift();
    for (const e of data.events || []) hud.events.set(`${Math.round(e.t * 10)}|${e.kind}|${e.text}`, e);
    for (const [key, e] of hud.events) if (e.t < data.t - 3600) hud.events.delete(key);
    for (const e of hud.events.values()) hud.after = Math.max(hud.after, e.t);
    hud.totals = data.totals || null;
    hud.ok = Object.keys(data.cameras || {}).length > 0 || hud.events.size > 0;
    hud.failures = 0;
    hud.failedAt = 0;
  } catch {
    // One missed answer keeps the last picture. Several in a row hide the overlay.
    if (++hud.failures >= 4) hud.ok = false;
  } finally {
    hud.busy = false;
  }
  renderHud();
}

function renderHud() {
  const live = document.querySelector('[data-screen="live"]');
  const on = hud.ok && !cameraMode;
  const map = on && mapShown();
  live.classList.toggle("has-hud", on);
  if (live.classList.contains("map-view") !== map) {
    live.classList.toggle("map-view", map);
    shapeFrame();
  }
  $("hud-stats").hidden = !on;
  $("hud-timeline").hidden = !on;
  $("hud-layers").hidden = !on || map;
  $("floor").hidden = !map;
  renderViewBar();
  if (!on) return clearHud();
  renderHudStats();
  renderTimeline();
  renderLayers();
  if (map) renderFloor();
  startHudDraw();
}

// Chips choose what Live shows large: the whole wall, or one camera, grouped by site when there are sites.
function renderViewBar(force) {
  const names = state?.cameras || [];
  if (hud.camera && names.length && !names.includes(hud.camera)) hud.camera = null;
  // A demo opens on its busiest camera, so a first visitor sees the overlay large, not six small tiles.
  if (state?.demo && !hud.demoPicked && !chosenBefore("whenever-camera") && names.includes("Cafe")) {
    hud.demoPicked = true;
    hud.camera = "Cafe";
  }
  const sites = Object.keys(state?.sites || {}).length ? state.sites : null;
  const pick = liveCamera && names.length > 1 && !cameraMode;
  const views = hud.ok && hasPlaces();
  const key = JSON.stringify([pick, names, sites, hud.camera, mapShown(), views, state?.wall]);
  if (!force && key === hud.keys.bar) return;
  hud.keys.bar = key;
  $("view-bar").hidden = !pick && !views;
  const shown = mapShown() ? undefined : hud.camera || null;
  const chip = (name, label) => {
    const b = button(label, "chip", () => chooseCamera(name));
    b.setAttribute("aria-pressed", String(shown === name));
    return b;
  };
  const nodes = [];
  if (pick) {
    nodes.push(chip(null, "All"));
    const groups = sites ? Object.entries(sites).map(([site, list]) => [site, list.filter((n) => names.includes(n))]) : [["", names]];
    const listed = new Set(groups.flatMap(([, list]) => list));
    const rest = names.filter((n) => !listed.has(n));
    if (rest.length) groups.push([sites ? "Other" : "", rest]);
    for (const [site, list] of groups) {
      if (!list.length) continue;
      const group = element("span", "camera-group");
      // A site of one camera with the same name needs no label.
      if (site && !(list.length === 1 && list[0] === site)) group.append(element("span", "site-label", site));
      group.append(...list.map((n) => chip(n, n)));
      nodes.push(group);
    }
  }
  $("camera-pick").replaceChildren(...nodes);
  // On a phone the row scrolls, so the chosen camera stays in view.
  requestAnimationFrame(() => {
    const row = $("camera-pick");
    const on = row.querySelector('[aria-pressed="true"]');
    if (!on || row.scrollWidth <= row.clientWidth) return;
    const a = row.getBoundingClientRect();
    const b = on.getBoundingClientRect();
    row.scrollLeft += b.left - a.left - (a.width - b.width) / 2;
  });
  $("view-pick").hidden = !views;
  $("view-pick").querySelectorAll("button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.liveView === (mapShown() ? "map" : "picture"))));
}

$("demo-hello-close").addEventListener("click", () => {
  store("whenever-demo-hello", true);
  $("demo-hello").hidden = true;
});

function chooseCamera(name) {
  hud.camera = name;
  store("whenever-camera", name);
  if (hud.view !== "picture") {
    hud.view = "picture";
    store("whenever-live-view", "picture");
  }
  $("frame-wrap").scrollLeft = 0;
  renderHud();
  shapeFrame();
  renderViewBar(true);
  renderHudStats(true);
  renderTimeline(true);
}

$("view-pick").addEventListener("click", (event) => {
  const b = event.target.closest("[data-live-view]");
  if (!b) return;
  hud.view = b.dataset.liveView;
  store("whenever-live-view", hud.view);
  renderHud();
  renderViewBar(true);
});

function renderHudStats(force) {
  const t = hud.totals || {};
  const tile = mapShown() ? null : shownTile();
  const site = tile && siteOf(tile.name);
  const where = mapShown() ? "Floor map" : tile ? (site && site !== tile.name ? `${site} · ${tile.name}` : tile.name) : "All cameras";
  const parts = [];
  if (t.guests != null) parts.push([t.guests, t.guests === 1 ? "guest" : "guests"]);
  if (t.staff != null) parts.push([t.staff, "staff"]);
  if (t.tables) parts.push([`${t.busy || 0}/${t.tables}`, "tables busy"]);
  if (t.longest_s) parts.push([stayWords(t.longest_s), "longest stay"]);
  if (t.spend_hour != null) parts.push([`$${Number(t.spend_hour).toFixed(2)}`, "this hour"]);
  const key = JSON.stringify([where, parts]);
  if (!force && key === hud.keys.stats) return;
  hud.keys.stats = key;
  const live = element("span", "hud-live");
  live.append(element("i"), element("b", null, "Live"), element("span", null, where));
  const stats = parts.map(([value, label]) => {
    const item = element("span", "hud-stat");
    item.append(element("b", null, String(value)), label);
    return item;
  });
  const time = element("span", "hud-clock", hud.clock);
  time.id = "hud-clock";
  $("hud-stats").replaceChildren(live, ...stats, time);
}

// The last hour as a line of dots. Labels that would run into the next one stay hidden, and a tap on a
// dot says what happened then.
function renderTimeline(force) {
  const box = $("hud-timeline");
  const now = serverNow();
  const tile = mapShown() ? null : shownTile();
  const events = [...hud.events.values()]
    .filter((e) => e.t > now - 3600 && (!tile || !e.camera || e.camera === tile.name))
    .sort((a, b) => a.t - b.t);
  const same = (e) => hud.moment && hud.moment.t === e.t && hud.moment.text === e.text;
  const key = JSON.stringify([events.map((e) => [e.t, e.kind, e.text]), Math.floor(now / 20), hud.moment?.t, hud.moment?.text,
    Math.round(box.clientWidth / 40)]);
  if (!force && key === hud.keys.timeline) return;
  hud.keys.timeline = key;
  const track = element("div", "hud-track");
  for (const e of events) {
    const dot = element("button", "hud-dot");
    dot.type = "button";
    const left = clamp(((e.t - (now - 3600)) / 3600) * 100, 0, 100);
    dot.style.left = `${left}%`;
    dot.style.setProperty("--dot", EVENT_COLOR[e.kind] || HUD_INK);
    dot.setAttribute("aria-label", `${e.text}, ${clock(e.t)}`);
    dot.classList.toggle("on", !!same(e));
    dot.classList.toggle("flip", left > 70);
    dot.append(element("span", "hud-dot-label", `${e.text} ${clock(e.t)}`));
    dot.addEventListener("click", () => {
      hud.moment = same(e) ? null : e;
      renderTimeline(true);
    });
    track.append(dot);
  }
  if (!events.length) track.append(element("span", "hud-empty", "Nothing yet in the last hour"));
  const nodes = [element("span", "hud-timeline-label", "Last hour"), track];
  if (hud.moment) nodes.push(momentNode(hud.moment));
  box.replaceChildren(...nodes);
  requestAnimationFrame(() => {
    const edge = track.getBoundingClientRect();
    const dots = [...track.querySelectorAll(".hud-dot")].map((d) => {
      const r = d.getBoundingClientRect();
      return { d, x: r.left + r.width / 2 };
    });
    const labels = dots.map(({ d }) => ({ d, label: d.querySelector(".hud-dot-label"), r: d.querySelector(".hud-dot-label").getBoundingClientRect() }))
      .sort((a, b) => a.r.left - b.r.left);
    // A label shows when it runs into no other label and covers no other dot. The newest win.
    const shown = [];
    for (const item of labels.reverse()) {
      const { r } = item;
      const fits = r.left >= edge.left - 14 && r.right <= edge.right + 14
        && !shown.some((o) => r.left < o.right + 10 && r.right > o.left - 10)
        && !dots.some(({ d, x }) => d !== item.d && x > r.left - 8 && x < r.right + 8);
      item.label.classList.toggle("hidden", !fits);
      if (fits) shown.push(r);
    }
  });
}

function momentNode(e) {
  const row = element("div", "hud-moment");
  const dot = element("i", "hud-moment-dot");
  dot.style.background = EVENT_COLOR[e.kind] || HUD_INK;
  const said = [e.text, e.person && !e.text.includes(e.person) ? e.person : "", e.camera].filter(Boolean).join(" · ");
  row.append(dot, element("b", null, clockSeconds(e.t)), element("span", "hud-moment-text", said));
  if (e.picture) {
    row.append(button("See the picture", "pill-button small", () => openFrame(e.picture, null,
      [e.camera, clock(e.t)].filter(Boolean).join(", ")), "photo"));
  }
  const names = state?.cameras || [];
  if (e.camera && names.length > 1 && names.includes(e.camera) && hud.camera !== e.camera) {
    row.append(button(`Show ${e.camera}`, "pill-button small ghost", () => chooseCamera(e.camera), "eye"));
  }
  const close = button("", "icon-button hud-moment-close", () => {
    hud.moment = null;
    renderTimeline(true);
  }, "x");
  close.setAttribute("aria-label", "Close this moment");
  row.append(close);
  return row;
}

function renderLayers() {
  const key = JSON.stringify(hud.layers);
  if (key === hud.keys.layers) return;
  hud.keys.layers = key;
  $("hud-layers").replaceChildren(...HUD_LAYERS.map(([id, label]) => {
    const chip = button(label, "chip", () => {
      hud.layers[id] = !hud.layers[id];
      store("whenever-hud", hud.layers);
      renderLayers();
    });
    chip.setAttribute("aria-pressed", String(!!hud.layers[id]));
    if (id !== "clean" && hud.layers.clean) chip.classList.add("wait");
    return chip;
  }));
}

function hudShown() {
  return screen === "live" && hud.ok && !mapShown() && !cameraMode && !!video.videoWidth
    && !document.querySelector('[data-screen="live"]').classList.contains("focused");
}

function startHudDraw() {
  if (hud.drawing || !hudShown()) return;
  hud.drawing = true;
  hud.lastDraw = performance.now();
  requestAnimationFrame(drawHud);
}

function clearHud() {
  if (hudCanvas.width) {
    hudCanvas.width = 0;
    hudCanvas.height = 0;
  }
}

// The moment of a camera that the picture shows now, on the server's clock. The analyzer reads the same
// media server near its live edge, so the player's distance from that edge is the delay. MediaMTX's
// low-latency playlists carry no program date, so a date is used only when a stream has one.
function shownTime(name) {
  const now = serverNow();
  const live = livePlayers.get(name);
  if (!live) return now;
  const date = live.hls?.playingDate;
  if (date && date.getTime() > 0) {
    const t = date.getTime() / 1000;
    if (t > now - 60 && t < now + 5) return t;
  }
  let behind = live.hls?.latency;
  if (behind == null && live.player.seekable?.length) behind = live.player.seekable.end(live.player.seekable.length - 1) - live.player.currentTime;
  return now - clamp(Number.isFinite(behind) ? behind : 2, 0, 30) - HLS_BEHIND_S;
}

// One camera at a moment: its two reports around the moment, with each box part of the way between them.
// Boxes glide toward that place, a new one fades in, and one that left fades out where it was.
function sceneAt(name, at, dt) {
  let a = null;
  let b = null;
  for (const snap of hud.snaps) {
    const camera = snap.cameras?.[name];
    if (!camera) continue;
    if (camera.t <= at) a = camera;
    else {
      b = camera;
      break;
    }
  }
  a = a || b;
  if (!a) return null;
  const f = b && b !== a ? clamp((at - a.t) / Math.max(0.05, b.t - a.t), 0, 1) : 0;
  const glide = stillMotion.matches ? 1 : 1 - Math.exp(-dt / 0.09);
  const scene = { name, people: [], things: [], places: [], age: Math.max(0, at - a.t) };
  const seen = new Set();
  for (const kind of ["places", "things", "people"]) {
    for (const item of a[kind] || []) {
      if (!item.box) continue;
      const next = b && b !== a ? (b[kind] || []).find((x) => x.id === item.id) : null;
      const box = next?.box ? item.box.map((v, i) => lerp(v, next.box[i], f)) : item.box;
      const key = `${name}|${kind}|${item.id}`;
      seen.add(key);
      let track = hud.tracks.get(key);
      if (!track) {
        track = { box: box.slice(), alpha: stillMotion.matches ? 1 : 0, camera: name, kind };
        hud.tracks.set(key, track);
      }
      track.box = kind === "places" ? box : track.box.map((v, i) => lerp(v, box[i], glide));
      track.item = item;
      track.gone = false;
      track.alpha = Math.min(1, track.alpha + dt / 0.25);
      scene[kind].push(track);
    }
  }
  for (const [key, track] of hud.tracks) {
    if (track.camera !== name || seen.has(key)) continue;
    track.alpha -= stillMotion.matches ? 1 : dt / 0.6;
    if (track.alpha <= 0) {
      hud.tracks.delete(key);
      continue;
    }
    track.gone = true;
    scene[track.kind].push(track);
  }
  return scene;
}

function drawHud(now) {
  if (!hudShown()) {
    hud.drawing = false;
    clearHud();
    return;
  }
  requestAnimationFrame(drawHud);
  const dt = Math.min(0.25, Math.max(0, (now - hud.lastDraw) / 1000));
  hud.lastDraw = now;
  // The canvas covers only the part of the wall in view, so a camera shown large costs no more to draw.
  const area = videoArea();
  const tile = shownTile();
  const view = tile ? { x: tile.col / tile.columns, y: tile.row / tile.rows, w: 1 / tile.columns, h: 1 / tile.rows }
    : { x: 0, y: 0, w: 1, h: 1 };
  const width = view.w * area.width;
  const height = view.h * area.height;
  if (!(width > 0 && height > 0)) return;
  const place = [`${area.left + view.x * area.width}px`, `${area.top + view.y * area.height}px`, `${width}px`, `${height}px`];
  if (hudCanvas.dataset.place !== place.join()) {
    hudCanvas.dataset.place = place.join();
    [hudCanvas.style.left, hudCanvas.style.top, hudCanvas.style.width, hudCanvas.style.height] = place;
  }
  const ratio = Math.min(devicePixelRatio || 1, 2);
  const pw = Math.round(width * ratio);
  const ph = Math.round(height * ratio);
  if (hudCanvas.width !== pw || hudCanvas.height !== ph) {
    hudCanvas.width = pw;
    hudCanvas.height = ph;
  }
  const ctx = hudCanvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const mirror = $("frame").classList.contains("mirror");
  const names = new Set(hud.snaps.flatMap((s) => Object.keys(s.cameras || {})));
  const scenes = [];
  let clockAt = null;
  for (const name of names) {
    if (tile && name !== tile.name) continue;
    const spot = wallSpot(name);
    if (!spot) continue;
    const rect = { x: ((spot.x - view.x) / view.w) * width, y: ((spot.y - view.y) / view.h) * height,
      w: (spot.w / view.w) * width, h: (spot.h / view.h) * height };
    if (mirror) rect.x = width - rect.x - rect.w;
    const at = shownTime(name);
    clockAt ??= at;
    const scene = sceneAt(name, at, dt);
    if (scene) scenes.push({ rect, scene, mirror });
  }
  if (!hud.layers.clean) drawScenes(ctx, scenes);
  const text = clockAt ? clockSeconds(clockAt) : "";
  if (text !== hud.clock) {
    hud.clock = text;
    const el = $("hud-clock");
    if (el) el.textContent = text;
  }
}

function hudFont(size, weight = 700) {
  return `${weight} ${size.toFixed(1)}px "Schibsted Grotesk", ui-sans-serif, system-ui, sans-serif`;
}

function rounded(ctx, x, y, w, h, r) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}

function tint(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function boxRect(box, rect, mirror) {
  const x1 = mirror ? 1 - box[2] : box[0];
  const x2 = mirror ? 1 - box[0] : box[2];
  return { x: rect.x + x1 * rect.w, y: rect.y + box[1] * rect.h, w: (x2 - x1) * rect.w, h: (box[3] - box[1]) * rect.h };
}

// How much a camera's chips say: everything on a large camera, less on a small one, none on a tiny one.
function detailOf(width) {
  return width >= 480 ? 3 : width >= 240 ? 2 : width >= 150 ? 1 : 0;
}

function drawScenes(ctx, scenes) {
  const chips = [];
  for (const { rect, scene, mirror } of scenes) {
    const level = detailOf(rect.w);
    const size = clamp(rect.w / 50, 10, 13);
    const age = scene.age;
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x, rect.y, rect.w, rect.h);
    ctx.clip();
    if (hud.layers.heat) drawHeat(ctx, scene.name, rect, mirror);
    if (hud.layers.tables) {
      for (const track of scene.places) drawPlace(ctx, track, boxRect(track.box, rect, mirror), size, level, age, chips, rect);
    }
    if (hud.layers.people) {
      if (hud.layers.trails) for (const track of scene.people) drawTrail(ctx, track, rect, mirror);
      for (const track of scene.things) drawThing(ctx, track, boxRect(track.box, rect, mirror), size, level, age, chips, rect);
      for (const track of scene.people) drawPerson(ctx, track, boxRect(track.box, rect, mirror), size, level, age, chips, rect);
    }
    ctx.restore();
  }
  flushChips(ctx, chips);
}

// Chips go last, the most important first, each where it hides the fewest others. A minor chip that
// would sit mostly on another one is left out.
function flushChips(ctx, chips) {
  const placed = [];
  chips.sort((a, b) => b.rank - a.rank);
  for (const chip of chips) {
    const { w, h } = chip.layout;
    let best = null;
    let bestHit = Infinity;
    for (const [tx, ty] of chip.tries) {
      const spot = { x: clamp(tx, chip.bounds.x + 3, chip.bounds.x + chip.bounds.w - w - 3),
        y: clamp(ty, chip.bounds.y + 3, chip.bounds.y + chip.bounds.h - h - 3), w, h };
      let hit = 0;
      for (const o of placed) {
        hit += Math.max(0, Math.min(o.x + o.w, spot.x + w) - Math.max(o.x, spot.x))
          * Math.max(0, Math.min(o.y + o.h, spot.y + h) - Math.max(o.y, spot.y));
      }
      if (hit < bestHit) {
        best = spot;
        bestHit = hit;
      }
      if (!hit) break;
    }
    if (bestHit > w * h * 0.45 && chip.rank < 3) continue;
    placed.push(best);
    chip.spot = best;
  }
  for (const chip of chips.reverse()) if (chip.spot) drawChip(ctx, chip.spot.x, chip.spot.y, chip.layout, chip.accent, chip.alpha);
}

// A chip: a dark pill, a bar of its colour at the left, and its words, each part in its own colour.
// Timers keep each digit the same width, so they do not shake as they count.
function layoutChip(ctx, parts, size) {
  const gap = size * 0.42;
  const out = [];
  let width = size * 0.6 + 3 + size * 0.5;
  for (const part of parts) {
    if (!part || part.text == null || part.text === "") continue;
    const fs = part.small ? size * 0.86 : size;
    ctx.font = hudFont(fs, part.weight || 700);
    const digit = ctx.measureText("0").width;
    const w = part.timer ? [...part.text].reduce((s, ch) => s + (/\d/.test(ch) ? digit : ctx.measureText(ch).width), 0)
      : ctx.measureText(part.text).width;
    out.push({ ...part, fs, w });
    width += w + gap;
  }
  width += size * 0.7 - gap;
  return { parts: out, w: Math.ceil(width), h: Math.round(size * 1.9), size };
}

function drawChip(ctx, x, y, chip, accent, alpha = 1) {
  const { w, h, size } = chip;
  ctx.save();
  ctx.globalAlpha = alpha;
  rounded(ctx, x, y, w, h, Math.min(h / 2, size * 0.55));
  ctx.fillStyle = "rgba(12, 13, 15, 0.82)";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
  ctx.lineWidth = 1;
  ctx.stroke();
  const barX = x + size * 0.6;
  rounded(ctx, barX, y + h * 0.24, 3, h * 0.52, 1.5);
  ctx.fillStyle = accent;
  ctx.fill();
  let left = barX + 3 + size * 0.5;
  ctx.textBaseline = "middle";
  const middle = y + h / 2 + size * 0.06;
  for (const part of chip.parts) {
    ctx.font = hudFont(part.fs, part.weight || 700);
    ctx.fillStyle = part.color || HUD_INK;
    if (part.timer) {
      const digit = ctx.measureText("0").width;
      ctx.textAlign = "center";
      let px = left;
      for (const ch of part.text) {
        const cw = /\d/.test(ch) ? digit : ctx.measureText(ch).width;
        ctx.fillText(ch, px + cw / 2, middle);
        px += cw;
      }
      ctx.textAlign = "left";
    } else {
      ctx.fillText(part.text, left, middle);
    }
    left += part.w + size * 0.42;
  }
  ctx.restore();
}

function drawBrackets(ctx, r, color, alpha, width) {
  const len = Math.max(5, Math.min(r.w, r.h) * 0.24);
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.shadowColor = "rgba(0, 0, 0, 0.55)";
  ctx.shadowBlur = 3;
  ctx.beginPath();
  ctx.moveTo(r.x, r.y + len); ctx.lineTo(r.x, r.y); ctx.lineTo(r.x + len, r.y);
  ctx.moveTo(r.x + r.w - len, r.y); ctx.lineTo(r.x + r.w, r.y); ctx.lineTo(r.x + r.w, r.y + len);
  ctx.moveTo(r.x + r.w, r.y + r.h - len); ctx.lineTo(r.x + r.w, r.y + r.h); ctx.lineTo(r.x + r.w - len, r.y + r.h);
  ctx.moveTo(r.x + len, r.y + r.h); ctx.lineTo(r.x, r.y + r.h); ctx.lineTo(r.x, r.y + r.h - len);
  ctx.stroke();
  ctx.restore();
}

function personParts(p, level, age) {
  const role = p.role || "";
  const color = ROLE_COLOR[role] || ROLE_COLOR[""];
  const who = p.name || p.visitor || "";
  const seated = p.seated_s != null;
  const time = seated ? p.seated_s : p.stayed_s;
  const timer = time != null ? mmss(time + age) : "";
  const sure = p.sure != null ? `${Math.round(p.sure * 100)}%` : "";
  if (level >= 3) {
    return [
      { text: ROLE_WORD[role] || words(role), color, weight: 600 },
      { text: who, weight: 800 },
      { text: sure && p.by ? `${sure} ${p.by}` : sure, color },
      { text: p.visits > 1 ? `${ordinal(p.visits)} visit` : "", color: HUD_DIM, small: true },
      { text: timer ? (seated ? "seated" : "stayed") : "", color: HUD_DIM, small: true, weight: 600 },
      { text: timer, timer: true },
    ];
  }
  // On a smaller camera only people with a name or a role that matters get a chip.
  if (!p.name && !["staff", "vip", "banned"].includes(role)) return [];
  if (level === 2) return [{ text: p.name || ROLE_WORD[role], weight: 800 }, { text: sure, color }, { text: timer, timer: true, color: HUD_DIM }];
  return [{ text: p.name || (role && role !== "regular" ? ROLE_WORD[role] : ""), weight: 800 }];
}

// The largest chip that fits the camera, or none.
function fitChip(ctx, partsAt, level, size, width) {
  for (let lv = level; lv > 0; lv--) {
    const layout = layoutChip(ctx, partsAt(lv), size);
    if (layout.parts.length && layout.w <= width - 8) return layout;
  }
  return null;
}

function drawPerson(ctx, track, r, size, level, age, chips, bounds) {
  const p = track.item;
  const color = ROLE_COLOR[p.role || ""] || ROLE_COLOR[""];
  drawBrackets(ctx, r, color, track.alpha, clamp(size / 6, 1.6, 2.4));
  if (p.hand_up_s != null && !track.gone) drawHand(ctx, track, r, size, level, age, chips, bounds);
  if (!hud.layers.names || level === 0) return;
  const layout = fitChip(ctx, (lv) => personParts(p, lv, age), level, size, bounds.w);
  if (!layout) return;
  const cx = r.x + r.w / 2 - layout.w / 2;
  chips.push({ layout, accent: color, alpha: track.alpha, rank: p.role === "banned" ? 4 : p.name ? 3 : 2, bounds,
    tries: [[cx, r.y - layout.h - 5], [cx, r.y + r.h + 5], [cx, r.y + 4], [r.x + r.w + 5, r.y], [r.x - layout.w - 5, r.y]] });
}

// A hand up: a ring that pulses where the hand is, and how long it has been up.
function drawHand(ctx, track, r, size, level, age, chips, bounds) {
  const p = track.item;
  const red = ROLE_COLOR.banned;
  const x = r.x + r.w / 2;
  const y = r.y + Math.min(r.h * 0.12, size * 2);
  const radius = clamp(r.w * 0.3, size * 0.8, size * 2.2);
  ctx.save();
  ctx.strokeStyle = red;
  if (!stillMotion.matches) {
    for (const phase of [0, 0.5]) {
      const f = (performance.now() / 1600 + phase) % 1;
      ctx.globalAlpha = track.alpha * (1 - f) * 0.85;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, radius * (1 + f * 1.3), 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = track.alpha;
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = tint(red, 0.28);
  ctx.beginPath();
  ctx.arc(x, y, radius * 0.45, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  if (level === 0) return;
  const layout = fitChip(ctx, (lv) => [{ text: "Hand up", color: red, weight: 800 },
    { text: lv >= 2 ? mmss(p.hand_up_s + age) : "", timer: true },
    { text: lv >= 3 && p.told ? `${p.told} told` : "", color: HUD_DIM, small: true }], Math.max(level, 1), size, bounds.w);
  if (!layout) return;
  chips.push({ layout, accent: red, alpha: track.alpha, rank: 5, bounds,
    tries: [[x - layout.w / 2, y - radius * 1.5 - layout.h], [x + radius * 1.4, y - layout.h / 2],
      [x - radius * 1.4 - layout.w, y - layout.h / 2], [x - layout.w / 2, y + radius * 1.5]] });
}

function placeParts(p, level, age) {
  const color = PLACE_COLOR[p.state] || PLACE_COLOR.busy;
  const free = p.state === "free";
  const since = p.since_s ? mmss(p.since_s + age) : "";
  const count = p.count ? plural(p.count, p.kind && p.kind !== "table" ? "person" : "guest", p.kind && p.kind !== "table" ? "people" : "guests") : "";
  if (level >= 3) {
    return [{ text: p.name, weight: 800 }, { text: free ? "Free" : count, color: free ? color : HUD_INK, weight: free ? 700 : 600 },
      { text: free ? "" : since, timer: true, color }, { text: p.note || "", color: HUD_DIM, small: true }];
  }
  if (level === 2) return [{ text: p.name, weight: 800 }, free ? { text: "Free", color } : { text: since, timer: true, color }];
  return [{ text: p.name, weight: 800 }];
}

// A table: a soft box in the colour of its state, free, busy, waiting or needing help.
function drawPlace(ctx, track, r, size, level, age, chips, bounds) {
  const p = track.item;
  const color = PLACE_COLOR[p.state] || PLACE_COLOR.busy;
  ctx.save();
  ctx.globalAlpha = track.alpha;
  rounded(ctx, r.x, r.y, r.w, r.h, clamp(size * 0.8, 5, 12));
  ctx.fillStyle = tint(color, p.state === "free" ? 0.1 : 0.17);
  ctx.fill();
  ctx.strokeStyle = tint(color, 0.72);
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
  if (level === 0 || !p.name) return;
  const layout = fitChip(ctx, (lv) => placeParts(p, lv, age), level, size, bounds.w);
  if (!layout) return;
  const cx = r.x + r.w / 2 - layout.w / 2;
  chips.push({ layout, accent: color, alpha: track.alpha, rank: p.state === "help" ? 4 : 1, bounds,
    tries: [[cx, r.y + r.h * 0.58 - layout.h / 2], [cx, r.y + r.h - layout.h - 4], [cx, r.y + 4], [cx, r.y + r.h + 4]] });
}

function drawThing(ctx, track, r, size, level, age, chips, bounds) {
  const t = track.item;
  drawBrackets(ctx, r, "#e9e7e1", track.alpha, 1.6);
  if (level < 2) return;
  const layout = layoutChip(ctx, [{ text: words(t.label || "thing"), weight: 800 },
    { text: t.stayed_s != null ? mmss(t.stayed_s + age) : "", timer: true, color: HUD_DIM }], size * 0.92);
  chips.push({ layout, accent: "#e9e7e1", alpha: track.alpha, rank: 1.5, bounds,
    tries: [[r.x, r.y - layout.h - 4], [r.x, r.y + r.h + 4], [r.x + 3, r.y + 3]] });
}

// Where a person walked: a line that fades toward the past, ending at their feet now.
function drawTrail(ctx, track, rect, mirror) {
  const points = (track.item.trail || []).slice(-24);
  if (!points.length) return;
  points.push([(track.box[0] + track.box[2]) / 2, track.box[3]]);
  const xy = ([x, y]) => [rect.x + (mirror ? 1 - x : x) * rect.w, rect.y + y * rect.h];
  ctx.save();
  ctx.strokeStyle = ROLE_COLOR[track.item.role || ""] || ROLE_COLOR[""];
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";
  for (let i = 1; i < points.length; i++) {
    ctx.globalAlpha = track.alpha * (0.08 + 0.62 * (i / (points.length - 1)));
    const [x0, y0] = xy(points[i - 1]);
    const [x1, y1] = xy(points[i]);
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
  }
  ctx.restore();
}

function drawHeat(ctx, name, rect, mirror) {
  const heat = hud.heat.get(name);
  if (!heat || (!heat.loading && Date.now() - heat.at > HEAT_KEEP_MS)) loadHeat(name);
  if (!heat?.image) return;
  ctx.save();
  ctx.globalAlpha = 0.85;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  if (mirror) {
    ctx.translate(rect.x * 2 + rect.w, 0);
    ctx.scale(-1, 1);
  }
  ctx.drawImage(heat.image, rect.x, rect.y, rect.w, rect.h);
  ctx.restore();
}

async function loadHeat(name) {
  const old = hud.heat.get(name);
  hud.heat.set(name, { ...old, at: old?.at || 0, loading: true });
  try {
    const response = await fetch(`/api/heat?camera=${encodeURIComponent(name)}&hours=1`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    hud.heat.set(name, { at: Date.now(), image: heatImage(data.grid || [], data.max) });
  } catch {
    hud.heat.set(name, { at: Date.now(), image: null });
  }
}

// The seconds that people spent in each cell of the picture, as a soft gradient: clear where nobody
// stayed, then teal, amber and red. The small grid is drawn large, so the browser smooths it.
function heatImage(grid, max) {
  const rows = grid.length;
  const cols = grid[0]?.length || 0;
  if (!rows || !cols) return null;
  const top = max || Math.max(1, ...grid.flat());
  const canvas = document.createElement("canvas");
  canvas.width = cols;
  canvas.height = rows;
  const context = canvas.getContext("2d");
  const image = context.createImageData(cols, rows);
  const stops = [[0, [62, 216, 182, 0]], [0.2, [62, 216, 182, 0.32]], [0.55, [246, 177, 60, 0.5]], [1, [255, 91, 79, 0.7]]];
  grid.forEach((row, y) => row.forEach((value, x) => {
    const v = clamp(value / top, 0, 1) ** 0.75;
    let i = 1;
    while (i < stops.length - 1 && v > stops[i][0]) i++;
    const [p0, c0] = stops[i - 1];
    const [p1, c1] = stops[i];
    const f = clamp((v - p0) / (p1 - p0), 0, 1);
    const o = (y * cols + x) * 4;
    for (let k = 0; k < 3; k++) image.data[o + k] = lerp(c0[k], c1[k], f);
    image.data[o + 3] = lerp(c0[3], c1[3], f) * 255;
  }));
  context.putImageData(image, 0, 0);
  return canvas;
}

// The floor map: each camera's tables as tiles, free, busy with how long, waiting, or needing help.
async function loadFloor() {
  if (hud.floorBusy || Date.now() - hud.floorAt < FLOOR_MS) return;
  hud.floorBusy = true;
  try {
    const response = await fetch("/api/floor");
    if (!response.ok) throw new Error();
    hud.floor = (await response.json()).places || [];
    hud.floorT = serverNow();
  } catch {
    hud.floor = null;
  } finally {
    hud.floorAt = Date.now();
    hud.floorBusy = false;
  }
}

function floorPlaces() {
  const now = serverNow();
  if (hud.floor) return hud.floor.map((p) => ({ ...p, since: p.since_s ? p.since_s + now - hud.floorT : 0 }));
  // Without /api/floor, the newest overlay says where each table is.
  const newest = {};
  for (const snap of hud.snaps) for (const [name, camera] of Object.entries(snap.cameras || {})) newest[name] = camera;
  return Object.entries(newest).flatMap(([name, camera]) => (camera.places || [])
    .map((p) => ({ ...p, camera: name, since: p.since_s ? p.since_s + now - camera.t : 0 })));
}

function renderFloor() {
  loadFloor();
  const places = floorPlaces();
  const order = state?.cameras || [];
  const key = JSON.stringify(places.map((p) => [p.camera, p.id, p.name, p.state, p.count, p.note, Math.floor(p.since)]));
  if (key === hud.keys.floor) return;
  hud.keys.floor = key;
  const byCamera = new Map();
  for (const p of places) {
    if (!byCamera.has(p.camera)) byCamera.set(p.camera, []);
    byCamera.get(p.camera).push(p);
  }
  const names = [...byCamera.keys()].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const nodes = names.map((name) => {
    const list = byCamera.get(name).sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { numeric: true }));
    const block = element("section", "floor-camera");
    const head = element("h2", "floor-head");
    const site = siteOf(name);
    const busy = list.filter((p) => p.state && p.state !== "free").length;
    head.append(element("span", null, name), element("small", null, [site && site !== name ? site : "", `${busy} of ${list.length} in use`]
      .filter(Boolean).join(" · ")));
    const grid = element("div", "floor-grid");
    for (const p of list) {
      const tile = element("button", "floor-tile");
      tile.type = "button";
      tile.dataset.state = p.state || "busy";
      const count = p.count ? plural(p.count, p.kind && p.kind !== "table" ? "person" : "guest", p.kind && p.kind !== "table" ? "people" : "guests") : "";
      const line = [count, p.since && p.state !== "free" ? mmss(p.since) : ""].filter(Boolean).join(" · ");
      tile.append(element("b", null, p.name), element("span", "floor-state", PLACE_WORD[p.state] || words(p.state || "")));
      if (line) tile.append(element("small", "floor-line", line));
      if (p.note) tile.append(element("small", "floor-note", p.note));
      tile.setAttribute("aria-label", [p.name, PLACE_WORD[p.state] || p.state, line, p.note, `Show ${name}`].filter(Boolean).join(", "));
      tile.addEventListener("click", () => chooseCamera(order.includes(name) ? name : null));
      grid.append(tile);
    }
    block.append(head, grid);
    return block;
  });
  if (!nodes.length) nodes.push(element("p", "note", "No tables are marked yet."));
  $("floor").replaceChildren(...nodes);
}

setInterval(pollOverlay, HUD_POLL_MS);
setInterval(() => {
  if (screen !== "live" || !hud.ok) return;
  if (mapShown()) renderFloor();
  renderTimeline();
}, 1000);
addEventListener("resize", () => {
  if (screen === "live" && hud.ok) renderTimeline(true);
});

/* ---------- Cameras ---------- */

// Where the picture comes from. Only one place feeds the camera: security cameras through camera.py
// win while they run, and otherwise the newest page that chose a camera.
let deviceList = [];
let previewTimer = null;

function sourceState() {
  const owner = state?.owner || {};
  const count = state?.cameras?.length || 0;
  if (cameraMode) return { short: "This phone", title: "This phone is the camera", line: "Its picture goes to your Whenevers." };
  if ((owner.cameras || liveCamera) && state?.cameras?.[0] === "Phone camera") {
    return { short: "Phone camera", title: "A phone is the camera", line: "Its picture shows here and on Live. Stop it on the phone to use another camera." };
  }
  if (owner.cameras || liveCamera) {
    return { short: count > 1 ? `${count} cameras` : "Security camera", title: count > 1 ? `${count} security cameras` : "A security camera",
      line: "Every Whenever watches them. To use this laptop or a phone instead, turn them off.", off: true };
  }
  if (owner.page && owner.page !== pageId) {
    return { short: "Another device", title: "Another tab or device has the camera",
      line: "Only one place can feed the camera at a time. Your Whenevers watch that one now.", take: true };
  }
  if (source === "clip") return { short: "Demo clip", title: "Demo clip", line: "On this page: AI-made frames of a person at a desk." };
  if (source === "device") {
    const track = video.srcObject?.getVideoTracks?.()[0];
    return { short: track?.label ? shortLabel(track.label) : "This camera", title: track?.label || "This device's camera",
      line: track ? "On this page. Keep this page open, so the camera keeps watching." : "Waiting for the browser to allow the camera." };
  }
  return { short: "Pick a camera", title: "No camera yet", line: "Pick where the picture comes from." };
}

function shortLabel(label) {
  return label.replace(/\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$/i, "").slice(0, 28);
}

function renderSourcePill() {
  const text = sourceState().short;
  if ($("source-name").textContent !== text) $("source-name").textContent = text;
}

function drawPreview() {
  const canvas = $("source-preview");
  const from = liveCamera && liveCanvas.width ? liveCanvas : video;
  const width = from.videoWidth || from.width;
  const height = from.videoHeight || from.height;
  if (!width || !height || screen !== "cameras") return;
  const shown = canvas.clientWidth * devicePixelRatio;
  if (canvas.width !== Math.round(shown)) {
    canvas.width = Math.round(shown);
    canvas.height = Math.round((shown * height) / width);
  }
  canvas.getContext("2d").drawImage(from, 0, 0, canvas.width, canvas.height);
}

function renderSourceChoices() {
  const owner = state?.owner || {};
  const fed = !!(owner.cameras || liveCamera);
  const phoneFeeds = fed && state?.cameras?.[0] === "Phone camera";
  const here = owner.page === pageId || !owner.page;
  const options = [
    { kind: "device", icon: "camera", title: onPhone() ? "This phone" : "This laptop's camera",
      text: onPhone() ? "Whenever watches through this phone. Keep the page open." : "The webcam of this computer. Your browser asks once.",
      on: !fed && here && source === "device" },
    { kind: "phone", icon: "phone", title: "A spare phone", text: "Scan a QR code with any phone, and it becomes the camera. No app to install.",
      on: phoneFeeds && !cameraMode },
    { kind: "clip", icon: "play", title: "Demo clip", text: "AI-made frames of a person at a desk, to try Whenever.",
      on: !fed && here && source === "clip" },
  ];
  if (fed && !phoneFeeds) {
    options.push({ kind: "cameras", icon: "cctv", title: "Security cameras", text: "Connected. Every Whenever watches them.", on: true });
  }
  const key = JSON.stringify(options.map((o) => [o.kind, o.on]));
  if (key === keys.sources) return;
  keys.sources = key;
  $("source-choices").replaceChildren(...options.map((o) => {
    if (fed && !o.on && o.kind !== "phone") o.tag = phoneFeeds ? "Stop the phone first" : "Turn the cameras off first";
    const choice = button("", "place-choice", () => {
      if (o.kind === "cameras") return;
      if (o.kind === "phone") return openPhoneCamera();
      if (fed) return flash(phoneFeeds ? "A phone is the camera now. Stop it on the phone, then pick this."
        : "Your security cameras are on. Turn them off at the top of this screen, then pick this.");
      chooseSource(o.kind, true);
      keys.sources = "";
      setTimeout(() => renderCamerasScreen(true), 400);
    });
    choice.replaceChildren();
    choice.setAttribute("aria-pressed", String(o.on));
    const badge = element("span", "badge");
    badge.append(icon(o.icon));
    const words = element("span", "place-words");
    const head = element("b", null, o.title);
    if (o.on) head.append(element("span", "state ready", "On now"));
    else if (o.tag) head.append(element("span", "state waiting", o.tag));
    words.append(head, element("small", null, o.text));
    choice.append(badge, words, icon("arrow"));
    return choice;
  }));
}

// Makes whose cameras need something that Whenever cannot get today: a maker's developer key, a review,
// a partner approval, or a small app at home. They show as Coming soon, with the reason.
const COMING = [
  { name: "EZVIZ", make: "ezviz", why: "Needs EZVIZ's developer key" },
  { name: "Imou", why: "Needs Imou's developer key" },
  { name: "Google Nest", why: "Needs Google's review" },
  { name: "Ring", why: "Needs Ring's partner approval" },
  { name: "CP Plus", why: "Needs a video entry on our server" },
  { name: "Dahua", why: "Needs a video entry on our server" },
  { name: "Amcrest", why: "Needs a video entry on our server" },
  { name: "Hikvision", why: "Needs Hikvision's platform access" },
  { name: "TP-Link Tapo", why: "Needs the Whenever home app" },
  { name: "Reolink", why: "Needs the Whenever home app" },
  { name: "UniFi Protect", why: "Needs the Whenever home app" },
  { name: "Arlo", why: "No official way in yet" },
  { name: "Wyze", why: "No official way in yet" },
  { name: "Eufy", why: "No official way in yet" },
  { name: "Xiaomi", why: "No official way in yet" },
];

// Makes that are connected come first, as connected. EZVIZ is Hikvision's home brand, so it covers both.
function renderComing() {
  const counts = {};
  for (const camera of state?.camera_kinds || []) if (!camera.removed && camera.make) counts[camera.make] = (counts[camera.make] || 0) + 1;
  const key = JSON.stringify(counts);
  if (keys.coming === key) return;
  keys.coming = key;
  const connected = COMING.filter((make) => counts[make.make]);
  const rest = COMING.filter((make) => !counts[make.make] && !(make.name === "Hikvision" && counts.ezviz));
  $("coming-grid").replaceChildren(...[...connected, ...rest].map((make) => {
    const tile = element("li", "coming-tile" + (counts[make.make] ? " connected" : ""));
    const count = counts[make.make];
    tile.append(element("b", "wordmark", make.name === "EZVIZ" && count ? "EZVIZ (Hikvision)" : make.name),
      element("span", count ? "state ready" : "state waiting",
        count ? `Connected · ${count} ${count === 1 ? "camera" : "cameras"}` : "Coming soon"));
    return tile;
  }));
}

// A spare phone becomes the camera: it scans the code, logs in, and opens camera mode.
async function openPhoneCamera() {
  if (onPhone()) {
    location.hash = "#/phone-camera";
    return;
  }
  pairDone = null;
  if (!$("pair").open) $("pair").showModal();
  setPairHead("Use a phone as the camera", false);
  if (!state?.hosted) {
    return showPair(para("A phone cannot reach Whenever on this computer. Open Whenever online at wheneverwhatever.si, and pick A spare phone there."),
      para("On the phone itself, you can also open Whenever and pick This phone.", "note"));
  }
  showPair(waitingNode("Making a link for your phone"));
  let start;
  try {
    start = await postJson("/api/pair/start", { kind: "camera" });
  } catch (error) {
    return showPair(para(error.message, "note error"));
  }
  const grid = element("div", "pair-grid");
  const words = element("div");
  const steps = element("ol", "mini-steps");
  steps.append(element("li", null, "Point the phone's camera at the code."),
    element("li", null, "Open the link, and allow the camera."),
    element("li", null, "Lean the phone where it sees the place, and plug it in."));
  words.append(element("b", "pair-lead", "Scan this with the phone"), steps,
    para("The phone's back camera watches. It needs no app, and the page keeps the screen on.", "note"));
  grid.append(makeQr(start.url, "QR code that turns a phone into the camera"), words);
  const status = waitingNode("Waiting for the phone");
  const send = element("div", "pair-send");
  send.append(para("No camera on hand? Send the link to the phone:", "note"), linkRow(start.url));
  waitForPairing(start.token, status, openPhoneCamera, () => {
    setPairHead("The phone is the camera", false);
    const head = element("div", "pair-done");
    const mark = element("span", "done-mark");
    mark.append(icon("check"));
    head.append(mark, element("h3", null, "Your phone is the camera now"));
    const row = element("div", "pair-result");
    row.append(button("See it live", "pill-button", () => { closePairing(); location.hash = "#/live"; }, "live"),
      button("Done", "link-button", closePairing));
    showPair(head, para("Its picture shows on Live in a few seconds. Every Whenever watches through it until you press Stop on the phone."), row);
  });
  showPair(grid, status, send);
}

/* ---------- Camera mode: this phone is the camera ---------- */

let wakeLock = null;
let liveUpload = null;
let liveSending = false;

async function keepAwake() {
  try {
    wakeLock = await navigator.wakeLock?.request("screen");
  } catch {
    wakeLock = null;
  }
}

function sendLivePicture() {
  if (!cameraMode || liveSending || !video.videoWidth || document.hidden) return;
  liveSending = true;
  const canvas = document.createElement("canvas");
  canvas.width = Math.min(960, video.videoWidth);
  canvas.height = Math.round((canvas.width * video.videoHeight) / video.videoWidth);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  canvas.toBlob(async (blob) => {
    try {
      if (blob) await fetch("/api/live", { method: "POST", headers: { "Content-Type": "image/jpeg", "X-Page": pageId }, body: blob });
    } catch {
      // The next picture tries again.
    } finally {
      liveSending = false;
    }
  }, "image/jpeg", 0.72);
}

async function claimAsCamera() {
  await fetch("/api/claim", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page: pageId, cameras: ["Phone camera"] }) });
}

async function enterCameraMode() {
  if (cameraMode) return;
  cameraMode = true;
  document.body.classList.add("camera-mode");
  if (!pageId.startsWith("camera-")) pageId = `camera-${pageId}`.slice(0, 40);
  liveCamera = false;
  inactive = false;
  source = "device";
  try { localStorage.setItem("whenever-source", "device"); } catch {}
  $("camera-mode-bar").hidden = false;
  await startVideo("device");
  await claimAsCamera();
  keepAwake();
  clearInterval(liveUpload);
  liveUpload = setInterval(sendLivePicture, 500);
  $("camera-mode-note").textContent = navigator.wakeLock
    ? "Keep this page open and the phone plugged in. The screen stays on."
    : "Keep this page open and the phone plugged in. Turn off auto-lock in the phone's settings.";
}

function leaveCameraMode() {
  cameraMode = false;
  document.body.classList.remove("camera-mode");
  clearInterval(liveUpload);
  wakeLock?.release?.().catch(() => {});
  wakeLock = null;
  $("camera-mode-bar").hidden = true;
}

$("camera-stop").addEventListener("click", () => {
  if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
  video.srcObject = null;
  source = null;
  try { localStorage.removeItem("whenever-source"); } catch {}
  location.hash = "#/cameras";
});
$("camera-flip").addEventListener("click", async () => {
  let facing = "environment";
  try {
    facing = localStorage.getItem("whenever-facing") === "user" ? "environment" : "user";
    localStorage.setItem("whenever-facing", facing);
    localStorage.removeItem("whenever-device");
  } catch {}
  await startVideo("device");
});
document.addEventListener("visibilitychange", () => {
  if (cameraMode && !document.hidden) {
    keepAwake();
    claimAsCamera();
  }
});

async function renderDevicePicker() {
  const box = $("device-picker");
  const live = video.srcObject?.getVideoTracks?.()[0];
  if (source !== "device" || liveCamera || !live || !navigator.mediaDevices?.enumerateDevices) {
    box.hidden = true;
    return;
  }
  deviceList = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const current = live.getSettings().deviceId;
  const key = JSON.stringify([deviceList.map((d) => [d.deviceId, d.label]), current]);
  if (key === keys.devices) return;
  keys.devices = key;
  box.hidden = deviceList.length < 2;
  const chips = element("div", "chips");
  for (const [index, device] of deviceList.entries()) {
    const chip = button(shortLabel(device.label) || `Camera ${index + 1}`, "chip", async () => {
      try { localStorage.setItem("whenever-device", device.deviceId); } catch {}
      keys.devices = "";
      await startVideo("device");
    }, "camera");
    chip.setAttribute("aria-pressed", String(device.deviceId === current));
    chips.append(chip);
  }
  box.replaceChildren(element("b", "point-label", "Which camera of this device"), chips);
}

function renderCamerasScreen(force) {
  const now = sourceState();
  $("source-title").textContent = now.title;
  $("source-line").textContent = now.line;
  const action = $("source-action");
  const removed = state?.removed_cameras || [];
  const want = now.take ? "take" : now.off ? "off" : removed.length > 1 ? "back" : "";
  if (force || action.dataset.want !== want) {
    action.dataset.want = want;
    const buttons = [];
    if (now.take) {
      buttons.push(button("Use it here", "pill-button small", async () => {
        await takeCamera();
        if (source) startVideo(source);
        renderCamerasScreen(true);
      }, "camera"));
    } else if (now.off) {
      buttons.push(button("Turn off the security cameras", "pill-button small ghost", async () => {
        for (const name of state?.cameras || []) await postJson("/api/cameras/remove", { camera: name }).catch(() => {});
        flash("The security cameras stop in a few seconds. Then pick this laptop or a phone.");
        refreshState();
      }, "x"));
    } else if (removed.length > 1) {
      buttons.push(button("Turn the security cameras back on", "pill-button small ghost", async () => {
        for (const name of removed) await postJson("/api/cameras/restore", { camera: name }).catch(() => {});
        flash("The security cameras come back in a few seconds.");
        refreshState();
      }, "cctv"));
    }
    action.replaceChildren(...buttons);
  }
  if (force) keys.sources = "";
  renderSourceChoices();
  renderDevicePicker();
  renderComing();
  renderCameraGuide();
  clearInterval(previewTimer);
  previewTimer = setInterval(() => {
    if (screen !== "cameras") return clearInterval(previewTimer);
    drawPreview();
  }, 250);
  drawPreview();
}

// How to connect each make of camera. Passwords go only into camera.env on the computer at home.
const CAMERA_GUIDES = [
  { name: "Hikvision", also: "HiLook, Prama", steps: [
    "Find the camera's IP address in the Hik-Connect app, or on the recorder's screen.",
    "Use the camera's admin user and the password that you set when you activated it. RTSP is on already.",
    "For a recorder (NVR), add the channel number after the address. Add ?sub for the smaller stream." ],
  lines: ["hikvision://admin:PASSWORD@192.168.1.64 Front door", "hikvision://admin:PASSWORD@192.168.1.10/3 Gate"] },
  { name: "EZVIZ", also: "Hikvision's home brand", steps: [
    "With local RTSP: in the EZVIZ app, open Settings, LAN Live View, scan, pick the camera, then the gear, Local Server Settings, and turn on RTSP. Not every model has this switch.",
    "The user is admin, and the password is the verification code on the camera's label.",
    "With no local RTSP: use ezviz:SERIAL, which reads the camera through EZVIZ's cloud. It needs a one-time login, described in the README. The demo's cameras connect this way." ],
  lines: ["ezviz-local://admin:VERIFICATIONCODE@192.168.1.30 Garden", "ezviz:SERIALNUMBER Garden"] },
  { name: "CP Plus, Dahua and Amcrest", also: "Imou, Lorex", steps: [
    "Use the admin password of the camera or the recorder. A new camera asks you to set it on first use.",
    "Imou: turn on RTSP in the Imou app first, and use the safety code on the camera's label as the password.",
    "For a recorder, add the channel number after the address. Add ?sub for the smaller stream." ],
  lines: ["cpplus://admin:PASSWORD@192.168.1.108 Shop floor", "dahua://admin:PASSWORD@192.168.1.10/2 Counter"] },
  { name: "TP-Link Tapo", also: "", steps: [
    "In the Tapo app, open the camera, then Settings, Advanced Settings, Camera Account. Make a user and a password of 6 to 32 characters. This is not your Tapo login.",
    "Battery models and some others, such as the C410, C420, C425 and D230, have no RTSP. Use a bridge for those.",
    "Tapo also speaks ONVIF on port 2020." ],
  lines: ["tapo://CAMERAUSER:CAMERAPASSWORD@192.168.1.40 Hall", "onvif://CAMERAUSER:CAMERAPASSWORD@192.168.1.40:2020 Hall"] },
  { name: "Reolink", also: "", steps: [
    "In the Reolink app or client, check that RTSP is on: Settings, Network, Advanced, Port Settings. Some models have it off.",
    "Use the admin password. For cameras on a Reolink recorder, use the recorder's address, its login, and the channel number.",
    "Whenever tries both stream paths that Reolink uses, so new and old firmware both work." ],
  lines: ["reolink://admin:PASSWORD@192.168.1.20 Porch", "reolink://admin:PASSWORD@192.168.1.5/4 Back gate"] },
  { name: "UniFi Protect", also: "Ubiquiti", steps: [
    "In Protect, open the camera, then Settings, Advanced, and turn on an RTSPS stream, such as High. Copy the address that appears.",
    "Change rtsps to rtsp and the port 7441 to 7447, and delete ?enableSrtp at the end." ],
  lines: ["rtsp://192.168.1.1:7447/ABCDEF123456 Driveway"] },
  { name: "Eufy", also: "", steps: [
    "Wired indoor cameras and some SoloCam models: in the Eufy Security app, open the camera, then Settings, Storage, NAS (RTSP), and turn it on. Copy the address that the app shows.",
    "Battery EufyCam models have no RTSP. Use a bridge for those." ],
  lines: ["rtsp://USER:PASSWORD@192.168.1.50/live0 Baby room"] },
  { name: "Axis", also: "", steps: ["Use a camera user with viewer rights."], lines: ["axis://root:PASSWORD@192.168.1.90 Entrance"] },
  { name: "Any ONVIF camera", also: "Most IP cameras", steps: [
    "Turn on ONVIF in the camera's app or web page. Some makes need a separate ONVIF user.",
    "Run python3 camera.py --find. It lists each camera that answers, with a line to copy.",
    "Whenever asks the camera for its stream, so you do not need its stream path." ],
  lines: ["onvif://USER:PASSWORD@192.168.1.70 Back yard"] },
  { name: "Any RTSP camera or recorder", also: "", steps: [
    "The camera's manual or its web page names the stream path. Port 554 is the usual one." ],
  lines: ["rtsp://USER:PASSWORD@192.168.1.80:554/stream1 Store room"] },
  { name: "A picture address", also: "MJPEG, JPEG snapshots, Home Assistant, Frigate, go2rtc", steps: [
    "An address that streams MJPEG works as it is.",
    "An address that gives one JPEG at a time starts with snapshot:. Whenever reads it eight times a second." ],
  lines: ["http://192.168.1.60/video.mjpg Workshop", "snapshot:http://USER:PASSWORD@192.168.1.61/snapshot.jpg Shed"] },
  { name: "Cameras with no local stream", also: "Ring, Google Nest, Arlo, Blink, Wyze, most Xiaomi and Qubo", steps: [
    "These send video only to their own cloud. A bridge program at home turns it into a stream: go2rtc (Ring, Nest and more), docker-wyze-bridge (Wyze), Scrypted, or Home Assistant.",
    "Use the rtsp:// address that the bridge shows. Some Xiaomi models can turn on ONVIF in Mi Home, under advanced settings. Aqara's G2H and G3 have RTSP." ],
  lines: ["rtsp://192.168.1.2:8554/front_doorbell Doorbell"] },
];

function copyCode(text) {
  const row = element("div", "copy-line");
  row.append(element("code", null, text), button("Copy", "pill-button small ghost", async (event) => {
    const b = event.currentTarget;
    await navigator.clipboard.writeText(text).catch(() => {});
    b.lastChild.textContent = "Copied";
  }, "copy"));
  return row;
}

function renderCameraGuide() {
  const key = JSON.stringify([!!state?.hosted, location.host]);
  if (key === keys.guide) return;
  keys.guide = key;
  const server = state?.hosted ? `https://${location.host}` : location.origin;
  const steps = element("ol", "pair-steps guide-steps");
  const step = (title, text, ...extra) => {
    const item = element("li");
    item.append(element("b", null, title), element("p", "pair-text", text), ...extra);
    steps.append(item);
  };
  step("Run one command", "On a Mac or a Linux computer that stays on, on the same Wi-Fi as your cameras. It gets what it needs, finds your cameras, asks for each camera's password, tests it, and starts sending.",
    copyCode(`curl -fsSL ${server}/connect.sh | sh`));
  step("Answer its questions", "The access code that you use to log in, then a password for each camera. The passwords stay on that computer: only pictures leave it.");
  step("Watch them arrive", "This screen says Connected when the cameras come in. To start it again later:",
    copyCode("cd ~/whenever-cameras && .venv/bin/python camera.py"));

  const makes = element("div", "guide-makes");
  for (const guide of CAMERA_GUIDES) {
    const item = element("details", "guide-make");
    const summary = element("summary");
    const words = element("span", "place-words");
    words.append(element("b", null, guide.name), ...(guide.also ? [element("small", null, guide.also)] : []));
    summary.append(words);
    const body = element("div", "guide-body");
    const list = element("ol", "mini-steps");
    list.append(...guide.steps.map((text) => element("li", null, text)));
    body.append(list, element("b", "point-label", guide.lines.length > 1 ? "Lines for camera.env" : "Line for camera.env"),
      ...guide.lines.map(copyCode));
    item.append(summary, body);
    makes.append(item);
  }
  const more = element("details", "guide-more");
  more.append(element("summary", null, "What to switch on in each make of camera"), makes);
  $("camera-guide").replaceChildren(steps, more);
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
  // The person removed this phone in Whenever, so it stops being the camera.
  if (cameraMode && (state.removed_cameras || []).includes("Phone camera")) {
    $("camera-stop").click();
    flash("This phone was removed in Whenever, so it stopped.");
  }
  // A phone in camera mode sends the live picture itself, so it never switches to watching it.
  if (state.live_camera && !liveCamera && !cameraMode) showLiveCamera();
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
  $("demo-pill").hidden = !state.demo;
  $("demo-hello").hidden = !state.demo || chosenBefore("whenever-demo-hello");
  if (keys.wall !== JSON.stringify([state.wall, state.cameras])) {
    keys.wall = JSON.stringify([state.wall, state.cameras]);
    shapeFrame();
  }
  renderActions();
  renderLiveStrip();
  renderViewBar();
  renderCameraKinds();
  renderSourcePill();
  if (screen === "cameras") renderCamerasScreen();
  if (screen === "live") renderFocus();
  if (screen === "whenevers") renderList();
  if (screen === "whenever") renderDetail(location.hash.split("/")[2]);
  if (screen === "create") renderCreate();
  if (screen === "actions") renderPhoneCard();
  if (ASK_SCREENS.has(screen) && document.activeElement !== $("ask-input")) renderAskBar();
  handleResults(state.results);
  for (const item of state.feed) {
    lastFeed = item.id;
    if (item.kind === "outline") {
      showOutline(item);
    } else if (item.kind === "draw") {
      // After a reload, old draws stay in the list and do not freeze the view.
      if (!firstState) showDraw(item);
    } else if (item.kind === "alert" && !firstState) {
      showAlertCard(item);
    } else if (item.kind === "say" && !firstState) {
      flash(`${item.rule}: ${item.text}`);
    }
  }
  problemSignals();
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
  if (s.cameras?.[0] === "Phone camera") return "Live: phone camera";
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
  const draw = async (blob) => {
    const picture = await createImageBitmap(blob);
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
  };
  while (liveCamera) {
    try {
      // Best: the browser plays each camera from the media server, so the server decodes nothing.
      if (await playStreams()) continue;
      // One open stream sends each picture as soon as it exists. Asking for each one waits a round trip.
      if (await readLiveStream(draw)) continue;
      const response = await fetch(`/api/live.jpg?after=${liveSeq}`);
      if (response.status !== 200) continue;
      liveSeq = Number(response.headers.get("X-Seq"));
      await draw(await response.blob());
    } catch (error) {
      await wait(1000);
    }
  }
}

// The media server relays each camera as it is. This page plays them with its own hardware decoder and
// draws them into the wall, so pointing, outlines and recording work as before. HLS needs hls.js outside Safari.
let hlsLoading = null;
let streamsFailed = false;

function loadHls() {
  hlsLoading ??= new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.13/hls.min.js";
    script.onload = () => resolve(window.Hls);
    script.onerror = () => resolve(null);
    document.head.append(script);
  });
  return hlsLoading;
}

async function playStreams() {
  const names = state?.cameras || [];
  const streams = state?.streams || {};
  if (streamsFailed || !names.length || !names.every((name) => streams[name])) return false;
  // hls.js tolerates the uneven timing of some cameras better than Safari's own player, so it comes first.
  const Hls = await loadHls();
  const native = !(Hls && Hls.isSupported()) && !!document.createElement("video").canPlayType("application/vnd.apple.mpegurl");
  if (!native && !(Hls && Hls.isSupported())) {
    streamsFailed = true;
    return false;
  }
  const box = $("stream-box");
  let failed = false;
  const players = names.map((name) => {
    const player = document.createElement("video");
    player.muted = true;
    player.playsInline = true;
    player.autoplay = true;
    box.append(player);
    const url = `/hls/${streams[name]}/index.m3u8`;
    let hls = null;
    if (native) {
      player.src = url;
      player.addEventListener("error", () => { failed = true; });
    } else {
      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, backBufferLength: 10 });
      hls.on(Hls.Events.ERROR, (event, data) => { if (data.fatal) failed = true; });
      hls.loadSource(url);
      hls.attachMedia(player);
    }
    player.play().catch(() => {});
    return { name, player, hls };
  });
  // The live overlay asks each player how far behind the live edge it plays.
  for (const p of players) livePlayers.set(p.name, p);
  const [columns, rows] = state?.wall || [1, 1];
  const tileW = Math.round(1920 / columns);
  const tileH = Math.round((tileW * 9) / 16);
  const context = liveCanvas.getContext("2d");
  const key = JSON.stringify([names, streams, state?.wall]);
  let started = false;
  const until = performance.now() + 20000;
  try {
    while (liveCamera && !failed && JSON.stringify([state?.cameras, state?.streams, state?.wall]) === key) {
      if (liveCanvas.width !== tileW * columns || liveCanvas.height !== tileH * rows) {
        liveCanvas.width = tileW * columns;
        liveCanvas.height = tileH * rows;
      }
      let drawn = 0;
      players.forEach(({ name, player }, index) => {
        if (player.readyState < 2) return;
        const x = (index % columns) * tileW;
        const y = Math.floor(index / columns) * tileH;
        context.drawImage(player, x, y, tileW, tileH);
        if (names.length > 1) {
          context.font = `${Math.max(14, Math.round(tileH / 18))}px sans-serif`;
          const width = context.measureText(name).width;
          context.fillStyle = "#000";
          context.fillRect(x, y, width + 14, Math.round(tileH / 18) + 12);
          context.fillStyle = "#ffdc3c";
          context.fillText(name, x + 6, y + Math.round(tileH / 18) + 4);
        }
        drawn++;
      });
      if (drawn && !started) {
        started = true;
        video.srcObject = liveCanvas.captureStream();
        setStatus(liveLabel(state), "live");
      }
      if (!started && performance.now() > until) failed = true;
      await wait(66);
    }
  } finally {
    livePlayers.clear();
    for (const { player, hls } of players) {
      hls?.destroy();
      player.removeAttribute("src");
      player.load();
      player.remove();
    }
  }
  // A browser that cannot play these streams, such as Firefox with H.265, watches the live wall instead.
  if (failed) streamsFailed = true;
  return !failed;
}

const FRAME_HEAD = new TextEncoder().encode("\r\n\r\n");

function findBytes(haystack, needle, from = 0) {
  outer: for (let i = from; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) if (haystack[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

// Reads multipart pictures from /api/live.mjpg and draws only the newest. Returns false if streams fail here.
let liveStreamWorks = true;
async function readLiveStream(draw) {
  if (!liveStreamWorks || !window.ReadableStream) return false;
  const response = await fetch("/api/live.mjpg");
  if (!response.ok || !response.body) {
    liveStreamWorks = false;
    return false;
  }
  const reader = response.body.getReader();
  let buffer = new Uint8Array(0);
  let got = 0;
  while (liveCamera) {
    const { value, done } = await reader.read();
    if (done) break;
    const joined = new Uint8Array(buffer.length + value.length);
    joined.set(buffer);
    joined.set(value, buffer.length);
    buffer = joined;
    let newest = null;
    for (;;) {
      const head = findBytes(buffer, FRAME_HEAD);
      if (head < 0) break;
      const text = new TextDecoder().decode(buffer.subarray(0, head));
      const size = Number((text.match(/Content-Length: (\d+)/i) || [])[1]);
      if (!size || buffer.length < head + 4 + size) break;
      newest = buffer.slice(head + 4, head + 4 + size);
      const seq = Number((text.match(/X-Seq: (\d+)/i) || [])[1]);
      if (seq) liveSeq = seq;
      buffer = buffer.slice(head + 4 + size);
    }
    if (newest) {
      got++;
      await draw(new Blob([newest], { type: "image/jpeg" }));
    }
  }
  reader.cancel().catch(() => {});
  if (!got) liveStreamWorks = false;
  return got > 0;
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
    // A phone watches with its back camera unless the person picked another one under Cameras.
    let chosen = null;
    let facing = "environment";
    try {
      chosen = localStorage.getItem("whenever-device");
      facing = localStorage.getItem("whenever-facing") || facing;
    } catch {}
    const size = { width: { ideal: 1280 }, height: { ideal: 720 } };
    const stream = await navigator.mediaDevices.getUserMedia({
      video: chosen ? { ...size, deviceId: { exact: chosen } } : { ...size, facingMode: { ideal: facing } },
      audio: false,
    }).catch(() => navigator.mediaDevices.getUserMedia({ video: size, audio: false }));
    // A security camera can start while the browser asks for this one.
    if (liveCamera) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
    video.removeAttribute("src");
    video.srcObject = stream;
    // A camera that faces the person shows like a mirror. A back camera shows the world as it is.
    const faces = stream.getVideoTracks()[0]?.getSettings().facingMode;
    $("frame").classList.toggle("mirror", faces !== "environment");
    $("notice").hidden = true;
    if (screen === "cameras") renderCamerasScreen(true);
  } catch (error) {
    setStatus("No camera", "warn");
    showNotice('This page cannot use the camera. Allow camera access and reload, or <a href="#/cameras">pick another camera</a>.');
  }
}

function chooseSource(kind, stay) {
  source = kind;
  try { localStorage.setItem("whenever-source", kind); } catch {}
  // A phone that was the camera has stopped, so this page can feed the camera again.
  if (liveCamera && !state?.owner?.cameras) liveCamera = false;
  // Only one page feeds the camera. Choosing here takes it back from another tab.
  if (state?.owner?.page && state.owner.page !== pageId && !state.owner.cameras) takeCamera();
  startVideo(kind);
  if (!stay) location.hash = rules.length ? "#/whenevers" : "#/create";
}

async function takeCamera() {
  await fetch("/api/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ page: pageId }) });
  inactive = false;
  $("notice").hidden = true;
  await refreshState();
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

/* ---------- Ask: questions about the past or now ---------- */

// One box on Live, Activity and a Whenever's page. What the person looks at fills its chips:
// the chosen cameras, a pointed thing, an alert card, and a time span.
const ASK_SCREENS = new Set(["live", "activity", "whenever"]);
const ASK_SPANS = [
  { label: "Any time", minutes: null },
  { label: "Last hour", minutes: () => 60 },
  { label: "Today", minutes: () => (Date.now() - new Date().setHours(0, 0, 0, 0)) / 60000 },
  { label: "Since yesterday", minutes: () => (Date.now() - new Date().setHours(0, 0, 0, 0)) / 60000 + 24 * 60 },
];
const askContext = { thing: null, occurrence: null, cameras: null, span: 0 };
let askPollTimer = null;
let askShown = null;

function askCameras() {
  if (askContext.cameras) return askContext.cameras;
  const rule = screen === "live" ? focusId && rules.find((r) => r.id === focusId)
    : screen === "whenever" ? rules.find((r) => r.id === location.hash.split("/")[2]) : null;
  return rule ? (rule.focus_cameras?.length ? rule.focus_cameras : rule.cameras || []) : [];
}

function askChip(text, onClear, extra = "") {
  const chip = element("span", `ask-chip ${extra}`.trim());
  chip.append(element("span", null, text));
  if (onClear) {
    const x = button("", "ask-chip-x", onClear, "x");
    x.setAttribute("aria-label", `Remove ${text}`);
    chip.append(x);
  }
  return chip;
}

function renderAskBar() {
  const bar = $("ask-bar");
  const on = ASK_SCREENS.has(screen) && !cameraMode && !!state;
  bar.hidden = !on;
  document.body.classList.toggle("has-ask", on);
  if (!on) return;
  const chips = [];
  if (askContext.thing) {
    const chip = askChip(`the ${askContext.thing.noun}`, () => { askContext.thing = null; renderAskBar(); }, "thing");
    if (askContext.thing.crop) {
      const img = element("img");
      img.src = `data:image/jpeg;base64,${askContext.thing.crop}`;
      img.alt = "";
      chip.prepend(img);
    }
    chips.push(chip);
  }
  if (askContext.occurrence) {
    const occ = askContext.occurrence;
    chips.push(askChip(`${occ.rule}, ${clock(occ.t)}`, () => { askContext.occurrence = null; renderAskBar(); }, "occurrence"));
  }
  const cameras = askCameras();
  if (cameras.length) chips.push(askChip(joinList(cameras), () => { askContext.cameras = []; renderAskBar(); }));
  // A question about one alert has its time already, so the span chip would only mislead.
  if (!askContext.occurrence) {
    const span = ASK_SPANS[askContext.span];
    const time = button(span.label, "ask-chip ask-time", () => {
      askContext.span = (askContext.span + 1) % ASK_SPANS.length;
      renderAskBar();
    }, "clock");
    time.setAttribute("aria-label", `Time span: ${span.label}. Tap to change.`);
    chips.push(time);
  }
  if (screen === "live" && liveCamera) {
    chips.push(button("Point at a thing", "ask-chip ask-point", () => openPointSheet(null, "ask"), "hand"));
  }
  $("ask-chips").replaceChildren(...chips);
  $("ask-input").placeholder = askPlaceholder();
}

function askPlaceholder() {
  if (askContext.thing) return `Ask about the ${askContext.thing.noun}, such as: when did it arrive?`;
  if (askContext.occurrence) return "Ask about this, such as: who opened it?";
  const noun = rules.map((r) => r.pointed).find(Boolean);
  return noun ? `Ask anything, such as: when did the ${noun} last open?` : "Ask anything, such as: what happened in the last hour?";
}

function askSuggestions() {
  if (askContext.thing) {
    const noun = askContext.thing.noun;
    return [`When did the ${noun} arrive?`, `Was the ${noun} here last night?`, `Who came near the ${noun} today?`];
  }
  if (askContext.occurrence) return ["Who was it?", "What did they do after?", "How long did it last?"];
  const noun = rules.map((r) => r.pointed).find(Boolean);
  const ideas = ["What happened in the last hour?"];
  if (noun) ideas.unshift(`How many times did the ${noun} open today?`, `Is anyone at the ${noun} now?`);
  return ideas.slice(0, 3);
}

function showAskIdeas() {
  const box = element("div", "ask-ideas");
  for (const idea of askSuggestions()) {
    box.append(button(idea, "ask-idea", () => {
      $("ask-input").value = idea;
      $("ask-bar").requestSubmit();
    }));
  }
  const chips = $("ask-chips");
  chips.querySelector(".ask-ideas")?.remove();
  // After what the question is about, so that chip stays in view, and before the time and point chips.
  chips.insertBefore(box, chips.querySelector(".ask-time, .ask-point"));
}

$("ask-input").addEventListener("focus", () => { if (!$("ask-input").value) showAskIdeas(); });
$("ask-input").addEventListener("input", () => { if ($("ask-input").value) $("ask-chips").querySelector(".ask-ideas")?.remove(); });
$("ask-input").addEventListener("blur", () => setTimeout(() => $("ask-chips").querySelector(".ask-ideas")?.remove(), 200));

// "/" opens the box from anywhere, as in a search field.
document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || /input|textarea|select/i.test(event.target.tagName)
      || event.target.isContentEditable) return;
  event.preventDefault();
  if (!ASK_SCREENS.has(screen)) location.hash = "#/activity";
  setTimeout(() => $("ask-input").focus(), 50);
});

$("ask-bar").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (!question) {
    $("ask-input").focus();
    return showAskIdeas();
  }
  const span = ASK_SPANS[askContext.span];
  const context = {
    cameras: askCameras(),
    thing: askContext.thing && { id: askContext.thing.id, noun: askContext.thing.noun, camera: askContext.thing.camera },
    occurrence: askContext.occurrence,
    since_minutes_ago: !askContext.occurrence && span.minutes ? span.minutes() : null,
  };
  const shot = screen === "live" ? snapshot() : null;
  openAskSheet({ question, status: "working", steps: ["Reading your question"] });
  try {
    const reply = await postJson("/api/ask", { question, context, snapshot: shot ? shot.url.split(",")[1] : null });
    $("ask-input").value = "";
    pollAsk(reply.id);
  } catch (error) {
    renderAsk({ question, status: "error", error: error.message, steps: [] });
  }
});

function openAskSheet(job) {
  renderAsk(job);
  if (!$("ask-sheet").open) $("ask-sheet").showModal();
}

function pollAsk(id) {
  clearTimeout(askPollTimer);
  const tick = async () => {
    let job;
    try {
      job = await (await fetch(`/api/ask/${id}`)).json();
    } catch {
      askPollTimer = setTimeout(tick, 1500);
      return;
    }
    renderAsk(job);
    if (job.status === "working" && $("ask-sheet").open) askPollTimer = setTimeout(tick, 900);
    if (job.status === "done") loadJournal(true);
  };
  tick();
}

function renderAsk(job) {
  askShown = job;
  const body = $("ask-body");
  $("ask-title").textContent = job.status === "working" ? "Looking" : "Answer";
  const nodes = [element("p", "ask-question", job.question)];
  if (job.status === "working") {
    const steps = element("ol", "ask-steps");
    const said = job.steps?.length ? job.steps : ["Reading your question"];
    for (const [index, step] of said.entries()) {
      const li = element("li", index === said.length - 1 ? "now" : "done");
      li.append(index === said.length - 1 ? element("span", "wait-dot") : icon("check"), element("span", null, step));
      steps.append(li);
    }
    nodes.push(steps);
  } else if (job.status === "needs") {
    nodes.push(element("p", "ask-answer", `This search looks at ${job.looked || "many"} moments and costs about `
      + `${cents(job.estimate_usd)}.`));
    const row = element("div", "pair-result");
    row.append(button("Go ahead", "pill-button", async () => {
      await postJson(`/api/ask/${job.id}/go`).catch((e) => flash(e.message));
      pollAsk(job.id);
    }, "check"), button("Make it smaller", "link-button", () => {
      $("ask-sheet").close();
      askContext.span = 1;
      renderAskBar();
      $("ask-input").value = job.question;
      $("ask-input").focus();
    }));
    nodes.push(row);
  } else if (job.status === "error") {
    nodes.push(element("p", "ask-answer error", job.error || "Something went wrong."));
  } else {
    nodes.push(answerCard(job, true));
  }
  body.replaceChildren(...nodes);
}

function cents(usd) {
  const value = Number(usd || 0) * 100;
  return value < 1 ? "under 1 cent" : `${Math.round(value)} ${Math.round(value) === 1 ? "cent" : "cents"}`;
}

// An answer: one sentence, the proof in time order, and a way to keep watching for it.
function answerCard(job, inSheet = false) {
  const card = element("article", "answer-card" + (job.unclear ? " unclear" : ""));
  card.append(element("p", "ask-answer", job.answer || ""));
  const moments = job.moments || [];
  const lead = moments.find((m) => m.clip) || moments[0];
  if (lead?.clip) card.append(clipNode(lead.clip, lead.picture, inSheet, clipInfo({ ...lead, cameras: lead.cameras || (lead.camera ? [lead.camera] : []) })));
  else if (lead?.picture) {
    const img = element("img", "answer-picture");
    img.src = lead.picture;
    img.alt = lead.note || "";
    card.append(img);
  }
  if (moments.length > 1 || (moments.length === 1 && lead?.clip)) {
    const strip = element("div", "answer-moments");
    for (const moment of moments) {
      const open = element("button", "answer-moment");
      open.type = "button";
      const title = [moment.camera, dayName(moment.t) === "Today" ? clockSeconds(moment.t) : `${dayName(moment.t)} ${clock(moment.t)}`]
        .filter(Boolean).join(", ");
      open.title = moment.note || title;
      open.onclick = () => openFrame(moment.picture, moment.box || null, `${title}${moment.note ? `: ${moment.note}` : ""}`);
      const img = element("img");
      img.src = moment.picture;
      img.alt = moment.note || title;
      img.loading = "lazy";
      open.append(img, element("span", null, clock(moment.t)));
      strip.append(open);
    }
    card.append(strip);
  }
  const meta = [];
  if (job.looked) meta.push(`Looked at ${job.looked} ${job.looked === 1 ? "moment" : "moments"}`);
  if (job.span?.length === 2) meta.push(`${dayName(job.span[0]) === "Today" ? "" : `${dayName(job.span[0])} `}${clock(job.span[0])} to ${clock(job.span[1])}`);
  if (job.cost != null) meta.push(cents(job.cost));
  // Nothing found in part of the footage is not a "no": the meta says what is not seen yet instead.
  const partial = !moments.length && job.unseen > 0;
  if (partial) meta.push(`${job.unseen} not seen yet`);
  else if (job.sure != null && !job.unclear) meta.push(`${Math.round(job.sure * 100)}% sure`);
  card.append(element("p", "entry-meta", meta.join(" · ")));
  const row = element("div", "entry-buttons");
  if (partial && job.more_usd && job.id) {
    // One more search has a limit, so the button says how many it sees when that is not all of them.
    const all = !job.more_moments || job.more_moments >= job.unseen;
    const label = all ? "Look at the rest" : `Look at ${job.more_moments} more`;
    row.append(button(`${label} · about ${cents(job.more_usd)}`, "pill-button", async (event) => {
      event.currentTarget.disabled = true;
      try {
        await postJson(`/api/ask/${job.id}/go`);
      } catch (error) {
        flash(error.message);
        return;
      }
      openAskSheet({ ...job, status: "working", steps: [...(job.steps || []), "Looking at the rest"] });
      pollAsk(job.id);
    }, "search"));
  }
  if (job.unclear) {
    row.append(button("Answer it", "chip", () => {
      $("ask-sheet").close();
      $("ask-input").value = `${job.question} `;
      $("ask-input").focus();
    }, "pencil"));
  }
  if (job.watch && !job.unclear) {
    row.append(button("Watch for this from now on", "chip", () => {
      if ($("ask-sheet").open) $("ask-sheet").close();
      watchFromAnswer(job.watch);
    }, "bell"));
  }
  if (job.steps?.length) {
    const how = element("details", "occ-details");
    how.append(element("summary", null, "How Whenever found it"));
    const lines = element("ol", "occ-lines");
    for (const step of job.steps) lines.append(element("li", null, step));
    how.append(lines);
    card.append(row, how);
  } else {
    card.append(row);
  }
  return card;
}

// An answer becomes a Whenever: the words go to Create, ready to check.
function watchFromAnswer(sentence) {
  let text = String(sentence || "").trim().replace(/^(when|whenever)\s+/i, "");
  let then = "";
  const split = text.match(/^(.*?),?\s+then\s+(.*)$/i);
  if (split) [text, then] = [split[1], split[2]];
  location.hash = "#/create";
  setTimeout(() => {
    endEdit();
    $("sentence").value = text.replace(/[.,;]+$/, "");
    thenPicked.clear();
    if (/phone|alert|notif/i.test(then)) thenPicked.add("phone");
    else if (then) $("then-extra").value = then.replace(/[.]+$/, "");
    keys.then = "";
    renderThenChips();
    updatePreview();
    showStep("compose");
    $("sentence").focus();
  }, 60);
}

$("ask-close").addEventListener("click", () => $("ask-sheet").close());
$("ask-sheet").addEventListener("click", (event) => { if (event.target === $("ask-sheet")) $("ask-sheet").close(); });
$("ask-sheet").addEventListener("close", () => clearTimeout(askPollTimer));

// A question asked from a card is about that one event.
function askAboutOccurrence(card) {
  askContext.occurrence = card;
  askContext.thing = null;
  renderAskBar();
  $("ask-input").focus();
}

// An answer in Activity: the question, the answer and its first pictures. A tap opens it whole.
function answerEntry(row) {
  const li = element("li", "occurrence answer-entry");
  li.dataset.t = row.t;
  const head = element("div", "occ-head");
  const words = element("div", "occ-words");
  words.append(element("span", "ask-asked", `You asked: ${row.values?.question || ""}`), element("b", null, row.text),
    element("span", "entry-meta", [clock(row.t), row.values?.cost != null ? cents(row.values.cost) : ""].filter(Boolean).join(" · ")));
  head.append(words);
  li.append(head);
  const pictures = row.values?.pictures || [];
  if (pictures.length) {
    const strip = element("div", "answer-moments");
    for (const url of pictures) {
      const img = element("img");
      img.src = url;
      img.alt = "";
      img.loading = "lazy";
      strip.append(img);
    }
    li.append(strip);
  }
  const row_ = element("div", "entry-buttons");
  row_.append(button("Open the answer", "chip", async () => {
    const job = await (await fetch(`/api/ask/${row.values.ask}`)).json().catch(() => null);
    if (job && !job.error) openAskSheet(job);
  }, "search"));
  li.append(row_);
  return li;
}

/* ---------- Start ---------- */

paintIcons();
try { source = params.has("clip") ? "clip" : localStorage.getItem("whenever-source"); } catch { source = null; }
if (params.has("clip")) source = "clip";

// This computer's camera starts only when nothing else feeds Whenever, so a page that only watches the
// security cameras never asks for the webcam. Such a page also never claims the feed: just after a
// restart, a claim would take it from the camera reader until the reader takes it back.
refreshState().then(async () => {
  // Just after the server restarts, nobody owns the feed, and the camera reader takes it back within
  // seconds. Wait for that before this page takes the webcam.
  for (let tries = 0; source && !state?.owner?.page && tries < 8; tries++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await refreshState();
  }
  if (!source || liveCamera || state?.owner?.cameras) return;
  await fetch("/api/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page: pageId }),
  }).catch(() => {});
  startVideo(source);
});
setInterval(tick, TICK_MS);
setInterval(refreshState, POLL_MS);
setInterval(() => { if (screen === "whenevers") renderList(); }, 30000);
