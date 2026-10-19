# One browser connection: its attached terminal slot, outbound queue, and uploads.

import asyncio
import json
import secrets

from .ws import ProtocolError


class ClientSession:
    def __init__(self, app, ws):
        self.app = app
        self.ws = ws
        self.id = secrets.token_hex(8)
        self.slot = None
        self.uploads = {}
        self._queue = asyncio.Queue()
        self._pump_task = None
        self._closed = False

    def push_text(self, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self._queue.put_nowait(("text", text))

    def push_binary(self, data):
        self._queue.put_nowait(("binary", data))

    def queue_depth(self):
        return self._queue.qsize()

    async def run(self):
        self._pump_task = asyncio.create_task(self._pump())
        try:
            await self._message_loop()
        finally:
            await self.close()

    async def _pump(self):
        while True:
            kind, payload = await self._queue.get()
            try:
                if kind == "text":
                    await self.ws.send_text(payload)
                else:
                    await self.ws.send_binary(payload)
            except Exception:
                return
            if self.slot is not None:
                self.slot.maybe_resume()

    async def _message_loop(self):
        while True:
            try:
                message = await self.ws.receive()
            except (asyncio.IncompleteReadError, ConnectionError, ProtocolError):
                return
            if message is None:
                return
            opcode, payload = message
            if opcode != 1:
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if isinstance(data, dict):
                await self._dispatch(data)

    async def _dispatch(self, data):
        kind = data.get("type")
        if kind == "input":
            text = data.get("data")
            if isinstance(text, str) and self.slot is not None:
                self.slot.write(text.encode())
        elif kind == "resize":
            cols, rows = data.get("cols"), data.get("rows")
            if isinstance(cols, int) and isinstance(rows, int) and 1 <= cols <= 2000 and 1 <= rows <= 2000:
                if self.slot is not None:
                    self.slot.resize(cols, rows)
        elif kind == "attach":
            await self._attach(data.get("slot"))
        elif kind == "ping":
            self.push_text({"type": "pong"})
        elif kind == "upload_done":
            await self._finish_upload(data)
        elif kind == "upload_cancel":
            await self._cancel_upload(data)

    async def _attach(self, number):
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = 0
        if not 1 <= number <= self.app.slot_count:
            self.push_text({"type": "attach_error", "message": "无效的标签页"})
            return
        slot = self.app.slot(number)
        if self.slot is not None and self.slot is not slot:
            await self._abort_uploads("已切换标签页")
            self.slot.detach(self)
        self.slot = slot
        try:
            slot.attach(self)
        except OSError:
            self.push_text({"type": "attach_error", "message": "shell 启动失败"})

    async def announce_upload(self, upload):
        self.push_text(
            {
                "type": "upload_request",
                "id": upload.id,
                "dest": upload.dest,
                "overwrite": upload.overwrite,
            }
        )

    async def _finish_upload(self, data):
        upload = self.uploads.pop(str(data.get("id", "")), None)
        if upload is None:
            return
        self.app.uploads.pop(upload.id, None)
        errors = data.get("errors")
        clean = []
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    clean.append({"name": item["name"], "message": str(item.get("message", ""))})
        await upload.finish(clean)

    async def _cancel_upload(self, data):
        upload = self.uploads.pop(str(data.get("id", "")), None)
        if upload is None:
            return
        self.app.uploads.pop(upload.id, None)
        await upload.abort("客户端取消了上传")

    async def _abort_uploads(self, reason):
        for upload in list(self.uploads.values()):
            self.uploads.pop(upload.id, None)
            self.app.uploads.pop(upload.id, None)
            self.push_text({"type": "upload_abort", "id": upload.id})
            await upload.abort(reason)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self.slot is not None:
            self.slot.detach(self)
            self.slot = None
        await self._abort_uploads("连接已断开")
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None
        try:
            await self.ws.send_close(1000)
        except Exception:
            pass
