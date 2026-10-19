# Command line entry point: options, access token, public tunnel, and the
# startup banner with a scannable QR code.

import argparse
import asyncio
import os
import secrets
import sys

from . import qr, tunnel
from .server import App

BANNER_NOTICE = "隧道随进程退出而关闭，重启后地址和 token 都会变。"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="webterm",
        description="Serve an interactive shell over HTTP with token access.",
    )
    parser.add_argument("--bind", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7681, help="port to listen on (default: 7681)")
    parser.add_argument("--shell",
        default=os.environ.get("SHELL") or "/bin/bash",
        help="shell to run in each session (default: $SHELL)",
    )
    parser.add_argument("--cwd",
        default=os.path.expanduser("~"),
        help="working directory for new shells (default: home)",
    )
    parser.add_argument("--no-tunnel",
        action="store_true",
        help="only listen on --bind instead of opening a public tunnel",
    )
    parser.add_argument("--token",
        default=None,
        help="use a fixed token instead of a random one (only with --no-tunnel)",
    )
    return parser


def _banner(app, token, url):
    print(f"webterm  {url}")
    if app.bind == "127.0.0.1" and url.startswith("http://"):
        print("        仅本机可访问（--no-tunnel）")
    print(f"token    {token}")
    print(f"提示     {BANNER_NOTICE}" if url.startswith("https://") else "提示     token 重启后失效")
    print()
    print(qr.to_terminal(url), flush=True)


async def _watch_tunnel(instance, app):
    code = await instance.process.wait()
    print(f"\nwebterm: 公网隧道已断开（cloudflared exit={code}），服务停止。", file=sys.stderr, flush=True)
    app.stop()


async def _serve(app, public):
    instance = None
    watcher = None
    token = app.token
    try:
        if public:
            instance = await tunnel.start(app.port)
            url = f"{instance.url}/?token={token}"
        else:
            url = f"http://{app.bind}:{app.port}/?token={token}"
        _banner(app, token, url)
        watcher = asyncio.create_task(_watch_tunnel(instance, app)) if instance else None
        await app.run()
    except tunnel.TunnelError as exc:
        print(f"webterm: {exc}", file=sys.stderr)
        return 1
    finally:
        if instance is not None:
            if watcher is not None:
                watcher.cancel()
            await instance.stop()
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    cwd = os.path.abspath(args.cwd)
    if not os.path.isdir(cwd):
        print(f"webterm: 工作目录不存在: {cwd}", file=sys.stderr)
        return 2
    if args.token and not args.no_tunnel:
        print("webterm: --token 只在 --no-tunnel 下生效；公网链接每次启动都用新 token。", file=sys.stderr)
        return 2
    token = args.token or secrets.token_urlsafe(24)
    app = App(
        bind=args.bind,
        port=args.port,
        token=token,
        shell=args.shell,
        cwd=cwd,
    )
    try:
        return asyncio.run(_serve(app, public=not args.no_tunnel))
    except OSError as exc:
        print(f"webterm: 启动失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
