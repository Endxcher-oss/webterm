#!/bin/sh
# 从任意目录启动 webterm，默认监听 tailnet 地址并沿用默认端口。

set -eu

cd "$(dirname "$0")"

if [ "$#" -eq 0 ]; then
    bind=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
    set -- --bind "${bind:-127.0.0.1}"
fi

exec python3 -m webterm "$@"
