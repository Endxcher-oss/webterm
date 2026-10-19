# Command line entry point: options, access token, and startup banner.

import argparse
import asyncio
import os
import secrets
import sys

from .server import App


def build_parser():
    parser = argparse.ArgumentParser(
        prog="webterm",
        description="Serve an interactive shell over HTTP with token access.",
    )
    parser.add_argument("--bind", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7681, help="port to listen on (default: 7681)")
    parser.add_argument("--token", default=None, help="use a fixed token instead of a random one")
    parser.add_argument(
        "--shell",
        default=os.environ.get("SHELL") or "/bin/bash",
        help="shell to run in each session (default: $SHELL)",
    )
    parser.add_argument(
        "--cwd",
        default=os.path.expanduser("~"),
        help="working directory for new shells (default: home)",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    cwd = os.path.abspath(args.cwd)
    if not os.path.isdir(cwd):
        print(f"webterm: 工作目录不存在: {cwd}", file=sys.stderr)
        return 2
    token = args.token or secrets.token_urlsafe(24)
    app = App(
        bind=args.bind,
        port=args.port,
        token=token,
        shell=args.shell,
        cwd=cwd,
    )
    print(f"webterm  {args.bind}:{args.port}")
    print(f"open     http://{args.bind}:{args.port}/?token={token}")
    print("token    已在上面的链接中，重启后失效", flush=True)
    try:
        asyncio.run(app.run())
    except OSError as exc:
        print(f"webterm: 启动失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
