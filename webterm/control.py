# Unix-socket control channel through which bin/upload asks the browser for files.

import asyncio
import json
import os
import tempfile
from pathlib import Path

from .uploads import UploadError, UploadRequest


class ControlServer:
    def __init__(self, app):
        self.app = app
        self.path = None
        self._dir = None
        self._server = None

    def prepare(self):
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self._dir = Path(base) / f"webterm-{os.getuid()}-{os.getpid()}"
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        self.path = self._dir / "control.sock"

    async def start(self):
        if self.path.exists():
            self.path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        os.chmod(self.path, 0o600)

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass
        if self._dir is not None:
            try:
                self._dir.rmdir()
            except OSError:
                pass

    async def _handle(self, reader, writer):
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            data = json.loads(line)
            try:
                number = int(str(data.get("session", "")))
            except ValueError:
                raise UploadError(400, "无效的标签页")
            slot = self.app.slots.get(number)
            if slot is None:
                raise UploadError(410, "标签页不存在")
            client = slot.latest_client()
            if client is None:
                raise UploadError(410, "该标签页没有已连接的客户端")
            dest = os.path.realpath(str(data.get("dest", "")))
            if not os.path.isdir(dest):
                raise UploadError(400, "目标目录不存在")
            upload = UploadRequest(client, dest, bool(data.get("overwrite")))
            upload.attach(writer)
            self.app.uploads[upload.id] = upload
            client.uploads[upload.id] = upload
            try:
                await client.announce_upload(upload)
            except Exception:
                self.app.uploads.pop(upload.id, None)
                client.uploads.pop(upload.id, None)
                raise UploadError(410, "客户端已断开")
            asyncio.create_task(self._watch(reader, upload))
        except UploadError as exc:
            await self._error(writer, exc.message)
        except (ValueError, ConnectionError, OSError):
            writer.close()

    async def _watch(self, reader, upload):
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, OSError):
            pass
        if upload.done:
            return
        self.app.uploads.pop(upload.id, None)
        upload.client.uploads.pop(upload.id, None)
        upload.client.push_text({"type": "upload_abort", "id": upload.id})
        await upload.abort("上传被取消")

    async def _error(self, writer, message):
        try:
            writer.write((json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n").encode())
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
