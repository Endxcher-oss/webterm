# A persistent terminal slot: its shell, replay buffer, and attached browser clients.

import asyncio
import os

from .pty import PtySession

REPLAY_LIMIT = 256 * 1024
PAUSE_AT = 256
RESUME_AT = 64


class TerminalSlot:
    def __init__(self, app, number, shell, cwd, env, bin_dir):
        self.app = app
        self.number = number
        self._shell = shell
        self._cwd = cwd
        self._env = env
        self._bin_dir = bin_dir
        self._pty = None
        self._watcher = None
        self._buffer = bytearray()
        self._clients = []
        self._closed = False
        self._size = (80, 24)

    def latest_client(self):
        return self._clients[-1] if self._clients else None

    def _shell_env(self):
        env = dict(self._env)
        env["WEBTERM_SESSION"] = str(self.number)
        env["WEBTERM_SOCK"] = str(self.app.control.path)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.pop("NO_COLOR", None)
        env["PATH"] = f"{self._bin_dir}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}"
        return env

    def ensure_started(self):
        if self._pty is not None or self._closed:
            return
        self._buffer.clear()
        pty = PtySession(
            shell=self._shell,
            cwd=self._cwd,
            env=self._shell_env(),
            on_output=self._output,
            cols=self._size[0],
            rows=self._size[1],
        )
        pty.start()
        self._pty = pty
        self._watcher = asyncio.create_task(self._watch(pty))

    async def _watch(self, pty):
        status = await pty.wait()
        if self._closed or self._pty is not pty:
            return
        self._pty = None
        self._buffer.clear()
        code = os.waitstatus_to_exitcode(status)
        for client in list(self._clients):
            client.push_text({"type": "exit", "slot": self.number, "code": code})

    def _output(self, data):
        self._buffer.extend(data)
        if len(self._buffer) > REPLAY_LIMIT:
            cut = len(self._buffer) - REPLAY_LIMIT
            newline = self._buffer.find(b"\n", cut)
            cut = len(self._buffer) - REPLAY_LIMIT if newline < 0 else newline + 1
            del self._buffer[:cut]
        for client in list(self._clients):
            client.push_binary(data)
        if self._pty is not None and any(client.queue_depth() > PAUSE_AT for client in self._clients):
            self._pty.pause()

    def maybe_resume(self):
        if self._pty is not None and all(client.queue_depth() < RESUME_AT for client in self._clients):
            self._pty.resume()

    def attach(self, client):
        self.ensure_started()
        if client in self._clients:
            self._clients.remove(client)
        client.push_text({"type": "attached", "slot": self.number})
        client.push_binary(b"\x1b[0m\x1b[2J\x1b[H")
        if self._buffer:
            client.push_binary(bytes(self._buffer))
        self._clients.append(client)

    def detach(self, client):
        if client in self._clients:
            self._clients.remove(client)

    def write(self, data):
        if self._pty is not None:
            self._pty.write(data)

    def resize(self, cols, rows):
        self._size = (cols, rows)
        if self._pty is not None:
            self._pty.resize(cols, rows)

    async def close(self):
        self._closed = True
        self._clients.clear()
        if self._watcher is not None:
            self._watcher.cancel()
            self._watcher = None
        if self._pty is not None:
            await self._pty.close()
            self._pty = None
