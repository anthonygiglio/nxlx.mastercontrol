# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Saved network streams (SRT, RTSP, RTMP) that mpv can play like a clip.

Only these URL schemes are accepted. mpv understands many more (`file://`, `edl://`, `lavf://`,
`ytdl://`, ...), some of which read local files or run helper programs; a stream URL is never
passed on unless its scheme is in the list below. A URL may carry a user name and password
(`rtsp://user:pass@camera/stream`); it is stored in settings (readable only by the service
account) and hidden everywhere it is shown, including the player status. So are the other secrets an
address can hold: an RTMP stream key and its query, an SRT passphrase and streamid (see _pieces).
"""

import re
import uuid
from urllib.parse import unquote, urlsplit

SCHEMES = ("srt", "rtsp", "rtsps", "rtmp", "rtmps")
MAX_STREAMS = 24
MAX_URL = 500
_ID = re.compile(r"^[0-9a-f]{8}$")
# SRT options that say how to connect and nothing about who: the only query names of an srt:// address that are
# shown and exported. Everything else in an SRT query is treated as a secret.
SRT_PLAIN = ("mode", "latency", "rcvlatency", "peerlatency", "transtype", "connect_timeout", "listen_timeout", "timeout",
             "rw_timeout", "pkt_size", "payload_size", "mss", "maxbw", "inputbw", "oheadbw", "ffs", "ipttl", "iptos",
             "tlpktdrop", "tsbpd", "nakreport", "lossmaxttl", "rcvbuf", "sndbuf", "recv_buffer_size", "send_buffer_size",
             "linger", "minversion", "messageapi", "smoother", "enforced_encryption", "pbkeylen", "kmrefreshrate",
             "kmpreannounce", "snddropdelay")
_SECRET_NAME = re.compile(r"pass|pwd|psk|secret|token|key|auth|sig|cred|login|user")      # rtsp: names that sound like a secret
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$|^[0-9A-Fa-f:.]{2,45}$")


class StreamError(Exception):
    pass


def valid_url(url):
    """The URL if it is an acceptable stream address, else raises StreamError."""
    if not isinstance(url, str) or not 1 <= len(url) <= MAX_URL:
        raise StreamError("enter the stream address")
    if re.search(r"[\x00-\x20\x7f-\x9f\\]", url):
        raise StreamError("the address may not contain spaces or control characters")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise StreamError("that is not a valid address")
    if parts.scheme.lower() not in SCHEMES:
        raise StreamError("the address must start with %s" % ", ".join(s + "://" for s in SCHEMES))
    if not host or not _HOST.fullmatch(host):
        raise StreamError("the address needs a host name or IP address")
    if port is not None and not 1 <= port <= 65535:
        raise StreamError("bad port number")
    return url


def clean_name(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or re.search(r"[\x00-\x1f\x7f]", name):
        raise StreamError("give the stream a name of up to 40 characters")
    return name.strip()


def new_entry(name, url):
    return {"id": uuid.uuid4().hex[:8], "name": clean_name(name), "url": valid_url(url)}


def _stream_parts(url):
    """(scheme, login or "", host and port, path, [query pairs as written] or None) by cutting the text, not by
    parsing and rebuilding it: an SRT streamid may hold "#" and "," and must come out byte for byte as it went in."""
    if not isinstance(url, str) or "://" not in url:
        return None
    scheme, rest = url.split("://", 1)
    cut = re.search(r"[/?#]", rest)
    authority, tail = (rest[:cut.start()], rest[cut.start():]) if cut else (rest, "")
    login, _, host = authority.rpartition("@")
    path, mark, query = tail.partition("?")
    return scheme, login, host, path, (query.split("&") if mark else None)


def _secret_pair(scheme, pair):
    """Is this query pair a secret? The name is compared without its percent-encoding and in lower case."""
    if not pair:
        return False
    name = unquote(pair.split("=", 1)[0]).strip().lower()
    if scheme in ("rtmp", "rtmps"):
        return True
    if scheme == "srt":
        return name not in SRT_PLAIN
    return bool(_SECRET_NAME.search(name))


def _pieces(url):
    """(scheme, login, host, path to keep, secret end of the path, [(query pair, is it secret)] or None), or None
    for what is not an address. The one place that says which parts of a stream address are secret:

    * every scheme: the user name and password before the host;
    * rtmp, rtmps: the last part of the path (the stream key) and the whole query (auth, sign and the like);
    * srt: every query pair whose name is not in SRT_PLAIN, so the passphrase and the streamid (it can carry a
      user name) and any name a later version of the protocol adds;
    * rtsp, rtsps: query pairs whose name sounds like a secret (_SECRET_NAME); the path stays.
    """
    parts = _stream_parts(url)
    if parts is None:
        return None
    scheme, login, host, path, query = parts
    kind, key = scheme.lower(), ""
    if kind in ("rtmp", "rtmps"):
        head, _, last = path.rstrip("/").rpartition("/")
        if last:
            path, key = head, last
    return scheme, login, host, path, key, (None if query is None else [(p, _secret_pair(kind, p)) for p in query])


def strip_login(url):
    """A stream address without its secrets (see _pieces). Everything else stays exactly as it was written."""
    pieces = _pieces(url)
    if pieces is None:
        return ""
    scheme, _login, host, path, _key, query = pieces
    out = scheme + "://" + host + path
    if query is None:
        return out
    kept = [p for p, secret in query if not secret]
    if len(kept) == len(query):                      # nothing secret in it: the query stays as written, even an empty one
        return out + "?" + "&".join(kept)
    return out + ("?" + "&".join(kept) if kept else "")


def redact(url):
    """The address as it may be shown: every secret in it (see _pieces) replaced by ***."""
    pieces = _pieces(url)
    if pieces is None:
        return url
    scheme, login, host, path, key, query = pieces
    out = scheme + "://" + ("***@" if login else "") + host + path + ("/***" if key else "")
    if query is None:
        return out
    return out + "?" + "&".join((p.split("=", 1)[0] + "=***" if "=" in p else "***") if secret else p for p, secret in query)


def stream_secrets(url):
    """The secret pieces of a stream address (see _pieces), as written and without percent-encoding, to scrub from
    any text."""
    pieces = _pieces(url)
    if pieces is None:
        return []
    out = [x for x in pieces[1].split(":", 1) if x]
    if pieces[4]:
        out.append(pieces[4])
    out += [p.split("=", 1)[1] for p, secret in pieces[5] or [] if secret and "=" in p]
    return out + [unquote(x) for x in out if unquote(x) != x]


def stream_where(url):
    """Only where a stream comes from (scheme, host, port), for diagnostics: a path can be a stream key."""
    try:
        parts = urlsplit(url)
        return "%s://%s%s" % (parts.scheme, parts.hostname or "?", ":%d" % parts.port if parts.port else "")
    except (ValueError, AttributeError):
        return "?"


def validate_saved(items):
    """Check the stored list (settings are user-editable files)."""
    if not isinstance(items, list) or len(items) > MAX_STREAMS:
        raise StreamError("streams must be a list of at most %d" % MAX_STREAMS)
    seen = set()
    for it in items:
        if not isinstance(it, dict) or not _ID.fullmatch(str(it.get("id", ""))) or it["id"] in seen:
            raise StreamError("bad stream entry")
        seen.add(it["id"])
        clean_name(it.get("name"))
        valid_url(it.get("url"))
    return items
