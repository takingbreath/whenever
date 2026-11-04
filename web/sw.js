// Shows Whenever's alerts when the page is closed. A tap opens the page that the alert names.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const options = {
    body: data.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/badge.png",
    tag: data.tag || "whenever",
    renotify: true,
    timestamp: data.t ? data.t * 1000 : Date.now(),
    data: { url: data.url || "/" },
  };
  if (data.image) options.image = data.image;
  event.waitUntil(self.registration.showNotification(data.title || "Whenever", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin);
  event.waitUntil((async () => {
    const pages = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = pages.find((page) => new URL(page.url).origin === url.origin);
    if (open) {
      await open.focus();
      open.postMessage({ open: url.hash || "#/activity" });
      return;
    }
    // A link to another site, such as an older alert's, opens as it is.
    await self.clients.openWindow(url.origin === self.location.origin ? url.pathname + url.hash : url.href);
  })());
});
