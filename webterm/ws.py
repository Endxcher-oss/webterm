# Server-side WebSocket handshake and frame codec on top of asyncio streams.

import asyncio
import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 1 << 20


class ProtocolError(Exception):
    pass


def accept_key(key):
    digest = hashlib.sha1((key + GUID).encode()).digest()
    return base64.b64encode(digest).decode()


class WebSocket:
    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer
        self._lock = asyncio.Lock()
        self._closed = False

    async def send_text(self, text):
        await self._send(TEXT, text.encode())

    async def send_binary(self, data):
        await self._send(BINARY, data)

    async def _send(self, opcode, payload):
        async with self._lock:
            if self._closed and opcode != CLOSE:
                return
            header = bytearray((0x80 | opcode,))
            size = len(payload)
            if size < 126:
                header.append(size)
            elif size < 1 << 16:
                header.append(126)
                header += struct.pack("!H", size)
            else:
                header.append(127)
                header += struct.pack("!Q", size)
            self._writer.write(bytes(header))
            if payload:
                self._writer.write(payload)
            await self._writer.drain()

    async def send_close(self, code=1000, reason=""):
        self._closed = True
        try:
            await self._send(CLOSE, struct.pack("!H", code) + reason.encode()[:123])
        finally:
            self._writer.close()

    async def receive(self):
        fragments = bytearray()
        first_opcode = None
        while True:
            head = await self._reader.readexactly(2)
            fin = bool(head[0] & 0x80)
            opcode = head[0] & 0x0F
            masked = bool(head[1] & 0x80)
            length = head[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self._reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self._reader.readexactly(8))[0]
            if length > MAX_MESSAGE:
                raise ProtocolError("message too large")
            if not masked:
                raise ProtocolError("client frame is not masked")
            mask = await self._reader.readexactly(4)
            payload = bytearray(await self._reader.readexactly(length))
            for index in range(length):
                payload[index] ^= mask[index & 3]
            if opcode == PING:
                await self._send(PONG, bytes(payload))
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                await self.send_close(1000)
                return None
            if opcode in (TEXT, BINARY):
                fragments = payload
                first_opcode = opcode
            elif opcode == CONT:
                if first_opcode is None:
                    raise ProtocolError("unexpected continuation frame")
                fragments += payload
            else:
                raise ProtocolError("unsupported opcode")
            if len(fragments) > MAX_MESSAGE:
                raise ProtocolError("message too large")
            if fin:
                return first_opcode, bytes(fragments)
