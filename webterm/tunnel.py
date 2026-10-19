# Cloudflare quick tunnel: publishes the local HTTP port as a public https URL
# through an outbound-only connection, so no port forwarding or public address
# is needed. The URL is random and new on every start.

import asyncio
import os
import re
import shutil

URL_PATTERN = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
START_TIMEOUT = 30
SEARCH_PATHS = (
    "/usr/local/bin/cloudflared",
    "/usr/bin/cloudflared",
    "/opt/homebrew/bin/cloudflared",
    os.path.expanduser("~/.local/bin/cloudflared"),
)


class TunnelError(RuntimeError):
    pass


def find_cloudflared():
    explicit = os.environ.get("WEBTERM_CLOUDFLARED")
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise TunnelError(f"WEBTERM_CLOUDFLARED 指向的不是可执行文件: {explicit}")
    found = shutil.which("cloudflared")
    if found:
        return found
    for path in SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise TunnelError(
        "找不到 cloudflared，无法建立公网隧道。安装方式：apt install cloudflared、"
        "brew install cloudflared，或从 https://github.com/cloudflare/cloudflared/releases "
        "下载后放进 PATH；也可以设置 WEBTERM_CLOUDFLARED 指向二进制。"
        "只想在本机访问就用 --no-tunnel。"
    )


def _clean_env():
    env = dict(os.environ)
    for key in PROXY_VARS:
        env.pop(key, None)
    return env


def _error_tail(output):
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:400]


class Tunnel:
    def __init__(self, url, process):
        self.url = url
        self.process = process

    async def stop(self):
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


async def start(port, cloudflared=None, timeout=START_TIMEOUT):
    binary = cloudflared or find_cloudflared()
    process = await asyncio.create_subprocess_exec(
        binary,
        "--no-autoupdate", "tunnel", "--url", f"http://127.0.0.1:{port}",
        "--protocol", "http2",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        env=_clean_env(),
    )
    output = []
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TunnelError(f"建立隧道超时（{timeout} 秒）")
            try:
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                continue
            if not line:
                code = process.returncode if process.returncode is not None else -1
                raise TunnelError(f"cloudflared 退出（code={code}）：{_error_tail(''.join(output))}")
            text = line.decode("utf-8", "replace")
            output.append(text)
            match = URL_PATTERN.search(text)
            if match:
                asyncio.create_task(_drain(process.stdout))
                return Tunnel(match.group(0), process)
    except BaseException:
        await _kill(process)
        raise


async def _drain(stream):
    while True:
        try:
            chunk = await stream.readline()
        except Exception:
            return
        if not chunk:
            return


async def _kill(process):
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), 3)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
