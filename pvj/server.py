# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""HTTP front end for the control API and the web panel (standard library only).

Security model, in one place:
* every API call except hello, pair, session and logout needs a paired device token
  (HttpOnly SameSite=Strict cookie, or Authorization: Bearer for scripts); logout
  removes that token from the box and answers with the cookie expired
* every state-changing call is a POST with JSON, the X-PVJ-Request header and a
  matching Origin (if the browser sends one), so other websites cannot drive it
* the panel is served with a Content-Security-Policy that forbids inline script,
  external resources and framing
* the same panel and API answer on a second, TLS listener when PVJ_HTTPS_PORT is set (D79, pvj/httpsbox.py): there
  the cookie is __Host-pvj_token with Secure; a switch keeps owner access off plain HTTP (the gate below)
"""

import json
import os
import re
import signal
import socket
import sys
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import icon, paths
from . import autostart as autostart_mod, dmx as dmx_mod, hardware, midi as midi_mod, pinscreen as pinscreen_mod, sysd as sysd_mod, netd as netd_mod, osc as osc_mod, scheduler as scheduler_mod, themes as themes_mod
from .api import Api, ApiError
from .auth import Auth
from .modules import Registry
from .player import Player
from .settings import Settings, SettingsError

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
MAX_BODY = 64 * 1024
COOKIE = "pvj_token"
SECURE_COOKIE = "__Host-pvj_token"      # over TLS only (D79): the prefix forces Secure, Path=/ and no Domain; a second name,
                                        # because browsers leave a Secure cookie alone when plain http tries to replace it
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2"}
HTML_TAG = b'<html lang="en">'          # in index.html; the style of the chosen theme is written into it (styled_html)


def static_files(web_dir):
    """What the panel may be asked for, as {request path: file under web_dir}: the files at the top of the folder and
    the .woff2 files in fonts/, nothing else. The map is fixed when the server starts and a request is looked up in
    it whole, so no request path is ever joined to a folder. A symbolic link is never in it (a file or the fonts
    folder itself): the panel serves what was installed there, not what something there points at."""

    def plain(path):
        return os.path.isfile(path) and not os.path.islink(path)
    out = {"/": "index.html"}
    if os.path.isdir(web_dir):
        for name in sorted(os.listdir(web_dir)):
            if plain(os.path.join(web_dir, name)):
                out["/" + name] = name
        fonts = os.path.join(web_dir, "fonts")
        if os.path.isdir(fonts) and not os.path.islink(fonts):
            for name in sorted(os.listdir(fonts)):
                if name.endswith(".woff2") and plain(os.path.join(fonts, name)):
                    out["/fonts/" + name] = os.path.join("fonts", name)
    return out


def styled_html(body, style):
    """index.html with the style of the chosen theme on its root element, so the page is drawn in that look from the
    first paint (a script could only add it later, and the strict policy allows no inline one). Only a name from
    themes.STYLES is ever written; the default look leaves the page as the file has it."""
    if style == "default" or style not in themes_mod.STYLES:
        return body
    return body.replace(HTML_TAG, HTML_TAG[:-1] + b' data-style="' + style.encode("ascii") + b'">', 1)


def host_allowed(host, names):
    """Is the Host header one of ours? IP addresses (any), localhost, and the names in `names`. A web page on the
    internet that rebinds its own name to the box's address (DNS rebinding) sends its own name here and is refused,
    so it cannot drive the panel from a visitor's browser."""
    import ipaddress
    if host is None:
        return True           # no Host header: an old HTTP/1.0 client or a script, never a browser (browsers always send it)
    host = host.strip().lower()
    if not host or len(host) > 255:
        return False
    if host.startswith("["):                                  # [v6] or [v6]:port
        end = host.find("]")
        if end < 0 or (host[end + 1:] and not re.fullmatch(r":[0-9]{1,5}", host[end + 1:])):
            return False
        name = host[1:end]
    else:
        name, _, port = host.partition(":")
        if port and not port.isdigit():
            return False
    try:
        ipaddress.ip_address(name.split("%")[0])
        return True
    except ValueError:
        pass
    return name.rstrip(".") in names


def allowed_names(env=None):
    env = os.environ if env is None else env
    hostname = socket.gethostname().lower()
    names = {"localhost", hostname, hostname.split(".")[0] + ".local"}
    names.update(n.strip().lower() for n in env.get("PVJ_ALLOWED_HOSTS", "").split(",") if n.strip())
    return names


def make_handler(api, auth, web_dir=WEB_DIR, max_lifetime=60.0, host_names=None):
    host_names = allowed_names() if host_names is None else host_names
    static = static_files(web_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "pvj"
        sys_version = ""
        timeout = 10  # per read; the lifetime timer below bounds the whole connection

        def setup(self):
            super().setup()
            # A client that trickles one byte every few seconds would keep a thread for ever.
            self._reaper = threading.Timer(max_lifetime, self._kill)
            self._reaper.daemon = True
            self._reaper.start()

        def _kill(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self):
            self._reaper.cancel()
            super().finish()

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

        # --- plumbing ---------------------------------------------------
        def _send(self, status, body, content_type, extra=None, cache="no-store"):
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("Cache-Control", cache)
                for k, v in (extra or []):
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except OSError:
                # The client went away (a phone that lost its connection): nothing to tell, nothing to log.
                self.close_connection = True

        def _json(self, status, payload, extra=None):
            self._send(status, json.dumps(payload).encode(), "application/json", extra)

        def _secure(self):
            return bool(getattr(self.server, "secure", False))

        def _token(self):
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return header[7:].strip()
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            if self._secure() and SECURE_COOKIE in jar:     # the secure name is read over TLS only
                return jar[SECURE_COOKIE].value
            return jar[COOKIE].value if COOKIE in jar else None

        def _cookie_set(self, token, max_age):
            if self._secure():
                return "%s=%s; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=%d" % (SECURE_COOKIE, token, max_age)
            return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (COOKIE, token, max_age)

        def _cookies_cleared(self):
            """The same attributes as the cookie that was set, with no life left: the browser drops it (D77). Over TLS
            both names, over plain http the plain one (a Secure cookie is never sent there and cannot be touched)."""
            gone = "=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            out = [("Set-Cookie", COOKIE + gone)]
            if self._secure():
                out.append(("Set-Cookie", SECURE_COOKIE + "=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"))
            return out

        def _https(self):
            return getattr(api, "https", None)

        def _gate(self, device, path):
            """D79: while "Owner access only over the secure connection" is on, why this request is refused, or None."""
            box = self._https()
            return box.owner_gate(device, self._secure(), path) if box is not None else None

        def _csrf_ok(self):
            if self.headers.get("X-PVJ-Request") != "1":
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    # netloc keeps IPv6 brackets and the port exactly as the browser wrote them
                    got = urlsplit(origin).netloc.lower()
                except ValueError:
                    return False
                if not got or got != (self.headers.get("Host") or "").lower():
                    return False
            return True

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None, (411, "Content-Length required")
            if length < 0 or length > MAX_BODY:
                return None, (413, "body too large")
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return None, (415, "send application/json")
            if self.headers.get("Transfer-Encoding"):
                return None, (501, "chunked bodies are not supported; send Content-Length")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
                return None, (400, "invalid JSON")
            if not isinstance(data, dict):
                return None, (400, "JSON object expected")
            return data, None

        # --- routing ----------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def _preview(self):
            """GET /api/preview.jpg: what the screen is showing (any paired device, even view-only)."""
            try:
                device = self._who("GET", "/api/preview.jpg")
                api.require(device, "view")
                self._send(200, api.preview_jpeg(device), "image/jpeg")
            except ApiError as e:
                self._json(e.status, {"error": e.message})

        def _qr(self):
            """GET /api/qr.svg?for=panel|view|live: a QR code to print or show. A presenter gets the panel address
            and the guest code; the presenter code needs full access (checked in access_qr)."""
            try:
                device = self._who("GET", "/api/qr.svg")
                api.require(device, "live")
                target = (parse_qs(urlsplit(self.path).query).get("for") or [""])[0]
                self._send(200, api.access_qr(target, self.headers.get("Host", ""), device), "image/svg+xml")
            except ApiError as e:
                self._json(e.status, {"error": e.message})

        def _host_ok(self):
            if host_allowed(self.headers.get("Host"), host_names):
                return True
            self.close_connection = True
            self._json(421, {"error": "unknown host name; open the box by its address or add the name to PVJ_ALLOWED_HOSTS"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            path = urlsplit(self.path).path
            if path == "/api/preview.jpg":
                return self._preview()
            if path == "/api/qr.svg":
                return self._qr()
            if path == "/api/https" or path.startswith("/api/https/"):
                return self._https_route("GET", path, {})
            if path.startswith("/api/"):
                return self._api("GET", path, {})
            if path == "/theme.css":
                return self._send(200, api.theme_css().encode(), TYPES[".css"])
            if path in icon.PATHS:          # a bookmark or a home-screen tile asks for these; they were 404 on the box
                return self._send(200, icon.png(), icon.TYPE)
            name = static.get(path)
            if name is None:
                return self._json(404, {"error": "not found"})
            with open(os.path.join(web_dir, name), "rb") as f:
                body = f.read()
            if name == "index.html":
                body = styled_html(body, api.theme_style())
            ext = os.path.splitext(name)[1]
            # a font's name holds the start of its checksum, so the same name is always the same bytes and may be
            # kept (a phone that fetched it at every page would show the fallback type first each time); everything
            # else is never kept
            self._send(200, body, TYPES.get(ext, "application/octet-stream"), cache="max-age=86400" if ext == ".woff2" else "no-store")

        def _upload(self, update=False):
            """Raw-body upload: POST /api/media/upload?name=clip.mp4[&replace=1]. Streams to disk."""
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            try:
                device = self._who("POST", "/api/system/update/upload" if update else "/api/media/upload")
                api.require(device, "full")
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/octet-stream":
                    raise ApiError(415, "send application/octet-stream")
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = None
                name = (query.get("name") or [""])[0]
                replace = (query.get("replace") or ["0"])[0] == "1"
                # Authenticated owner: the short connection lifetime would cut a big file off, so use
                # an idle timeout instead (the connection cap still applies).
                if self.headers.get("Transfer-Encoding"):
                    raise ApiError(501, "chunked uploads are not supported; send Content-Length")
                token = self._token()

                def still_paired():
                    if (auth.authenticate(token) or api.support.authenticate(token)) is None:  # revoked or ended: stop at once
                        raise ApiError(403, "this device was removed")
                self._reaper.cancel()
                self.connection.settimeout(30)
                # read1 returns what has arrived, so slow senders are noticed after every packet
                if update:
                    result = api.update_upload(name, length, self.rfile.read1, check=still_paired)
                else:
                    result = api.upload(name, length, self.rfile.read1, replace, check=still_paired)
                self._json(200, result)
            except ApiError as e:
                self.close_connection = True  # an unread body must not be parsed as the next request
                self._json(e.status, {"error": e.message})
            except (OSError, ValueError):
                self.close_connection = True
            except Exception:
                traceback.print_exc()
                self.close_connection = True
                self._json(500, {"error": "internal error"})

        def _settings_import(self):
            """POST /api/system/settings/import?confirm=import: the body is the exported file itself. It is read
            here as bytes (larger than MAX_BODY allows) and parsed once, strictly, by boxcare."""
            from . import boxcare
            try:
                device = self._who("POST", "/api/system/settings/import")
                api.require(device, "full")
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    raise ApiError(415, "send application/json")
                if self.headers.get("Transfer-Encoding"):
                    raise ApiError(501, "chunked bodies are not supported; send Content-Length")
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    raise ApiError(411, "Content-Length required")
                if length < 0 or length > boxcare.MAX_IMPORT:
                    raise ApiError(413, "the file is too large for a settings file")
                raw = self.rfile.read(length)
                confirm = (parse_qs(urlsplit(self.path).query).get("confirm") or [""])[0]
                self._json(200, api.boxcare.import_settings(raw, confirm, device, self.client_address[0]))
            except ApiError as e:
                self.close_connection = True  # an unread body must not be parsed as the next request
                self._json(e.status, {"error": e.message})
            except OSError:
                self.close_connection = True
            except Exception:
                traceback.print_exc()
                self.close_connection = True
                self._json(500, {"error": "internal error"})

        def do_POST(self):
            if not self._host_ok():
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._csrf_ok():
                return self._json(403, {"error": "cross-site or missing request header"})
            if path == "/api/media/upload":
                return self._upload()
            if path == "/api/system/update/upload":
                return self._upload(update=True)
            if path == "/api/system/settings/import":
                return self._settings_import()
            body, err = self._body()
            if err:
                return self._json(err[0], {"error": err[1]})
            if path.startswith("/api/https/"):
                return self._https_route("POST", path, body)
            self._api("POST", path, body)

        def _https_route(self, method, path, body):
            """The secure connection's own routes (D79, pvj/httpsbox.py), answered here because they need to know the
            scheme and the Host of the request, which the API's handlers do not see. Full access for all but the
            probe; the files (the request to sign, the root to install) are plain downloads for the owner's browser."""
            from .httpsbox import HttpsError
            box = self._https()
            host = self.headers.get("Host", "")
            try:
                if box is None:
                    raise ApiError(404, "not found")
                if path == "/api/https/probe":              # does this device trust the box: a tiny answer, nothing secret
                    if method != "GET":
                        raise ApiError(405, "method not allowed")
                    return self._json(200, {"https": self._secure()})
                device = self._who(method, path)
                api.require(device, "full")
                if method == "GET":
                    if path == "/api/https":
                        return self._json(200, box.status(self._secure(), host, device))
                    if path == "/api/https/request.csr":
                        text = box.request_pem()
                        if text is None:
                            raise ApiError(404, "no request has been made yet")
                        return self._send(200, text.encode(), "application/pkcs10",
                                          [("Content-Disposition", 'attachment; filename="%s.csr"' % box.hostname())])
                    if path == "/api/https/root.crt":
                        text = box.root_pem()
                        if text is None:
                            raise ApiError(404, "the box has no root certificate to give: upload a certificate file that holds the root after the box's certificate")
                        return self._send(200, text.encode(), "application/x-x509-ca-cert",
                                          [("Content-Disposition", 'attachment; filename="nxlx-root.crt"')])
                    raise ApiError(405 if path in ("/api/https/request", "/api/https/certificate", "/api/https/undo", "/api/https/remove", "/api/https/owner-only", "/api/https/root") else 404,
                                   "method not allowed" if path.startswith("/api/https/") else "not found")
                if path == "/api/https/request":
                    names = body.get("names") if "names" in body else box.default_names()
                    return self._json(200, box.make_request(names, body.get("new_key") is True))
                if path == "/api/https/certificate":
                    leaf = box.install(body.get("certificate"), host)
                    api.log("pvj-web: certificate uploaded by device %s (%s)" % (device["id"], device["name"]))
                    return self._json(200, {"installed": True, "status": box.status(self._secure(), host, device)})
                if path == "/api/https/undo":
                    box.undo()
                    return self._json(200, {"undone": True, "status": box.status(self._secure(), host, device)})
                if path == "/api/https/remove":
                    had = box.remove()
                    return self._json(200, {"removed": had, "status": box.status(self._secure(), host, device)})
                if path == "/api/https/root":
                    out = box.replace_root(body.get("root"), body.get("confirm"), self._secure(), box.device_secure(device))
                    api.log("pvj-web: root replaced by device %s (%s)" % (device["id"], device["name"]))
                    return self._json(200, dict(out, status=box.status(self._secure(), host, device)))
                if path == "/api/https/owner-only":
                    on = box.set_owner_only(body.get("on"), self._secure(), box.device_secure(device))
                    return self._json(200, {"owner_only": on, "status": box.status(self._secure(), host, device)})
                raise ApiError(404, "not found")
            except HttpsError as e:
                self._json(e.status, {"error": e.message})
            except ApiError as e:
                self._json(e.status, {"error": e.message})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "internal error"})

        def _method_not_allowed(self):
            self._json(405, {"error": "method not allowed"}, [("Allow", "GET, POST")])

        do_PUT = do_DELETE = do_PATCH = _method_not_allowed

        def _seen_from(self):
            """The connection's own address (never a header), for Auth to remember where a paired device asks from
            (D78). Not through the support tunnel: that address is the tunnel's, not a device at the studio."""
            client = self.client_address[0]
            return None if api.support.is_remote(client) else client

        def _who(self, method, path):
            """The device behind this request (a paired device, or support during a session), after the support tunnel's
            rules: through the tunnel only support's login works, and some things are never allowed there."""
            from . import support as support_mod
            token = self._token()
            device = auth.authenticate(token, self._seen_from()) or api.support.authenticate(token)
            try:
                api.support.guard(method, path, device, self.client_address[0])
            except support_mod.SupportApiError as e:
                raise ApiError(e.status, e.message)
            gate = self._gate(device, path)
            if gate:
                raise ApiError(403, gate)
            return device

        def _api(self, method, path, body):
            token = self._token()
            device = auth.authenticate(token, self._seen_from()) or api.support.authenticate(token)
            gate = self._gate(device, path)
            if gate:
                return self._json(403, {"error": gate, "https": self._https().https_address()})
            box = self._https()
            if method == "POST" and path == "/api/pair" and box is not None and not box.pin_allowed(self._secure()) \
                    and len(str(body.get("pin", ""))) != 6:
                # a 6-digit code (a guest or presenter, or the code from a controller) is still taken over plain http
                return self._json(403, {"error": "the PIN pairs an owner over the secure connection only: open %s and pair there"
                                        % box.https_address(), "https": box.https_address()})
            try:
                status, payload = api.handle(method, path, body, device, self.client_address[0])
            except Exception:
                # Never drop the connection silently: log for the journal, tell the client plainly.
                traceback.print_exc()
                return self._json(500, {"error": "internal error"})
            extra = []
            if status == 200 and path == "/api/pair" and box is not None and not self._secure() and box.effective() \
                    and isinstance(payload.get("device"), dict) and payload["device"].get("role") == "full":
                # a six-digit OWNER code from a controller (D61) pairs full access: over plain http while the switch
                # is on that is refused too, and the device it just made is taken back (review of #119)
                auth.revoke(payload["device"].get("id"))
                return self._json(403, {"error": "full access is paired over the secure connection only: open %s and use the code there" % box.https_address(),
                                        "https": box.https_address()})
            if status == 200 and path in ("/api/pair", "/api/session") and payload.get("token"):
                extra.append(("Set-Cookie", self._cookie_set(payload["token"], 31536000)))
                if self._secure() and path == "/api/pair" and box is not None and isinstance(payload.get("device"), dict):
                    # a token MADE over TLS (D79). Never at /api/session: that is a token that already existed (a
                    # guest link, or one sniffed off plain http) and only the cookie is new (review of #119, M1)
                    box.mark_secure(payload["device"].get("id"))
            if status == 200 and path == "/api/logout":
                extra.extend(self._cookies_cleared())
            if status == 200 and path == "/api/support/login" and payload.get("token"):
                # support's login ends with the session on the box; the cookie only has to cover the longest a
                # session can last (the studio may extend it), and is useless after that
                extra.append(("Set-Cookie", self._cookie_set(payload["token"], supportd_max_seconds())))
            if payload.get("retry_after"):
                extra.append(("Retry-After", str(payload["retry_after"])))
            self._json(status, payload, extra)

    return Handler


class PvjServer(ThreadingHTTPServer):
    """Threaded server with a hard cap on simultaneous connections, so a flood of idle
    sockets cannot exhaust threads; extra connections get a plain 503 straight away.

    The queue of connections the kernel has finished and this server has not yet taken (the listen backlog) is 128,
    not the 5 that socketserver asks for. A browser opens six connections at once for the files of the page, and
    every request here is its own connection (HTTP/1.0), so two or three people opening the panel in the same
    second ask for more than five before the loop below has taken the first. What the kernel does with the one too
    many differs: macOS resets it (a script of the page then never arrives, measured 2026-10-08). Linux, as
    documented, drops the handshake and leaves the client to ask again about a second later; that was measured on
    CI's Linux only (Ubuntu 24.04, 2026-10-08: with a queue of 5 and nobody taking connections, 33 and 34 of 40
    had not connected after half a second, in two runs) and not on the Pi. 128 is the largest value every kernel
    in use here grants (the kernel lowers a larger one to its own limit, net.core.somaxconn or kern.ipc.somaxconn). A waiting connection costs the
    kernel a little memory and this process nothing: no thread and no file descriptor until it is taken, and then
    the cap below decides. So the bound on threads and descriptors is still max_connections."""

    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, handler, max_connections=64):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            # The thread did not start (the system had no thread to give), so process_request_thread below will
            # never run for this connection and never give its place back: give it back here. Without this every
            # such failure took one of the places for good, and after max_connections of them the panel answered
            # nothing but 503 until the service was started again. socketserver closes the connection and logs.
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class TlsServer(PvjServer):
    """The same panel on a second port, over TLS (D79). The handshake happens in the connection's own thread, after
    the connection cap was taken, so a client that connects and says nothing, or one that refuses the certificate,
    costs the accept loop nothing. With no certificate loaded (box.context None) every connection is closed at once:
    "HTTPS off" and "no certificate" are the same thing and need no restart to change."""

    secure = True

    def __init__(self, address, handler, box, max_connections=64):
        super().__init__(address, handler, max_connections)
        self.box = box
        box.port = self.server_address[1]

    def process_request_thread(self, request, client_address):
        import ssl
        ctx = self.box.context
        if ctx is None:
            self.shutdown_request(request)
            self._slots.release()
            return
        try:
            request.settimeout(10)
            request = ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError, ValueError):       # a device without the root walks away mid-handshake
            self.shutdown_request(request)
            self._slots.release()
            return
        super().process_request_thread(request, client_address)


def write_pin_file(rundir, pin):
    """Show-the-PIN channel: a tmpfs file the display or an admin can read. Cleared on reboot."""
    path = paths.pin_file(rundir)
    # Owner only: the panel writes it and root reads it (`sudo pvj-pin`). The player's account, which opens
    # untrusted media and shares group pvj, has no business reading the full-access PIN.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(pin + "\n")


def supportd_max_seconds():
    from . import supportd
    return supportd.MAX_TOTAL_MINUTES * 60


def build(env=None, player=None):
    env = os.environ if env is None else env
    state = env.get("PVJ_STATE_DIR", "/var/lib/pvj")
    media = env.get("PVJ_MEDIA_DIR", os.path.join(state, "video"))
    settings = Settings(os.path.join(state, "settings.json"))
    settings.load()
    board = hardware.detect_board()
    auth = Auth(settings, rotate_on_start=True)
    registry = Registry(settings, board["kind"])
    addons = os.path.join(state, "addons")
    themes = themes_mod.load_themes()        # the looks the box comes with; the owner's own are read by the Api's store
    player = player or Player()
    rundir = player.rundir
    api = Api(player, settings, auth, registry, themes, media, board, addons_dir=addons,
              spawn=env.get("PVJ_DEV_SPAWN") == "1", on_pin=lambda pin: write_pin_file(rundir, pin))
    api.net = netd_mod.NetdClient(os.path.join(env.get("PVJ_NETD_DIR") or rundir, paths.NETD_SOCKET))
    from . import capture as capture_mod
    api.capture = capture_mod.Capture(rundir, getattr(player, "mpv_bin", "mpv"))
    api.sysd = sysd_mod.SysdClient(paths.sysd_socket())
    from . import httpsbox as httpsbox_mod
    api.https = httpsbox_mod.HttpsBox(os.path.join(state, "tls"), settings, openssl=env.get("PVJ_OPENSSL", "openssl"),
                                      addresses=lambda: [a["local"] for e in api._ip_json() if e.get("ifname") not in ("lo", "wg-pvj")
                                                         for a in e.get("addr_info", []) if a.get("family") == "inet" and a.get("local")],
                                      clock_trusted=lambda: api.clock_status().get("clock_from_network"), log=api.log)
    api.https.load()                         # a certificate that will not load leaves HTTP as it is, with the reason on the page
    from . import supportd as supportd_mod
    api.support.client = supportd_mod.SupportdClient(paths.supportd_socket())
    api.support.panel_port = int(env.get("PVJ_PORT", "8080"))
    api.support.close_leftover()
    api.health.start()                       # notices a short undervoltage with nobody looking
    try:
        api.shaders.tidy()                   # shader texts an earlier panel process left in the runtime folder
    except Exception as e:
        print("pvj-web: old shader texts not removed: %s" % e, file=sys.stderr)
    try:
        api.effects.tidy()                   # an effect an earlier panel process left on comes off: nobody knows its values
    except Exception as e:
        print("pvj-web: an old effect was not taken off: %s" % e, file=sys.stderr)
    try:
        api.transitions.tidy()               # the still of a crossfade an earlier panel process left on the screen comes off
    except Exception as e:
        print("pvj-web: an old transition was not tidied: %s" % e, file=sys.stderr)
    try:
        api.sync.apply()                     # lead or follow, as the settings say
    except Exception as e:                   # a busy port must not stop the panel
        print("pvj-web: sync not started: %s" % e, file=sys.stderr)
    api.projectors.apply()                   # background status of the projectors, if that module is on
    api.sweep_stale_uploads()  # temp files left by a power cut can be gigabytes
    api.osc = osc_mod.OscManager(api, settings)
    api.scheduler = scheduler_mod.Scheduler(api, settings, registry)
    api.autostart = autostart_mod.Autostart(api, settings)
    api.pinscreen = pinscreen_mod.PinScreen(api, auth)
    api.dmx = dmx_mod.DmxManager(api, settings)
    api.midi = midi_mod.MidiHub(api, settings)
    write_pin_file(rundir, auth.current_pin)
    try:
        api.osc.apply()
    except osc_mod.OscError as e:
        print("pvj-web: OSC not started: %s" % e, file=sys.stderr)
    for name, manager in (("DMX", api.dmx), ("MIDI", api.midi)):
        try:
            manager.apply()
        except Exception as e:  # a missing controller or a busy port must not stop the panel
            print("pvj-web: %s not started: %s" % (name, e), file=sys.stderr)
    return api, auth, rundir


def main(argv=None):
    env = os.environ
    try:
        api, auth, rundir = build(env)
    except SettingsError as e:
        print("pvj-web: %s" % e, file=sys.stderr)
        return 1
    host, port = env.get("PVJ_BIND", "0.0.0.0"), int(env.get("PVJ_PORT", "8080"))
    httpd = PvjServer((host, port), make_handler(api, auth))
    tls_port = int(env.get("PVJ_HTTPS_PORT", "0") or 0)
    tlsd = None
    if tls_port:
        try:
            tlsd = TlsServer((host, tls_port), make_handler(api, auth), api.https)
            threading.Thread(target=tlsd.serve_forever, name="pvj-https", daemon=True).start()
        except OSError as e:                 # the port in use or not allowed: the plain panel still comes up
            print("pvj-web: https not listening on %s:%d: %s" % (host, tls_port, e), file=sys.stderr)
    api.scheduler.start()
    api.autostart.start()
    api.pinscreen.start()
    print("pvj-web: listening on %s:%d%s; pairing PIN %s (also in %s/pin)"
          % (host, port, " and https on %d (%s)" % (tls_port, "certificate loaded" if api.https.context else "no certificate yet") if tlsd else "",
             auth.current_pin, rundir), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if tlsd:
            tlsd.shutdown()
            tlsd.server_close()
        api.scheduler.stop()
        api.autostart.stop()
        api.pinscreen.stop()
        if api.capture:
            api.capture.stop()
        api.dmx.stop()
        api.midi.stop()
        api.room.stop()
        api.projectors.stop(final=True)
        if api.osc:
            api.osc.stop()
    return 0
