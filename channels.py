"""Where the person's alerts go: a browser, Telegram, email, or the ntfy app.

The Phone action sends each alert to every place that the person paired. Keys stay in actions.env
and the places in data/channels.json, so rule code, Wonder and the page never read them.

A browser gets Web Push (RFC 8291 and RFC 8292). Telegram gets a bot that the person made with
BotFather. Email goes through Amazon SES. The ntfy app follows a private topic.
"""

import base64
import configparser
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from connectors import NTFY_SERVER, OPENER, SECRETS_FILE, header_text, read_secrets, write_secret

ROOT = Path(__file__).resolve().parent
CHANNELS_FILE = ROOT / "data" / "channels.json"
PICTURES_DIR = ROOT / "data" / "alert-pictures"
KINDS = ("push", "telegram", "email", "ntfy")
PAIR_S = 15 * 60
MAX_PUSH = 10
MAX_EMAILS = 5
MAX_PICTURES = 300
MAX_TEXT = 900
TIMEOUT_S = 10
POLL_S = 25
PUSH_TTL_S = 6 * 3600
# A subscription names its push service. Only these get a request, so a fake one cannot aim it.
PUSH_HOSTS = (".googleapis.com", ".push.apple.com", ".mozilla.com", ".mozaws.net", ".notify.windows.com")
TELEGRAM_API = "https://api.telegram.org"
BOT_TOKEN = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{30,60}")
# A token inside other text, such as BotFather's whole answer. A pasted line break can vanish, so a
# second pattern finds a token that touches the next word: BotFather's tokens have 35 characters, from AA.
TOKEN_ALONE = re.compile(r"(?<![0-9])(\d{5,16}:[A-Za-z0-9_-]{30,60})(?![A-Za-z0-9_-])")
TOKEN_GLUED = re.compile(r"(?<![0-9])(\d{5,16}:AA[A-Za-z0-9_-]{33})")
EMAIL = re.compile(r"[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
SES_REGION = "ap-south-1"
CONTACT = "https://wheneverwhatever.si"


class ChannelError(ValueError):
    """A place for alerts is not set up, or it did not take the alert."""


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------- Web Push ----------

def _ec():
    """cryptography is needed only for browser alerts, so the rest works without it."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as e:
        raise ChannelError("Browser alerts need the cryptography package: pip install cryptography.") from e
    return hashes, serialization, ec, utils, AESGCM, HKDF


def public_bytes(key):
    _, serialization, _, _, _, _ = _ec()
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt_push(payload, p256dh, auth, salt=None, server_key=None):
    """aes128gcm content coding of RFC 8291. Only the browser that subscribed can read it."""
    hashes, _, ec, _, AESGCM, HKDF = _ec()
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = public_bytes(server_key)
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    salt = salt or os.urandom(16)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    # One record. 0x02 marks the last record.
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + sealed


def vapid_token(endpoint, private_key, contact=CONTACT, now=None):
    """A signed JWT for the push service of this endpoint (RFC 8292)."""
    hashes, _, ec, utils, _, _ = _ec()
    parts = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": contact}
    signing = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64u(json.dumps(claims).encode())
    r, s = utils.decode_dss_signature(private_key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    return signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def push_host_ok(endpoint):
    parts = urllib.parse.urlsplit(str(endpoint))
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(end) or host == end[1:] for end in PUSH_HOSTS)


# ---------- Amazon SES ----------

def aws_keys(found):
    """Keys from actions.env, or from a named profile in ~/.aws (a Mac). A server keeps them in actions.env."""
    region = found.get("AWS_SES_REGION") or SES_REGION
    if found.get("AWS_SES_ACCESS_KEY_ID") and found.get("AWS_SES_SECRET_ACCESS_KEY"):
        return found["AWS_SES_ACCESS_KEY_ID"], found["AWS_SES_SECRET_ACCESS_KEY"], None, region
    profile = found.get("AWS_SES_PROFILE")
    if not profile:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(Path.home() / ".aws" / "credentials")
    except (OSError, configparser.Error):
        return None
    if not parser.has_section(profile):
        return None
    section = parser[profile]
    if not section.get("aws_access_key_id") or not section.get("aws_secret_access_key"):
        return None
    return section["aws_access_key_id"], section["aws_secret_access_key"], section.get("aws_session_token"), region


def sign_v4(method, url, body, key_id, secret, region, service="ses", token=None, now=None):
    """AWS Signature Version 4 headers for one request."""
    parts = urllib.parse.urlsplit(url)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    headers = {"content-type": "application/json", "host": parts.netloc, "x-amz-date": stamp}
    if token:
        headers["x-amz-security-token"] = token
    names = ";".join(sorted(headers))
    canonical = "\n".join([method, parts.path or "/", parts.query,
                           "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), names,
                           hashlib.sha256(body).hexdigest()])
    scope = f"{stamp[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret).encode()
    for part in (stamp[:8], region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
                                f"SignedHeaders={names}, Signature={signature}")
    return headers


def mail_html(title, lines, picture_cid=None, button=None, footer=""):
    """A plain, light message. Mail apps strip most styles, so each style is inline."""
    esc = html.escape
    paragraphs = "".join(f'<p style="margin:0 0 12px;font-size:16px;line-height:1.5;color:#2a2b30">{esc(line)}</p>'
                         for line in lines if line)
    picture = (f'<img src="cid:{picture_cid}" alt="The picture that proves it" width="520" '
               f'style="display:block;width:100%;max-width:520px;height:auto;border-radius:14px;margin:4px 0 16px">'
               if picture_cid else "")
    action = (f'<a href="{esc(button[1])}" style="display:inline-block;padding:12px 22px;border-radius:999px;'
              f'background:#3346d3;color:#ffffff;font-weight:700;font-size:15px;text-decoration:none">{esc(button[0])}</a>'
              if button else "")
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f3f3f1">
<div style="max-width:560px;margin:0 auto;padding:28px 20px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<p style="margin:0 0 20px;font-weight:800;font-size:15px;color:#17181c"><span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:#3346d3;vertical-align:-2px;margin-right:8px"></span>Whenever</p>
<div style="background:#ffffff;border-radius:18px;padding:24px 22px">
<h1 style="margin:0 0 12px;font-size:22px;line-height:1.25;color:#17181c">{esc(title)}</h1>
{paragraphs}{picture}{action}
</div>
<p style="margin:18px 4px 0;font-size:12px;line-height:1.5;color:#6b6d75">{esc(footer)}</p>
</div></body></html>"""


def mime(sender, to, subject, text, html_body, picture=None, picture_cid=None, clip=None):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    if picture and picture_cid:
        message.get_payload()[1].add_related(picture, "image", "jpeg", cid=f"<{picture_cid}>", filename="picture.jpg")
    if clip:
        message.add_attachment(clip, maintype="video", subtype="mp4", filename="clip.mp4")
    return message.as_bytes()


# ---------- Telegram ----------

def find_bot_token(text):
    """The token in what the person pasted: BotFather's whole answer, a part of it, or the token alone."""
    text = str(text or "")
    alone = TOKEN_ALONE.findall(text)
    exact = [t for t in alone if len(t.split(":")[1]) == 35]
    found = list(dict.fromkeys(exact or TOKEN_GLUED.findall(text) or alone))
    if not found:
        raise ChannelError("Whenever found no bot token there. Copy BotFather's whole answer, "
                           "or the line under \"Use this token\". It looks like 123456789:AAH...")
    if len(found) > 1:
        raise ChannelError("That text has more than one token. Paste the answer about your new bot only.")
    return found[0]


def multipart(fields, files):
    boundary = secrets.token_hex(16)
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, (filename, data, content_type) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f'Content-Type: {content_type}\r\n\r\n').encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def device_label(text):
    text = " ".join(str(text or "").split())[:60]
    return text or "A browser"


class Channels:
    def __init__(self, path=None, secrets_path=SECRETS_FILE, opener=None, pictures_dir=None, clock=time.time):
        """With no path, the places live only in memory, as in tests."""
        self.path = Path(path) if path else None
        self.secrets_path = secrets_path
        self.opener = opener or OPENER
        self.pictures_dir = Path(pictures_dir) if pictures_dir else None
        self.clock = clock
        self.lock = threading.Lock()
        self.pairings = {}
        self.bot = None
        self.bot_for = None
        self.update_offset = 0
        self.places = {"push": [], "telegram": None, "email": [], "ntfy": None}
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.places.update({k: saved.get(k) for k in KINDS if k in saved})
            except (OSError, ValueError, AttributeError):
                pass

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.places, indent=1, ensure_ascii=False))
            os.chmod(self.path, 0o600)

    def secrets(self):
        return read_secrets(self.secrets_path)

    # ----- What the page shows -----

    def all(self):
        """Every paired place, with no key, endpoint or chat id."""
        p = self.places
        items = [{"kind": "push", "id": s["id"], "label": s["label"], "t": s["t"]} for s in p["push"]]
        if p["telegram"]:
            items.append({"kind": "telegram", "id": "telegram", "label": f"Telegram: {p['telegram']['name']}",
                          "t": p["telegram"]["t"]})
        items += [{"kind": "email", "id": e["id"], "label": e["address"], "t": e["t"]} for e in p["email"]]
        if p["ntfy"]:
            items.append({"kind": "ntfy", "id": "ntfy", "label": "ntfy app", "t": p["ntfy"]["t"]})
        return items

    def paired(self):
        return bool(self.all())

    def public(self):
        found = self.secrets()
        bot = self.bot if self.bot_for == found.get("TELEGRAM_BOT_TOKEN") else None
        return {
            "places": self.all(),
            "push_key": self.push_public_key(create=False),
            "telegram_bot": bot,
            "telegram_key": bool(found.get("TELEGRAM_BOT_TOKEN")),
            "email_ready": bool(aws_keys(found) and found.get("WHENEVER_MAIL_FROM")),
        }

    # ----- Pairing -----

    def start(self, kind, address=None, login=False):
        """A one-time token. The phone, the bot or the email link proves it within PAIR_S."""
        if kind not in ("push", "telegram", "email", "camera"):
            raise ChannelError("Pick a browser, Telegram or email.")
        now = self.clock()
        token = secrets.token_urlsafe(18)
        with self.lock:
            self.pairings = {k: v for k, v in self.pairings.items() if now - v["t"] < PAIR_S}
            self.pairings[token] = {"kind": kind, "t": now, "done": None, "address": address, "login": login}
        return token

    def pairing(self, token):
        """The page asks this until the phone, the bot or the email link finishes it."""
        item = self.pairings.get(str(token))
        if not item:
            return None
        expired = self.clock() - item["t"] >= PAIR_S
        place = next((p for p in self.all() if p["id"] == item["done"]), None) if item["done"] else None
        return {"kind": item["kind"], "done": bool(item["done"]), "expired": expired and not item["done"],
                "place": place, "login": item["login"], "address": item.get("address")}

    def _claim(self, token, kind):
        item = self.pairings.get(str(token))
        if not item or item["kind"] != kind or item["done"] or self.clock() - item["t"] >= PAIR_S:
            raise ChannelError("This link is old or used. Make a new one in Whenever.")
        return item

    def claim_camera(self, token):
        """A phone opened the camera link. It becomes the camera, and the computer sees it finish."""
        item = self._claim(token, "camera")
        with self.lock:
            item["done"] = "camera"
        return item

    def remove(self, place_id):
        with self.lock:
            p = self.places
            before = len(self.all())
            p["push"] = [s for s in p["push"] if s["id"] != place_id]
            p["email"] = [e for e in p["email"] if e["id"] != place_id]
            if place_id == "telegram":
                p["telegram"] = None
            if place_id == "ntfy":
                p["ntfy"] = None
            self._save()
            return len(self.all()) < before

    # ----- Browser -----

    def _vapid_key(self):
        _, _, ec, _, _, _ = _ec()
        found = self.secrets()
        raw = found.get("WEBPUSH_PRIVATE_KEY")
        if raw:
            return ec.derive_private_key(int.from_bytes(unb64u(raw), "big"), ec.SECP256R1())
        key = ec.generate_private_key(ec.SECP256R1())
        write_secret(self.secrets_path, "WEBPUSH_PRIVATE_KEY", b64u(key.private_numbers().private_value.to_bytes(32, "big")))
        return key

    def push_public_key(self, create=True):
        """The key that a browser needs to subscribe. The first pairing makes the pair of keys."""
        if not create and not self.secrets().get("WEBPUSH_PRIVATE_KEY"):
            return ""
        try:
            return b64u(public_bytes(self._vapid_key()))
        except ChannelError:
            return ""

    def add_push(self, subscription, label, token=None):
        """A browser subscribed. With a token, it came from the pairing page on a phone."""
        if token:
            item = self._claim(token, "push")
        endpoint = str((subscription or {}).get("endpoint") or "")
        keys = (subscription or {}).get("keys") or {}
        if not push_host_ok(endpoint):
            raise ChannelError("This browser's push service is not one that Whenever knows.")
        try:
            if len(unb64u(keys.get("p256dh", ""))) != 65 or len(unb64u(keys.get("auth", ""))) != 16:
                raise ValueError
        except ValueError as e:
            raise ChannelError("The browser sent a broken subscription.") from e
        place = {"id": "p" + secrets.token_hex(4), "endpoint": endpoint, "p256dh": keys["p256dh"],
                 "auth": keys["auth"], "label": device_label(label), "t": self.clock()}
        with self.lock:
            # The same browser again replaces its old subscription.
            self.places["push"] = [s for s in self.places["push"] if s["endpoint"] != endpoint][-(MAX_PUSH - 1):]
            self.places["push"].append(place)
            if token:
                item["done"] = place["id"]
            self._save()
        return place

    def _send_push(self, place, title, text, picture_url=None, click=None, tag=None):
        payload = {"title": title[:120], "body": text[:MAX_TEXT], "url": click or "/", "tag": tag or "whenever",
                   "t": int(self.clock())}
        if picture_url:
            payload["image"] = picture_url
        body = encrypt_push(json.dumps(payload, ensure_ascii=False).encode(), place["p256dh"], place["auth"])
        key = self._vapid_key()
        headers = {"TTL": str(PUSH_TTL_S), "Urgency": "high", "Content-Encoding": "aes128gcm",
                   "Content-Type": "application/octet-stream",
                   "Authorization": f"vapid t={vapid_token(place['endpoint'], key)}, k={b64u(public_bytes(key))}"}
        status, reply = self._open(urllib.request.Request(place["endpoint"], data=body, headers=headers, method="POST"))
        if status in (404, 410):
            self.remove(place["id"])
            raise ChannelError(f"{place['label']} no longer takes alerts, so Whenever removed it. Pair it again.")
        if not 200 <= status < 300:
            raise ChannelError(f"The push service answered {status}: {reply[:160]}")

    # ----- Telegram -----

    def telegram_token(self):
        return self.secrets().get("TELEGRAM_BOT_TOKEN", "")

    def _telegram(self, method, fields=None, files=None, timeout=TIMEOUT_S, token=None):
        token = token or self.telegram_token()
        if not token:
            raise ChannelError("Telegram needs a bot first.")
        url = f"{TELEGRAM_API}/bot{token}/{method}"
        if files:
            body, content_type = multipart(fields or {}, files)
        else:
            body, content_type = json.dumps(fields or {}).encode(), "application/json"
        status, reply = self._open(urllib.request.Request(url, data=body, headers={"Content-Type": content_type},
                                                          method="POST"), timeout=timeout, hide=token)
        try:
            data = json.loads(reply)
        except ValueError:
            data = {}
        if status != 200 or not data.get("ok"):
            raise ChannelError(f"Telegram answered {status}: {str(data.get('description') or reply)[:160]}",)
        return data.get("result")

    def set_telegram_token(self, pasted):
        """The person pasted BotFather's answer, or only its token. Telegram must accept it before it is kept."""
        token = find_bot_token(pasted)
        try:
            me = self._telegram("getMe", token=token)
        except ChannelError as e:
            raise ChannelError("Telegram did not accept this token. Copy it again from BotFather.") from e
        write_secret(self.secrets_path, "TELEGRAM_BOT_TOKEN", token)
        with self.lock:
            self.bot, self.bot_for = {"username": me.get("username", ""), "name": me.get("first_name", "")}, token
            self.update_offset = 0
            # A new bot cannot reach the chat of the old one.
            if self.places["telegram"] and self.places["telegram"].get("bot") != me.get("username"):
                self.places["telegram"] = None
                self._save()
        return self.bot

    def telegram_bot(self):
        token = self.telegram_token()
        if not token:
            return None
        if self.bot_for != token:
            me = self._telegram("getMe")
            with self.lock:
                self.bot, self.bot_for = {"username": me.get("username", ""), "name": me.get("first_name", "")}, token
        return self.bot

    def poll_telegram(self, timeout=POLL_S):
        """One long poll. /start with a pairing token connects that chat. /stop ends alerts there."""
        updates = self._telegram("getUpdates", {"offset": self.update_offset, "timeout": timeout,
                                                "allowed_updates": ["message"]}, timeout=timeout + 10)
        for update in updates or []:
            self.update_offset = max(self.update_offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            text = str(message.get("text") or "").strip()
            if not chat.get("id") or chat.get("type") != "private":
                continue
            self._telegram_message(chat, message.get("from") or {}, text)
        return len(updates or [])

    def _telegram_message(self, chat, sender, text):
        word, _, arg = text.partition(" ")
        word = word.split("@")[0].lower()
        current = self.places["telegram"]
        if word == "/start" and arg:
            try:
                item = self._claim(arg.strip(), "telegram")
            except ChannelError as e:
                return self._telegram("sendMessage", {"chat_id": chat["id"], "text": str(e)})
            name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or "you"
            with self.lock:
                self.places["telegram"] = {"chat_id": chat["id"], "name": name[:40], "t": self.clock(),
                                           "bot": (self.bot or {}).get("username", "")}
                item["done"] = "telegram"
                self._save()
            return self._telegram("sendMessage", {"chat_id": chat["id"], "text":
                                  "Paired. Whenever alerts arrive in this chat now, with the picture that proves "
                                  "each one. Send /stop to end them."})
        if current and current["chat_id"] == chat["id"] and word == "/stop":
            self.remove("telegram")
            return self._telegram("sendMessage", {"chat_id": chat["id"], "text":
                                  "Alerts stopped. To start them again, pair Telegram in Whenever."})
        if current and current["chat_id"] == chat["id"]:
            return self._telegram("sendMessage", {"chat_id": chat["id"], "text":
                                  "Whenever sends alerts here. Send /stop to end them."})
        return self._telegram("sendMessage", {"chat_id": chat["id"], "text":
                              "To get alerts here, open Whenever, pick Telegram, and use the link it shows."})

    def _send_telegram(self, place, title, text, picture=None, click=None, clip=None, reply_to=None):
        """Returns the message's id, so a later picture of the same event can answer it."""
        caption = f"<b>{html.escape(title)}</b>\n{html.escape(text)}"[:1000]
        fields = {"chat_id": place["chat_id"], "parse_mode": "HTML"}
        if reply_to:
            fields["reply_parameters"] = json.dumps({"message_id": int(reply_to), "allow_sending_without_reply": True})
        if click and str(click).startswith("https://"):
            fields["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": "Open in Whenever", "url": click}]]})
        try:
            if clip:
                # A clip plays in the chat, so the person sees what moved.
                sent = self._telegram("sendVideo", {**fields, "caption": caption, "supports_streaming": "true"},
                                      {"video": ("clip.mp4", clip, "video/mp4")}, timeout=60)
            elif picture:
                sent = self._telegram("sendPhoto", {**fields, "caption": caption},
                                      {"photo": ("picture.jpg", picture, "image/jpeg")})
            else:
                sent = self._telegram("sendMessage", {**{k: json.loads(v) if k in ("reply_markup", "reply_parameters")
                                                         else v for k, v in fields.items()}, "text": caption})
        except ChannelError as e:
            if "blocked" in str(e) or "chat not found" in str(e):
                self.remove("telegram")
                raise ChannelError("The Telegram chat no longer takes alerts, so Whenever removed it.") from e
            raise
        return (sent or {}).get("message_id")

    def add_clip(self, sent, clip, title, text, click=None, picture=None):
        """The clip of an alert that already went out with its picture. Telegram swaps the picture for the
        clip in the same message, so one event stays one message. Email waited for the clip, so it goes now
        with both. Returns one result for each place that got something."""
        results = []
        for place in self.all():
            try:
                if place["kind"] == "telegram" and clip and sent.get(place["id"]):
                    caption = f"<b>{html.escape(title)}</b>\n{html.escape(text)}"[:1000]
                    media = {"type": "video", "media": "attach://clip", "caption": caption, "parse_mode": "HTML",
                             "supports_streaming": True}
                    fields = {"chat_id": self.places["telegram"]["chat_id"], "message_id": str(sent[place["id"]]),
                              "media": json.dumps(media)}
                    if click and str(click).startswith("https://"):
                        fields["reply_markup"] = json.dumps(
                            {"inline_keyboard": [[{"text": "Open in Whenever", "url": click}]]})
                    self._telegram("editMessageMedia", fields, {"clip": ("clip.mp4", clip, "video/mp4")}, timeout=60)
                elif place["kind"] == "email" and place["id"] not in sent:
                    stored = next(e for e in self.places["email"] if e["id"] == place["id"])
                    self._send_email(stored, title, text, picture, click, clip)
                else:
                    continue
                results.append({**place, "ok": True, "detail": ""})
            except (ChannelError, StopIteration, OSError) as e:
                results.append({**place, "ok": False, "detail": str(e)[:300]})
        return results

    # ----- Email -----

    def _mail(self, to, subject, text, html_body, picture=None, picture_cid=None, clip=None):
        found = self.secrets()
        keys = aws_keys(found)
        sender = found.get("WHENEVER_MAIL_FROM")
        if not keys or not sender:
            raise ChannelError("Email needs Amazon SES keys and WHENEVER_MAIL_FROM in actions.env.")
        key_id, secret, token, region = keys
        raw = mime(sender, to, subject, text, html_body, picture, picture_cid, clip)
        body = json.dumps({"FromEmailAddress": sender, "Destination": {"ToAddresses": [to]},
                           "Content": {"Raw": {"Data": base64.b64encode(raw).decode()}}}).encode()
        url = f"https://email.{region}.amazonaws.com/v2/email/outbound-emails"
        headers = sign_v4("POST", url, body, key_id, secret, region, token=token)
        status, reply = self._open(urllib.request.Request(url, data=body, headers=headers, method="POST"), hide=secret)
        if status != 200:
            raise ChannelError(f"Amazon SES answered {status}: {reply[:200]}")

    def start_email(self, address, confirm_url):
        """Sends one message with a button. The address is paired only when someone taps it."""
        address = str(address or "").strip()
        if not EMAIL.fullmatch(address):
            raise ChannelError("Type a full email address, like you@example.com.")
        if len(self.places["email"]) >= MAX_EMAILS:
            raise ChannelError(f"You can pair {MAX_EMAILS} addresses. Remove one first.")
        token = self.start("email", address=address)
        url = confirm_url(token)
        lines = ["Someone asked Whenever to send its alerts to this address. If it was you, tap the button.",
                 "Each alert says what happened, with the picture that proves it."]
        self._mail(address, "Send Whenever alerts here?", "\n\n".join(lines + [url]),
                   mail_html("Send Whenever alerts here?", lines, button=("Yes, send alerts here", url),
                             footer="If it was not you, do nothing. Whenever sends nothing more until someone taps."))
        return token

    def confirm_email(self, token):
        item = self._claim(token, "email")
        place = {"id": "e" + secrets.token_hex(4), "address": item["address"], "t": self.clock()}
        with self.lock:
            self.places["email"] = [e for e in self.places["email"] if e["address"].lower() != item["address"].lower()]
            self.places["email"].append(place)
            item["done"] = place["id"]
            self._save()
        return place

    def _send_email(self, place, title, text, picture=None, click=None, clip=None):
        cid = make_msgid(domain="whenever")[1:-1] if picture else None
        button = ("Open in Whenever", click) if click and str(click).startswith("https://") else None
        footer = "This address is paired in Whenever. To stop these messages, remove it under Actions, Where alerts go."
        lines = [text, "The clip of what moved is attached." if clip else ""]
        self._mail(place["address"], title, text + (f"\n\n{click}" if button else ""),
                   mail_html(title, lines, cid, button, footer), picture, cid, clip)

    # ----- ntfy -----

    def add_ntfy(self):
        with self.lock:
            self.places["ntfy"] = {"t": self.clock()}
            self._save()

    def _send_ntfy(self, place, title, text, picture=None, click=None):
        topic = self.secrets().get("NTFY_TOPIC")
        if not topic:
            raise ChannelError("The ntfy topic is missing from actions.env.")
        headers = {"Title": header_text(title)}
        if click and str(click).startswith("https://"):
            headers["Click"] = str(click)
        if picture is None:
            request = urllib.request.Request(f"{NTFY_SERVER}/{topic}", data=text.encode(), headers=headers, method="POST")
        else:
            headers.update({"Message": header_text(text), "Filename": "picture.jpg"})
            request = urllib.request.Request(f"{NTFY_SERVER}/{topic}", data=picture, headers=headers, method="PUT")
        status, reply = self._open(request, hide=topic)
        if not 200 <= status < 300:
            raise ChannelError(f"ntfy answered {status}: {reply[:160]}")

    # ----- Sending -----

    def keep_picture(self, picture):
        """A browser fetches an alert's picture by address, so it gets a long random name and no login."""
        if not picture or not self.pictures_dir:
            return None
        self.pictures_dir.mkdir(parents=True, exist_ok=True)
        name = secrets.token_urlsafe(16) + ".jpg"
        (self.pictures_dir / name).write_bytes(picture)
        old = sorted(self.pictures_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for file in old[:-MAX_PICTURES]:
            file.unlink(missing_ok=True)
        return name

    def picture(self, name):
        if not self.pictures_dir or not re.fullmatch(r"[A-Za-z0-9_-]{20,30}\.jpg", str(name)):
            return None
        file = self.pictures_dir / name
        return file.read_bytes() if file.is_file() else None

    def alert(self, text, title="Whenever", picture=None, click=None, base_url=None, only=None, clip=None,
              reply=None, tag=None, skip=()):
        """Sends to every paired place, or only to one. Returns one result for each place, with ref: the
        message that a later picture of the same event can answer.

        A place that fails does not stop the others, so one broken bot cannot hide an alert. reply maps a
        place to the message to answer. tag groups a browser's notifications of one event. skip lists kinds
        of place to leave out for now, such as email, which waits for the clip.
        """
        text = " ".join(str(text or "Whenever").split())[:MAX_TEXT]
        title = " ".join(str(title or "Whenever").split())[:120]
        places = [p for p in self.all() if only is None or p["id"] == only]
        if only is not None and not places:
            raise ChannelError("That place is not paired.")
        places = [p for p in places if p["kind"] not in skip]
        reply = reply or {}
        picture_url = None
        if picture and base_url and any(p["kind"] == "push" for p in places):
            name = self.keep_picture(picture)
            picture_url = f"{base_url}/alert-picture/{name}" if name else None
        results = []
        for place in places:
            try:
                ref = None
                if place["kind"] == "push":
                    stored = next(s for s in self.places["push"] if s["id"] == place["id"])
                    self._send_push(stored, title, text, picture_url, click, tag)
                elif place["kind"] == "telegram":
                    ref = self._send_telegram(self.places["telegram"], title, text, picture, click, clip,
                                              reply.get(place["id"]))
                elif place["kind"] == "email":
                    stored = next(e for e in self.places["email"] if e["id"] == place["id"])
                    self._send_email(stored, title, text, picture, click, clip)
                else:
                    self._send_ntfy(place, title, text, picture, click)
                results.append({**place, "ok": True, "detail": "", "ref": ref})
            except (ChannelError, StopIteration, OSError) as e:
                results.append({**place, "ok": False, "detail": str(e)[:300]})
        return results

    def _open(self, request, timeout=TIMEOUT_S, hide=None):
        """Returns (status, text). A key in a reply or an error never reaches the page."""
        host = urllib.parse.urlsplit(request.full_url).hostname
        try:
            with self.opener.open(request, timeout=timeout) as reply:
                status, raw = reply.status, reply.read(20_000)
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(20_000) if e.fp else b""
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = str(getattr(e, "reason", e))
            if hide:
                reason = reason.replace(hide, "***")
            raise ChannelError(f"{host} did not answer: {reason[:120]}") from None
        text = raw.decode("utf-8", "replace")
        return status, text.replace(hide, "***") if hide else text
