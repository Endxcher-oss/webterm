# Command line entry point: options, optional access password, public tunnel,
# and the startup banner with a scannable QR code.

import argparse
import asyncio
import os
import secrets
import sys

from . import qr, tunnel
from .server import App

BANNER_NOTICE = "隧道随进程退出而关闭，重启后地址和密码都会变。"
BANNER_NOTICE_OPEN = "隧道随进程退出而关闭，重启后地址会变；无密码，谁拿到链接谁就能进。"
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PASSWORD_GROUP = 4
PASSWORD_GROUPS = 2


def make_password():
    raw = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_GROUP * PASSWORD_GROUPS))
    return "-".join(raw[index:index + PASSWORD_GROUP] for index in range(0, len(raw), PASSWORD_GROUP))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="webterm",
        description="Serve an interactive shell over HTTP, optionally behind a password login.",
    )
    parser.add_argument("--bind", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7681, help="port to listen on (default: 7681)")
    parser.add_argument("--auth",
        action="store_true",
        help="generate an access password and require the login page (default: no password)",
    )
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
    return parser


def _banner(app, url):
    print(f"webterm  {url}")
    if app.bind == "127.0.0.1" and url.startswith("http://"):
        print("         仅本机可访问（--no-tunnel）")
    if app.password:
        print(f"密码     {app.password}")
        print(f"提示     {BANNER_NOTICE}" if url.startswith("https://") else "提示     密码重启后失效")
    else:
        print("访问     无密码（--auth 可开启）")
        print(f"提示     {BANNER_NOTICE_OPEN}" if url.startswith("https://") else "提示     本机直接打开即可")
    print()
    print(qr.to_terminal(url), flush=True)


async def _watch_tunnel(instance, app):
    code = await instance.process.wait()
    print(f"\nwebterm: 公网隧道已断开（cloudflared exit={code}），服务停止。", file=sys.stderr, flush=True)
    app.stop()


async def _serve(app, public):
    instance = None
    watcher = None
    try:
        if public:
            instance = await tunnel.start(app.port)
            url = f"{instance.url}/"
        else:
            url = f"http://{app.bind}:{app.port}/"
        _banner(app, url)
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
    app = App(
        bind=args.bind,
        port=args.port,
        password=make_password() if args.auth else None,
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
