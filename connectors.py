"""The person's actions: an alert on their phone, or any web request that they approved.

Rule code has no network. It names an action, and this file sends the request. A request goes only
to the host that the person approved, and it follows no redirect. A key stays in actions.env, so it
never reaches rule code, Wonder or the page.
"""

import base64
import ipaddress
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTIONS_FILE = ROOT / "data" / "actions.json"
SECRETS_FILE = ROOT / "actions.env"
NTFY_SERVER = "https://ntfy.sh"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_TYPES = ("none", "json", "text")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
INPUT = re.compile(r"[a-z][a-z0-9_]{0,29}")
SECRET = re.compile(r"[A-Z][A-Z0-9_]{1,39}")
BLANK = re.compile(r"\{(secret:)?([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_ACTIONS = 20
MAX_INPUTS = 8
MAX_VALUE_CHARS = 2000
MAX_PICTURE_BYTES = 5_000_000
MAX_REPLY_CHARS = 500
TIMEOUT_S = 10
# A hosted server must not reach its own network for a visitor, so it sends only to public hosts.
PUBLIC = os.environ.get("WHENEVER_PUBLIC") == "1"

PHONE = {
    "name": "Phone", "kind": "phone", "alerts": True,
    "description": "Sends an alert to the person's phone, with a picture if you pass one.",
    "inputs": [{"name": "text", "description": "The alert, in one or two short sentences", "example": "A car blocks the gate"},
               {"name": "title", "description": "A short title, or leave it out", "example": "Gate"}],
    "secrets": [{"name": "NTFY_TOPIC", "description": "The ntfy topic that the phone app follows"}],
    "method": "POST", "url": f"{NTFY_SERVER}/{{secret:NTFY_TOPIC}}",
}


class ActionError(ValueError):
    """An action does not exist, is not ready, or did not work."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def read_secrets(path):
    """NAME=value lines. The file is read at each call, so a new key needs no restart."""
    found = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and SECRET.fullmatch(name.strip()):
            found[name.strip()] = value.strip()
    return found


def header_text(text):
    """A header can carry only ASCII, so other text goes in the form that ntfy decodes."""
    text = " ".join(str(text).split())
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


def public_host(host):
    """True if every address of the host is on the public internet."""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)


def blanks(text):
    return [(bool(secret), name) for secret, name in BLANK.findall(str(text))]


def fill_text(template, inputs, secrets, quote=False):
    def value(match):
        if match.group(1):
            return secrets[match.group(2)]
        text = str(inputs.get(match.group(2), ""))
        return urllib.parse.quote(text, safe="") if quote else text
    return BLANK.sub(value, template)


def fill_json(template, inputs, secrets):
    """A blank that is the whole string keeps the input's own type, so {"on": "{on}"} can be false."""
    if isinstance(template, dict):
        return {key: fill_json(value, inputs, secrets) for key, value in template.items()}
    if isinstance(template, list):
        return [fill_json(value, inputs, secrets) for value in template]
    if isinstance(template, str):
        whole = BLANK.fullmatch(template)
        if whole and not whole.group(1) and whole.group(2) in inputs:
            return inputs[whole.group(2)]
        return fill_text(template, inputs, secrets)
    return template


def check_draft(draft, taken=()):
    """Returns a clean action, or raises ActionError. Wonder wrote it, so nothing is trusted."""
    name = str(draft.get("name") or "").strip()
    if not NAME.fullmatch(name):
        raise ActionError("An action needs a short name of letters and numbers.")
    if name.lower() in {item.lower() for item in taken} or name.lower() == "phone":
        raise ActionError(f"An action named {name} already exists.")
    method = str(draft.get("method") or "").upper()
    if method not in METHODS:
        raise ActionError(f"The method must be one of {', '.join(METHODS)}.")
    url = str(draft.get("url") or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "{" in parts.netloc:
        raise ActionError("The address must start with http:// or https:// and have a fixed host.")
    if PUBLIC and parts.scheme != "https":
        raise ActionError("A hosted server sends only to https addresses.")
    inputs = [{"name": str(item.get("name") or ""), "description": str(item.get("description") or "")[:200],
               "example": str(item.get("example") or "")[:200]} for item in draft.get("inputs") or []][:MAX_INPUTS]
    secrets = [{"name": str(item.get("name") or ""), "description": str(item.get("description") or "")[:200]}
               for item in draft.get("secrets") or []][:MAX_INPUTS]
    for item in inputs:
        if not INPUT.fullmatch(item["name"]):
            raise ActionError(f"The input name {item['name']!r} must be short, in small letters.")
    for item in secrets:
        if not SECRET.fullmatch(item["name"]):
            raise ActionError(f"The key name {item['name']!r} must be in capital letters.")
    headers = {str(k).strip(): str(v) for k, v in (draft.get("headers") or {}).items() if str(k).strip()}
    body_type = str(draft.get("body_type") or "none")
    body = str(draft.get("body") or "")
    if body_type not in BODY_TYPES:
        raise ActionError("The body type must be none, json or text.")
    if body_type == "json":
        try:
            json.loads(body)
        except ValueError as e:
            raise ActionError("The JSON body is not valid JSON.") from e
    known = {(False, item["name"]) for item in inputs} | {(True, item["name"]) for item in secrets}
    texts = [url, body, *headers.keys(), *headers.values()]
    for secret, blank in {b for text in texts for b in blanks(text)}:
        if (secret, blank) not in known:
            raise ActionError(f"The request uses {{{'secret:' if secret else ''}{blank}}}, which is not listed.")
    if any(blanks(key) for key in headers):
        raise ActionError("A header name must be fixed.")
    return {"name": name, "kind": "web", "description": str(draft.get("description") or "")[:300],
            "method": method, "url": url, "headers": headers, "body_type": body_type, "body": body,
            "inputs": inputs, "secrets": secrets, "alerts": False}


class Connectors:
    def __init__(self, path=None, secrets_path=SECRETS_FILE, opener=None):
        """With no path, actions live only in memory, as in tests."""
        self.path = Path(path) if path else None
        self.secrets_path = secrets_path
        self.opener = opener or OPENER
        self.lock = threading.Lock()
        self.items = []
        if self.path and self.path.exists():
            try:
                self.items = [check_draft(item) for item in json.loads(self.path.read_text())][:MAX_ACTIONS]
            except (OSError, ValueError):
                self.items = []

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=1, ensure_ascii=False))

    def all(self):
        return [PHONE, *self.items]

    def get(self, name):
        found = next((item for item in self.all() if item["name"].lower() == str(name).lower()), None)
        if not found:
            names = ", ".join(item["name"] for item in self.ready()) or "none"
            raise ActionError(f"There is no action named {name}. These are ready: {names}.")
        return found

    def missing(self, action, secrets=None):
        secrets = read_secrets(self.secrets_path) if secrets is None else secrets
        return [item["name"] for item in action["secrets"] if not secrets.get(item["name"])]

    def ready(self):
        secrets = read_secrets(self.secrets_path)
        return [item for item in self.all() if not self.missing(item, secrets)]

    def public(self):
        """What the page shows. Key names only, never their values."""
        secrets = read_secrets(self.secrets_path)
        return [{"name": item["name"], "kind": item["kind"], "description": item["description"],
                 "method": item["method"], "host": urllib.parse.urlsplit(item["url"]).hostname,
                 "inputs": item["inputs"], "secrets": [s["name"] for s in item["secrets"]],
                 "missing": self.missing(item, secrets), "alerts": item["alerts"]} for item in self.all()]

    def describe(self):
        """Tells Wonder which actions a rule can use."""
        lines = []
        for item in self.ready():
            inputs = ", ".join(f"{i['name']} ({i['description']})" for i in item["inputs"]) or "none"
            picture = " It can attach a picture." if item["kind"] == "phone" else ""
            lines.append(f"- {item['name']}: {item['description']} Inputs: {inputs}.{picture}")
        return "\n".join(lines)

    def add(self, draft):
        with self.lock:
            action = check_draft(draft, [item["name"] for item in self.items])
            if len(self.items) >= MAX_ACTIONS:
                raise ActionError(f"You can keep {MAX_ACTIONS} actions.")
            self.items.append(action)
            self._save()
        return action

    def remove(self, name):
        with self.lock:
            before = len(self.items)
            self.items = [item for item in self.items if item["name"].lower() != str(name).lower()]
            self._save()
        return len(self.items) < before

    def run(self, name, inputs=None, picture=None, click=None):
        return self.send(self.get(name), inputs, picture, click)

    def alert(self, text, picture=None, click=None):
        """ctx.notify reaches every ready action that takes alerts, such as the phone."""
        done = []
        for action in self.ready():
            if action["alerts"]:
                done.append((action["name"], self.send(action, {"text": text}, picture, click)))
        return done

    def send(self, action, inputs=None, picture=None, click=None):
        """Sends one request. click is a page that a phone alert opens when the person taps it."""
        secrets = read_secrets(self.secrets_path)
        missing = self.missing(action, secrets)
        if missing:
            raise ActionError(f"{action['name']} needs {', '.join(missing)} in actions.env.")
        inputs = {str(k): v for k, v in (inputs or {}).items()}
        for key, value in inputs.items():
            if not isinstance(value, (str, int, float, bool)) or len(str(value)) > MAX_VALUE_CHARS:
                raise ActionError(f"The input {key} must be a short text, number or true or false.")
        if picture is not None and len(picture) > MAX_PICTURE_BYTES:
            raise ActionError("The picture is too large to send.")
        if action["kind"] == "phone":
            request = self._phone(action, inputs, secrets, picture, click)
        else:
            request = self._web(action, inputs, secrets)
        host = urllib.parse.urlsplit(request.full_url).hostname
        if PUBLIC and not public_host(host):
            raise ActionError(f"{host} is not on the public internet.")
        try:
            with self.opener.open(request, timeout=TIMEOUT_S) as reply:
                status, text = reply.status, reply.read(MAX_REPLY_CHARS * 4)
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read(MAX_REPLY_CHARS * 4) if e.fp else b""
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ActionError(f"{action['name']} did not answer.") from e
        text = text.decode("utf-8", "replace")
        for value in secrets.values():
            if len(value) >= 4:
                text = text.replace(value, "***")
        return {"ok": 200 <= status < 300, "status": status, "reply": text[:MAX_REPLY_CHARS]}

    def _phone(self, action, inputs, secrets, picture, click=None):
        url = fill_text(action["url"], {}, secrets)
        text = str(inputs.get("text") or "Whenever").strip()[:MAX_VALUE_CHARS]
        headers = {"Title": header_text(inputs.get("title") or "Whenever")}
        if click and str(click).startswith("https://"):
            headers["Click"] = str(click)
        if picture is None:
            return urllib.request.Request(url, data=text.encode(), headers=headers, method="POST")
        headers.update({"Message": header_text(text), "Filename": "picture.jpg"})
        return urllib.request.Request(url, data=picture, headers=headers, method="PUT")

    def _web(self, action, inputs, secrets):
        for item in action["inputs"]:
            if item["name"] not in inputs:
                raise ActionError(f"{action['name']} needs the input {item['name']}.")
        parts = urllib.parse.urlsplit(action["url"])
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, fill_text(parts.path, inputs, secrets, True),
                                       fill_text(parts.query, inputs, secrets, True), ""))
        headers = {}
        for key, value in action["headers"].items():
            value = fill_text(value, inputs, secrets)
            if "\r" in value or "\n" in value:
                raise ActionError("A header cannot hold a new line.")
            headers[key] = value
        data = None
        if action["body_type"] == "json":
            data = json.dumps(fill_json(json.loads(action["body"]), inputs, secrets)).encode()
            headers.setdefault("Content-Type", "application/json")
        elif action["body_type"] == "text":
            data = fill_text(action["body"], inputs, secrets).encode()
        return urllib.request.Request(url, data=data, headers=headers, method=action["method"])
