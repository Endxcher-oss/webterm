# Streamed file reception and the per-request state shared with bin/upload.

import json
import os
import secrets


class UploadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class UploadRequest:
    def __init__(self, client, dest, overwrite):
        self.id = secrets.token_urlsafe(12)
        self.client = client
        self.dest = dest
        self.overwrite = overwrite
        self.files = []
        self.bytes = 0
        self.done = False
        self._writer = None

    def attach(self, writer):
        self._writer = writer

    async def _line(self, payload):
        if self._writer is None:
            return
        try:
            self._writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self._writer.drain()
        except (ConnectionError, OSError):
            self._writer = None

    async def progress(self, name, size):
        await self._line({"type": "file", "name": name, "size": size})

    async def finish(self, errors):
        self.done = True
        await self._line(
            {
                "type": "done",
                "dest": self.dest,
                "files": [{"name": name, "size": size} for name, size in self.files],
                "bytes": self.bytes,
                "errors": errors or [],
            }
        )
        self._close()

    async def abort(self, message):
        if self.done:
            return
        self.done = True
        await self._line({"type": "error", "message": message})
        self._close()

    def _close(self):
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None


def safe_name(name):
    if not name or name in (".", ".."):
        raise UploadError(400, "文件名无效")
    if "/" in name or "\x00" in name:
        raise UploadError(400, "文件名无效")
    if len(name.encode()) > 255:
        raise UploadError(400, "文件名过长")
    return name


async def receive_file(reader, length, dest, name, overwrite):
    name = safe_name(name)
    dest = os.path.realpath(dest)
    if not os.path.isdir(dest):
        raise UploadError(400, "目标目录不存在")
    final = os.path.join(dest, name)
    if os.path.lexists(final) and not overwrite:
        raise UploadError(409, "文件已存在")
    part = os.path.join(dest, f".webterm-{secrets.token_hex(4)}.part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    written = 0
    try:
        while written < length:
            chunk = await reader.read(min(65536, length - written))
            if not chunk:
                raise UploadError(400, "上传中断")
            offset = 0
            while offset < len(chunk):
                offset += os.write(fd, chunk[offset:])
            written += len(chunk)
        os.close(fd)
        fd = None
        try:
            os.replace(part, final)
        except OSError as exc:
            raise UploadError(409, f"无法写入文件: {exc.strerror}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(part):
            try:
                os.unlink(part)
            except OSError:
                pass
    return written
