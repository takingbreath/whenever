// The page that a phone opens from the QR code or the link. It turns on alerts for this phone,
// or it confirms an email address. It needs no login: the one-time token in the address is enough.

const token = location.pathname.split("/")[2] || "";
const view = document.getElementById("view");
const ua = navigator.userAgent;
const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const canPush = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
let info = null;

const GLYPHS = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  share: '<path d="M12 3v12M8 7l4-4 4 4"/><path d="M6 11H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1"/>',
  plus: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8v8M8 12h8"/>',
  bell: '<path d="M10 5a2 2 0 1 1 4 0 7 7 0 0 1 4 6v3a4 4 0 0 0 2 3H4a4 4 0 0 0 2-3v-3a7 7 0 0 1 4-6"/><path d="M9 17v1a3 3 0 0 0 6 0v-1"/>',
};

function glyph(name, className = "glyph") {
  return `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GLYPHS[name]}</svg>`;
}

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function deviceLabel() {
  const os = /Android/.test(ua) ? "Android" : isIOS ? (/iPad/.test(ua) ? "iPad" : "iPhone")
    : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "this device";
  const browser = /Edg\//.test(ua) ? "Edge" : /SamsungBrowser/.test(ua) ? "Samsung Internet"
    : /Firefox|FxiOS/.test(ua) ? "Firefox" : /CriOS|Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "A browser";
  return isIOS && standalone ? `Whenever on ${os}` : `${browser} on ${os}`;
}

function keyBytes(text) {
  const raw = atob((text + "=".repeat((4 - (text.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function render(html) {
  view.innerHTML = html;
  const action = view.querySelector("[data-act]");
  if (action) action.addEventListener("click", ACTS[action.dataset.act]);
}

const SCREENS = {
  expired: () => `<div class="mark warn">${glyph("alert", "")}</div>
    <h1>This link is old</h1>
    <p class="lead">A pairing link works once, for 15 minutes.</p>
    <p>Make a new one in Whenever: open Actions, then Pair your phone.</p>`,
  used: () => `<div class="mark ok">${glyph("check", "")}</div>
    <h1>This phone is already paired</h1>
    <p class="lead">The link worked before, so there is nothing to do.</p>
    <a class="button" href="/">Open Whenever</a>`,
  push: () => `<h1>Get Whenever alerts on this phone</h1>
    <p class="lead">Each alert says what happened, with the picture that proves it. You need no app.</p>
    <button class="button" type="button" data-act="turnOn">${glyph("bell")}Turn on alerts</button>
    <p class="small">Your phone asks once. Tap Allow.</p>`,
  install: () => `<h1>Add Whenever to your Home Screen first</h1>
    <p class="lead">An iPhone shows web alerts only for an app on its Home Screen. It takes three taps.</p>
    <ol class="steps">
      <li><b>Tap Share ${glyph("share")}</b><span>It is in Safari's bar, at the bottom or the top.</span></li>
      <li><b>Tap Add to Home Screen ${glyph("plus")}</b><span>Scroll the list if you do not see it. Then tap Add.</span></li>
      <li><b>Open Whenever from your Home Screen</b><span>This page opens there, with a button to turn on alerts.</span></li>
    </ol>
    <p class="small" style="margin-top:18px">This needs iOS 16.4 or later. The link works for 15 minutes.</p>`,
  noPush: () => `<div class="mark warn">${glyph("alert", "")}</div>
    <h1>This browser cannot show alerts</h1>
    <p class="lead">Open this page in Chrome on Android, or in Safari on an iPhone.</p>
    <button class="button ghost" type="button" data-act="copy">Copy the link</button>
    <p class="small">Some apps open links inside their own browser. That browser cannot show alerts.</p>`,
  denied: () => `<div class="mark warn">${glyph("alert", "")}</div>
    <h1>Alerts are blocked for this site</h1>
    <p class="lead">${isIOS ? "Open Settings, then Notifications, then Whenever, and allow notifications."
      : "Tap the icon to the left of the address, open Permissions or Site settings, and allow Notifications."}</p>
    <button class="button" type="button" data-act="turnOn">Try again</button>`,
  working: () => `<h1>Turning on alerts</h1>
    <p class="lead">Tap Allow if your phone asks.</p>
    <button class="button" type="button" disabled><span class="spin" aria-hidden="true"></span>Working</button>`,
  done: () => `<div class="mark ok">${glyph("check", "")}</div>
    <h1>Alerts are on</h1>
    <p class="lead">A test alert is on its way to this phone. Your computer shows that it is paired.</p>
    <a class="button" href="/">Open Whenever</a>
    <p class="small">To stop alerts, remove this phone in Whenever, under Actions.</p>`,
  email: () => `<h1>Send Whenever alerts to this address?</h1>
    <p class="lead"><code>${esc(info.address)}</code></p>
    <p>Each alert says what happened, with the picture that proves it.</p>
    <button class="button" type="button" data-act="confirmEmail">${glyph("check")}Yes, send alerts here</button>
    <p class="small">If you did not ask for this, close the page. Nothing more is sent.</p>`,
  emailDone: () => `<div class="mark ok">${glyph("check", "")}</div>
    <h1>Email alerts are on</h1>
    <p class="lead">Whenever sends its alerts to <code>${esc(info.address)}</code> now. You can close this page.</p>`,
};

function fail(message) {
  const note = document.createElement("p");
  note.className = "error";
  note.textContent = message;
  view.querySelector(".error")?.remove();
  view.append(note);
}

const ACTS = {
  async turnOn() {
    // The permission prompt must come straight from the tap, or an iPhone refuses it.
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return render(SCREENS.denied());
    render(SCREENS.working());
    try {
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const old = await registration.pushManager.getSubscription();
      if (old) await old.unsubscribe();
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: keyBytes(info.push_key),
      });
      const reply = await fetch(`/api/pair/${token}/push`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: subscription.toJSON(), label: deviceLabel() }),
      });
      const data = await reply.json().catch(() => ({}));
      if (!reply.ok) throw new Error(data.error || "Whenever did not answer.");
      render(SCREENS.done());
    } catch (error) {
      render(SCREENS.push());
      fail(error.message || "This phone could not turn on alerts. Try again.");
    }
  },
  async confirmEmail(event) {
    event.currentTarget.disabled = true;
    const reply = await fetch(`/api/pair/${token}/email`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      event.currentTarget.disabled = false;
      return fail(data.error || "Whenever did not answer.");
    }
    render(SCREENS.emailDone());
  },
  async copy(event) {
    await navigator.clipboard?.writeText(location.href).catch(() => {});
    event.currentTarget.textContent = "Copied";
  },
};

async function start() {
  try {
    info = await (await fetch(`/api/pair/${token}/info`)).json();
  } catch {
    return render(`<h1>Whenever did not answer</h1><p class="lead">Check the connection, then open the link again.</p>`);
  }
  if (!info.valid) {
    // The Home Screen app opens this address again later. Then it goes straight to Whenever.
    if (info.done && info.logged_in) return location.replace("/");
    return render(info.done ? SCREENS.used() : SCREENS.expired());
  }
  if (info.kind === "email") return render(SCREENS.email());
  if (isIOS && !standalone) return render(SCREENS.install());
  if (!canPush || !info.push_key) return render(SCREENS.noPush());
  if (Notification.permission === "denied") return render(SCREENS.denied());
  render(SCREENS.push());
}

start();
