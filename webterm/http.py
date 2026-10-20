# HTTP/1.1 request parsing and response helpers used by the front end.

import asyncio
import json
from urllib.parse import parse_qs

REASONS = {
    200: "OK",
    204: "No Content",
    303: "See Other",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    411: "Length Required",
    413: "Payload Too Large",
    500: "Internal Server Error",
}


class Request:
    def __init__(self, method, target, headers, reader):
        self.method = method
        self.target = target
        self.headers = headers
        self.reader = reader
        self.path, _, query = target.partition("?")
        self.query = parse_qs(query, keep_blank_values=True)
        self.cookies = {}
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                self.cookies[name] = value

    def get(self, name, default=""):
        values = self.query.get(name)
        return values[0] if values else default


async def read_request(reader):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return None
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ")
    except (UnicodeDecodeError, ValueError):
        return None
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        if name:
            headers[name] = value.strip()
    return Request(method, target, headers, reader)


async def respond(writer, status, body=b"", content_type="text/plain; charset=utf-8", extra=None):
    if isinstance(body, str):
        body = body.encode()
    lines = [
        f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "X-Content-Type-Options: nosniff",
        "Referrer-Policy: no-referrer",
        "Connection: close",
    ]
    for name, value in (extra or {}).items():
        lines.append(f"{name}: {value}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
    await writer.drain()


async def json_response(writer, status, payload, extra=None):
    body = json.dumps(payload, ensure_ascii=False).encode()
    await respond(writer, status, body, "application/json; charset=utf-8", extra)
