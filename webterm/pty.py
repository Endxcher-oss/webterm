# A pseudo-terminal running one interactive shell, bridged into the asyncio loop.

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios


class PtySession:
    def __init__(self, shell, cwd, env, on_output, cols=80, rows=24):
        self._shell = shell
        self._cwd = cwd
        self._env = env
        self._on_output = on_output
        self.cols = cols
        self.rows = rows
        self._loop = asyncio.get_running_loop()
        self._fd = None
        self._pid = None
        self._pending = bytearray()
        self._writer_armed = False
        self._paused = False
        self._closed = False
        self._waiter = None

    def start(self):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(self._cwd)
                os.execvpe(self._shell, [self._shell], self._env)
            except BaseException:
                os._exit(127)
        self._pid = pid
        self._fd = fd
        os.set_blocking(fd, False)
        self.resize(self.cols, self.rows)
        self._loop.add_reader(fd, self._readable)
        self._waiter = self._loop.run_in_executor(None, os.waitpid, pid, 0)

    async def wait(self):
        _, status = await self._waiter
        return status

    def _readable(self):
        while not self._paused:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if not data:
                self._detach()
                return
            self._on_output(data)

    def pause(self):
        if self._fd is None or self._paused:
            return
        self._paused = True
        try:
            self._loop.remove_reader(self._fd)
        except Exception:
            pass

    def resume(self):
        if self._fd is None or not self._paused:
            return
        self._paused = False
        self._loop.add_reader(self._fd, self._readable)

    def _detach(self):
        if self._closed:
            return
        self._closed = True
        if self._fd is not None:
            if self._writer_armed:
                try:
                    self._loop.remove_writer(self._fd)
                except Exception:
                    pass
                self._writer_armed = False
            try:
                self._loop.remove_reader(self._fd)
            except Exception:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def write(self, data):
        if self._closed or self._fd is None:
            return
        self._pending += data
        self._flush()

    def _flush(self):
        if self._closed or self._fd is None:
            self._pending.clear()
            return
        while self._pending:
            try:
                written = os.write(self._fd, self._pending)
            except BlockingIOError:
                if not self._writer_armed:
                    self._loop.add_writer(self._fd, self._flush)
                    self._writer_armed = True
                return
            except OSError:
                self._pending.clear()
                return
            del self._pending[:written]
        if self._writer_armed:
            self._loop.remove_writer(self._fd)
            self._writer_armed = False

    def resize(self, cols, rows):
        self.cols = cols
        self.rows = rows
        if self._fd is None:
            return
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    async def close(self):
        self._pending.clear()
        self._detach()
        if self._pid is not None:
            try:
                os.killpg(self._pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(self._waiter), 2)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(self._pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(self._waiter), 2)
                except asyncio.TimeoutError:
                    pass
            self._pid = None
