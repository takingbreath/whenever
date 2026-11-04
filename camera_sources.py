"""Turns a camera's address into something ffmpeg can read, for many makes of camera.

Most cameras send RTSP, but each make puts its stream at its own path, and some need a setting
switched on first. A short address such as reolink://admin:PASSWORD@192.168.1.20 gives the known
paths of that make in order, and the reader tries the next one when a path fails. An onvif://
address asks the camera itself, so it works for any camera that speaks ONVIF. A snapshot: address
reads one JPEG at a time, for a camera or a bridge that has no stream.

Cameras with no local stream at all, such as Ring, Nest, Arlo, Blink and most Wyze or Xiaomi
models, reach Whenever through a bridge such as go2rtc, Scrypted or Home Assistant, which gives an
rtsp:// or snapshot address.
"""

import base64
import hashlib
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree
from xml.sax.saxutils import escape

RTSP_PORT = 554
ONVIF_PORT = 80
ONVIF_TIMEOUT_S = 6
SNAPSHOT = "snapshot:"
MAX_SNAPSHOT_BYTES = 8_000_000
DISCOVERY = ("239.255.255.250", 3702)


class SourceError(ValueError):
    """The camera did not answer, refused the login, or sent no usable stream."""


def _hikvision(channel, sub):
    return [f"/Streaming/Channels/{channel}0{2 if sub else 1}"]


def _dahua(channel, sub):
    return [f"/cam/realmonitor?channel={channel}&subtype={1 if sub else 0}"]


def _reolink(channel, sub):
    # Older firmware names the codec in the path, newer firmware does not.
    kind = "sub" if sub else "main"
    return [f"/h264Preview_{channel:02d}_{kind}", f"/Preview_{channel:02d}_{kind}", f"/h265Preview_{channel:02d}_{kind}"]


def _tapo(channel, sub):
    return ["/stream2" if sub else "/stream1"]


def _ezviz(channel, sub):
    kind = "sub" if sub else "main"
    return [f"/h264/ch{channel}/{kind}/av_stream", f"/Streaming/Channels/{channel}0{2 if sub else 1}"]


def _axis(channel, sub):
    return [f"/axis-media/media.amp?camera={channel}" + ("&resolution=640x360" if sub else "")]


# Makes that sell the same camera under other names share a path: CP Plus, Amcrest, Imou and Lorex
# are Dahua inside, and EZVIZ is Hikvision's home brand.
BRANDS = {
    "hikvision": _hikvision, "hik": _hikvision, "hilook": _hikvision,
    "dahua": _dahua, "cpplus": _dahua, "amcrest": _dahua, "imou": _dahua, "lorex": _dahua,
    "reolink": _reolink, "tapo": _tapo, "ezviz-local": _ezviz, "axis": _axis,
}


def candidates(address):
    """The stream addresses to try, in order. An address that is not a short brand address stays as it is.

    brand://USER:PASSWORD@HOST[:PORT][/CHANNEL][?sub]. The channel is for a recorder (NVR) with many
    cameras, and ?sub asks for the smaller second stream.
    """
    parts = urllib.parse.urlsplit(str(address))
    maker = BRANDS.get(parts.scheme.lower())
    if not maker:
        return [address]
    channel = parts.path.strip("/") or "1"
    if not channel.isdigit() or not parts.hostname:
        raise SourceError(f"{parts.scheme}:// needs a camera address and an optional channel number, "
                          f"such as {parts.scheme}://admin:PASSWORD@192.168.1.20/1")
    try:
        port = parts.port or RTSP_PORT
    except ValueError as e:
        raise SourceError("The port after the camera address must be a number.") from e
    login = parts.netloc.rpartition("@")[0]
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    sub = parts.query.lower() in ("sub", "sub=1", "stream=sub")
    return [f"rtsp://{login + '@' if login else ''}{host}:{port}{path}" for path in maker(int(channel), sub)]


# ---------- ONVIF ----------

def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _find(root, name):
    return next((e for e in root.iter() if _local_name(e.tag) == name), None)


def _soap(url, body, user="", password="", offset=timedelta(0), opener=None, timeout=ONVIF_TIMEOUT_S):
    """One ONVIF call. A login uses a WS-Security digest, so the password never crosses the network."""
    security = ""
    if user:
        created = (datetime.now(timezone.utc) + offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        nonce = os.urandom(16)
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
        security = (
            '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/'
            'oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken>'
            f"<Username>{escape(user)}</Username>"
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0'
            f'#PasswordDigest">{digest}</Password>'
            '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0'
            f'#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>'
            '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            f"{created}</Created></UsernameToken></Security>"
        )
    envelope = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                f"<s:Header>{security}</s:Header><s:Body>{body}</s:Body></s:Envelope>")
    request = urllib.request.Request(url, data=envelope.encode(),
                                     headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with (opener or urllib.request).urlopen(request, timeout=timeout) as reply:
            return ElementTree.fromstring(reply.read())
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 500):
            raise SourceError("The camera refused the ONVIF login. Check the user and the password; many "
                              "cameras need a separate camera account for ONVIF.") from None
        raise SourceError(f"The camera answered ONVIF with status {e.code}.") from None
    except (urllib.error.URLError, OSError) as e:
        raise SourceError(f"The camera did not answer ONVIF at {urllib.parse.urlsplit(url).netloc}: "
                          f"{getattr(e, 'reason', e)}") from None
    except ElementTree.ParseError:
        raise SourceError("The camera's ONVIF answer was not readable.") from None


def camera_clock(root):
    """The camera's own UTC time from GetSystemDateAndTime, or None."""
    utc = _find(root, "UTCDateTime")
    if utc is None:
        return None
    try:
        values = {_local_name(e.tag): int(e.text) for e in utc.iter() if e.text and e.text.strip().isdigit()}
        return datetime(values["Year"], values["Month"], values["Day"], values["Hour"], values["Minute"],
                        values["Second"], tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def onvif_stream(address, opener=None):
    """Asks an ONVIF camera for its main stream. Returns an rtsp:// address with the login in it.

    onvif://USER:PASSWORD@HOST[:PORT]. TP-Link Tapo uses port 2020, most others use 80.
    """
    parts = urllib.parse.urlsplit(str(address))
    if not parts.hostname:
        raise SourceError("onvif:// needs a camera address, such as onvif://admin:PASSWORD@192.168.1.20")
    user, password = urllib.parse.unquote(parts.username or ""), urllib.parse.unquote(parts.password or "")
    port = parts.port or ONVIF_PORT
    device = f"http://{parts.hostname}:{port}/onvif/device_service"
    # The login digest holds a time, so a camera with a wrong clock refuses it. Its own time fixes that.
    offset = timedelta(0)
    clock = camera_clock(_soap(device, '<GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/>',
                               opener=opener))
    if clock:
        offset = clock - datetime.now(timezone.utc)
    capabilities = _soap(device, '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl">'
                                 "<Category>Media</Category></GetCapabilities>", user, password, offset, opener)
    media = _find(capabilities, "Media")
    xaddr = _find(media, "XAddr") if media is not None else None
    media_url = xaddr.text.strip() if xaddr is not None and xaddr.text else device
    profiles = _soap(media_url, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, password, offset, opener)
    tokens = [e.get("token") for e in profiles.iter() if _local_name(e.tag) == "Profiles" and e.get("token")]
    if not tokens:
        raise SourceError("The camera has no ONVIF video profile.")
    reply = _soap(media_url, '<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl"><StreamSetup>'
                             '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
                             '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
                             f"</StreamSetup><ProfileToken>{escape(tokens[0])}</ProfileToken></GetStreamUri>",
                  user, password, offset, opener)
    uri = _find(reply, "Uri")
    if uri is None or not (uri.text or "").strip().lower().startswith("rtsp"):
        raise SourceError("The camera gave no RTSP stream over ONVIF.")
    return with_login(uri.text.strip(), parts.netloc.rpartition("@")[0], parts.hostname)


def with_login(stream, login, camera_host):
    """The stream address with the camera's login in it. A camera that names no host means itself."""
    parts = urllib.parse.urlsplit(stream)
    host = parts.hostname or ""
    if host in ("", "0.0.0.0", "127.0.0.1", "localhost"):
        host = camera_host
    netloc = (f"{login}@" if login else "") + (f"[{host}]" if ":" in host else host) + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


# ---------- Finding cameras ----------

PROBE = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
         'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
         'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
         'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
         "<e:Header><w:MessageID>uuid:{id}</w:MessageID>"
         '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
         '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
         "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>")


def read_probe_match(data):
    """One camera from a WS-Discovery answer: {host, port, name, hardware}, or None."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    xaddrs = _find(root, "XAddrs")
    addresses = [a for a in (xaddrs.text or "").split() if a.startswith("http")] if xaddrs is not None else []
    if not addresses:
        return None
    first = urllib.parse.urlsplit(addresses[0])
    scopes = _find(root, "Scopes")
    found = {"host": first.hostname, "port": first.port or 80, "name": "", "hardware": ""}
    for scope in (scopes.text or "").split() if scopes is not None else []:
        for key in ("name", "hardware"):
            prefix = f"onvif://www.onvif.org/{key}/"
            if scope.startswith(prefix):
                found[key] = urllib.parse.unquote(scope[len(prefix):]).replace("_", " ").strip()
    return found


def discover(seconds=3.0):
    """Finds ONVIF cameras on this network. Most IP cameras answer, including Hikvision, Dahua, CP Plus,
    Reolink, Tapo and Axis, if ONVIF is switched on."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        probe = PROBE.format(id=uuid.uuid4()).encode()
        for _ in range(2):
            sock.sendto(probe, DISCOVERY)
        deadline = datetime.now() + timedelta(seconds=seconds)
        while datetime.now() < deadline:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            camera = read_probe_match(data)
            if camera:
                found.setdefault(camera["host"], camera)
    finally:
        sock.close()
    return sorted(found.values(), key=lambda c: tuple(int(p) if p.isdigit() else 0 for p in str(c["host"]).split(".")))


# ---------- Snapshots ----------

def snapshot_opener(address):
    """An opener for a snapshot: address. A login in the address works for basic and digest logins."""
    url = str(address)[len(SNAPSHOT):]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceError("A snapshot address looks like snapshot:http://CAMERA/path/to/picture.jpg")
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, parts.query, ""))
    handlers = []
    if parts.username:
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, clean, urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password or ""))
        handlers = [urllib.request.HTTPDigestAuthHandler(manager), urllib.request.HTTPBasicAuthHandler(manager)]
    return clean, urllib.request.build_opener(*handlers)


def fetch_snapshot(clean, opener, timeout=10):
    try:
        with opener.open(clean, timeout=timeout) as reply:
            data = reply.read(MAX_SNAPSHOT_BYTES)
    except urllib.error.HTTPError as e:
        raise SourceError(f"The camera answered {e.code}" + (": check the user and the password." if e.code in (401, 403) else ".")) from None
    except (urllib.error.URLError, OSError) as e:
        raise SourceError(f"The camera did not answer: {getattr(e, 'reason', e)}") from None
    if not data.startswith(b"\xff\xd8"):
        raise SourceError("That address did not give a JPEG picture.")
    return data


def hide_login(text):
    """An address can hold a camera's password, so it never reaches a log or the page."""
    return re.sub(r"://[^@/\s]*@", "://***@", str(text))
