#!/bin/sh
# 从任意目录启动 webterm。额外的参数原样传给 webterm。

set -eu

cd "$(dirname "$0")"

exec python3 -m webterm "$@"
