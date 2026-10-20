# Command line entry point: options, optional access password, and the startup
# banner.

import argparse
import asyncio
import os
import secrets
import sys

from .server import App

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
    return parser


def _banner(app):
    print(f"webterm  http://{app.bind}:{app.port}/")
    if app.password:
        print(f"密码     {app.password}")
        print("提示     密码重启后失效", flush=True)
    else:
        print("访问     无密码（--auth 可开启）")
        print("提示     本机直接打开即可", flush=True)


async def _serve(app):
    _banner(app)
    await app.run()
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
        return asyncio.run(_serve(app))
    except OSError as exc:
        print(f"webterm: 启动失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
