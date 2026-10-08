# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""HTTP front end for the control API and the web panel (standard library only).

Security model, in one place:
* every API call except hello, pair and session needs a paired device token
  (HttpOnly SameSite=Strict cookie, or Authorization: Bearer for scripts)
* every state-changing call is a POST with JSON, the X-PVJ-Request header and a
  matching Origin (if the browser sends one), so other websites cannot drive it
* the panel is served with a Content-Security-Policy that forbids inline script,
  external resources and framing
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

        def _token(self):
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return header[7:].strip()
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            return jar[COOKIE].value if COOKIE in jar else None

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
            self._api("POST", path, body)

        def _method_not_allowed(self):
            self._json(405, {"error": "method not allowed"}, [("Allow", "GET, POST")])

        do_PUT = do_DELETE = do_PATCH = _method_not_allowed

        def _who(self, method, path):
            """The device behind this request (a paired device, or support during a session), after the support tunnel's
            rules: through the tunnel only support's login works, and some things are never allowed there."""
            from . import support as support_mod
            token = self._token()
            device = auth.authenticate(token) or api.support.authenticate(token)
            try:
                api.support.guard(method, path, device, self.client_address[0])
            except support_mod.SupportApiError as e:
                raise ApiError(e.status, e.message)
            return device

        def _api(self, method, path, body):
            token = self._token()
            device = auth.authenticate(token) or api.support.authenticate(token)
            try:
                status, payload = api.handle(method, path, body, device, self.client_address[0])
            except Exception:
                # Never drop the connection silently: log for the journal, tell the client plainly.
                traceback.print_exc()
                return self._json(500, {"error": "internal error"})
            extra = []
            if status == 200 and path in ("/api/pair", "/api/session") and payload.get("token"):
                extra.append(("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
                              % (COOKIE, payload["token"])))
            if status == 200 and path == "/api/support/login" and payload.get("token"):
                # support's login ends with the session on the box; the cookie only has to cover the longest a
                # session can last (the studio may extend it), and is useless after that
                extra.append(("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
                              % (COOKIE, payload["token"], supportd_max_seconds())))
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
    many differs: macOS resets it (a script of the page then never arrives, measured 2026-10-08), Linux leaves the
    client to ask again a second later. 128 is the largest value every kernel in use here grants (the kernel lowers
    a larger one to its own limit, net.core.somaxconn or kern.ipc.somaxconn). A waiting connection costs the
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
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


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
    api.scheduler.start()
    api.autostart.start()
    api.pinscreen.start()
    print("pvj-web: listening on %s:%d; pairing PIN %s (also in %s/pin)" % (host, port, auth.current_pin, rundir),
          flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
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
