# HTTP and WebSocket front end: password login, sessions, routing and static files.

import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .control import ControlServer
from .http import read_request, respond, json_response
from .session import ClientSession
from .slot import TerminalSlot
from .uploads import UploadError, receive_file
from .ws import WebSocket, accept_key

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
SLOT_COUNT = 4
COOKIE_NAME = "webterm_session"
SESSION_TTL = 30 * 24 * 3600
MAX_FORM = 4096

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/vendor/xterm.js": ("vendor/xterm.js", "text/javascript; charset=utf-8"),
    "/vendor/xterm.css": ("vendor/xterm.css", "text/css; charset=utf-8"),
    "/vendor/addon-fit.js": ("vendor/addon-fit.js", "text/javascript; charset=utf-8"),
}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self' ws: wss:; base-uri 'none'; form-action 'none'"
)

CSP_LOGIN = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

LOGIN_ERROR = "密码不正确，请重试。"


class App:
    def __init__(self, bind, port, password, shell, cwd, environ=None):
        self.bind = bind
        self.port = port
        self.password = password
        self.secret = secrets.token_bytes(32)
        self.shell = shell
        self.cwd = cwd
        self.env = dict(environ if environ is not None else os.environ)
        self.sessions = {}
        self.slots = {}
        self.uploads = {}
        self.control = ControlServer(self)
        self._server = None
        self._stop = None
        self.slot_count = SLOT_COUNT

    async def run(self):
        self.control.prepare()
        await self.control.start()
        self._server = await asyncio.start_server(self.handle, self.bind, self.port)
        self._stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (ValueError, NotImplementedError):
                pass
        try:
            await self._stop.wait()
        finally:
            await self.shutdown()

    def stop(self):
        if self._stop is not None:
            self._stop.set()

    def slot(self, number):
        slot = self.slots.get(number)
        if slot is None:
            slot = TerminalSlot(self, number, self.shell, self.cwd, self.env, BIN_DIR)
            self.slots[number] = slot
        return slot

    async def shutdown(self):
        server = self._server
        self._server = None
        if server is not None:
            server.close()
        sessions = list(self.sessions.values())
        self.sessions.clear()
        if sessions:
            await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
        slots = list(self.slots.values())
        self.slots.clear()
        if slots:
            await asyncio.gather(*(slot.close() for slot in slots), return_exceptions=True)
        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), 5)
            except asyncio.TimeoutError:
                pass
        await self.control.close()

    async def handle(self, reader, writer):
        try:
            request = await read_request(reader)
            if request is None:
                await respond(writer, 400, "bad request")
                return
            if request.method == "GET" and request.path == "/ws":
                await self._websocket(request, writer)
            elif request.method == "GET" and request.path == "/ping":
                await respond(writer, 204 if self._authorized(request) else 401)
            elif request.method == "POST" and request.path == "/login":
                await self._login(request, writer)
            elif request.method == "POST" and request.path == "/upload":
                await self._upload(request, writer)
            elif request.method == "GET":
                await self._static(request, writer)
            else:
                await respond(writer, 405, "method not allowed")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            try:
                await respond(writer, 500, "internal error")
            except Exception:
                pass
        finally:
            writer.close()

    async def _static(self, request, writer):
        if request.path == "/favicon.ico":
            await respond(writer, 204)
            return
        if request.path == "/":
            if self._authorized(request):
                await self._file(writer, "index.html", "text/html; charset=utf-8", "no-cache", CSP)
            else:
                await self._login_page(writer, 200)
            return
        entry = STATIC.get(request.path)
        if entry is None:
            await respond(writer, 404, "not found")
            return
        name, content_type = entry
        cache = "public, max-age=604800" if request.path.startswith("/vendor/") else "no-cache"
        await self._file(writer, name, content_type, cache)

    async def _file(self, writer, name, content_type, cache, csp=None):
        try:
            body = (WEB_ROOT / name).read_bytes()
        except OSError:
            await respond(writer, 404, "not found")
            return
        extra = {"Cache-Control": cache}
        if csp is not None:
            extra["Content-Security-Policy"] = csp
        await respond(writer, 200, body, content_type, extra)

    async def _login_page(self, writer, status, error=False):
        try:
            page = (WEB_ROOT / "login.html").read_text(encoding="utf-8")
        except OSError:
            await respond(writer, 500, "login page missing")
            return
        note = f'<p class="error">{LOGIN_ERROR}</p>' if error else ""
        extra = {"Content-Security-Policy": CSP_LOGIN, "Cache-Control": "no-store"}
        await respond(writer, status, page.replace("<!--error-->", note), "text/html; charset=utf-8", extra)

    async def _login(self, request, writer):
        if not self._same_origin(request):
            await respond(writer, 403, "cross-origin request rejected")
            return
        try:
            length = int(request.headers.get("content-length", ""))
        except ValueError:
            await respond(writer, 411, "缺少 Content-Length")
            return
        if length < 0:
            await respond(writer, 400, "Content-Length 无效")
            return
        if length > MAX_FORM:
            await respond(writer, 413, "表单过长")
            return
        try:
            body = await request.reader.readexactly(length) if length else b""
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        fields = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        supplied = (fields.get("password") or [""])[0]
        if not hmac.compare_digest(supplied.encode(), self.password.encode()):
            await self._login_page(writer, 401, error=True)
            return
        extra = {
            "Location": "/",
            "Set-Cookie": self._cookie(request),
            "Cache-Control": "no-store",
        }
        await respond(writer, 303, b"", "text/plain; charset=utf-8", extra)

    async def _websocket(self, request, writer):
        if not self._authorized(request):
            await respond(writer, 401, "unauthorized")
            return
        if not self._same_origin(request):
            await respond(writer, 403, "cross-origin request rejected")
            return
        key = request.headers.get("sec-websocket-key")
        if not key or request.headers.get("upgrade", "").lower() != "websocket":
            await respond(writer, 400, "websocket upgrade required")
            return
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept_key(key).encode() + b"\r\n\r\n"
        )
        await writer.drain()
        ws = WebSocket(request.reader, writer)
        session = ClientSession(self, ws)
        self.sessions[session.id] = session
        try:
            await session.run()
        except Exception:
            pass
        finally:
            self.sessions.pop(session.id, None)

    async def _upload(self, request, writer):
        if not self._authorized(request):
            await json_response(writer, 401, {"error": "unauthorized"})
            return
        if not self._same_origin(request):
            await json_response(writer, 403, {"error": "cross-origin request rejected"})
            return
        upload = self.uploads.get(request.get("id"))
        if upload is None:
            await json_response(writer, 404, {"error": "上传请求已失效"})
            return
        try:
            length = int(request.headers.get("content-length", ""))
        except ValueError:
            await json_response(writer, 411, {"error": "缺少 Content-Length"})
            return
        if length < 0:
            await json_response(writer, 411, {"error": "Content-Length 无效"})
            return
        name = request.get("name")
        try:
            size = await receive_file(request.reader, length, upload.dest, name, upload.overwrite)
        except UploadError as exc:
            await json_response(writer, exc.status, {"error": exc.message})
            return
        upload.files.append((name, size))
        upload.bytes += size
        await upload.progress(name, size)
        await json_response(writer, 200, {"name": name, "size": size})

    def _authorized(self, request):
        return self._valid_cookie(request.cookies.get(COOKIE_NAME))

    def _cookie(self, request):
        expires = int(time.time()) + SESSION_TTL
        value = f"{expires}.{self._sign(str(expires))}"
        # Cloudflare adds cf-visitor at the edge; only then is the session worth marking Secure,
        # since a plain-HTTP local run would otherwise never receive the cookie back.
        secure = "; Secure" if "cf-visitor" in request.headers else ""
        return f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict{secure}; Max-Age={SESSION_TTL}"

    def _sign(self, text):
        digest = hmac.new(self.secret, text.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _valid_cookie(self, raw):
        if not raw or "." not in raw:
            return False
        expires, _, signature = raw.partition(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._sign(expires))

    def _same_origin(self, request):
        origin = request.headers.get("origin")
        if not origin:
            return True
        host = request.headers.get("host", "")
        origin_host = urlsplit(origin).hostname
        if origin_host and origin_host == urlsplit(f"//{host}").hostname:
            return True
        print(
            f"origin mismatch: origin={origin!r} host={host!r} path={request.path}",
            file=sys.stderr,
            flush=True,
        )
        return False
