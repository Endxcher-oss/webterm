# webterm

把服务器上的 shell 放进浏览器。xterm.js 负责终端渲染，Python 标准库负责 PTY 和
WebSocket，没有第三方依赖。shell 以启动 webterm 的用户身份运行，权限完全一致。

## 运行

需要 Linux 和 Python 3.10+（在 3.14 上测试），不需要安装任何 Python 包。先进入项目根
目录，再启动：

    ~/webterm/start.sh

启动后打印监听地址，用浏览器打开即可：

    webterm  http://127.0.0.1:7681/
    访问     无密码（--auth 可开启）
    提示     本机直接打开即可

默认只监听 `127.0.0.1`，不对外开放。要手机或外网访问，用 `~/portal` 把它发布出去：

    cd ~/portal
    python3 -m portal --port 7681

加上 `--auth` 会先生成一个 8 位密码并要求登录，之后这台设备用 cookie 记住，不再重复
验证：

    webterm  http://127.0.0.1:7681/
    密码     abcd-efgh
    提示     密码重启后失效

参数原样传给 webterm，例如 `~/webterm/start.sh --port 9000`。

也可以手动启动，但必须在项目根目录执行，因为包位于 `webterm/webterm/`：

    cd ~/webterm
    python3 -m webterm

在别的目录运行会报 `No module named webterm.__main__`。

| 参数 | 说明 |
| --- | --- |
| `--bind ADDR` | 监听地址，默认 `127.0.0.1` |
| `--port N` | 监听端口，默认 `7681` |
| `--auth` | 生成访问密码并要求登录，默认无密码 |
| `--shell PATH` | 每个标签页运行的 shell，默认 `$SHELL` |
| `--cwd DIR` | 新会话的初始目录，默认 `$HOME` |

`--bind` 决定谁能连上来。默认没有密码，谁能连到端口谁就能进；要发到公网又不想加
`--auth`，靠 portal 的 token 把门。

顶部有 1–4 四个标签页，每个对应一个常驻 shell。默认只启动 1 号，点其他数字才启动。
浏览器关掉、切后台或断网后 shell 继续运行，重新连接会回到原来的标签页，并回放最近
约 256 KB 的输出。连接断开后页面会自动重连（退避间隔最长 10 秒）；启用 --auth 时会话失效
后停止重试并回到登录页。按 Ctrl-C 停止服务。

## 上传文件

在 webterm 的终端里执行 `upload`，浏览器会弹出面板请你选择文件；选完后文件写入
目标目录，命令等到传输结束才返回：

    upload                 # 传到当前目录
    upload ~/Downloads     # 传到指定目录，目录必须已存在
    upload -f ~/Downloads  # 允许覆盖同名文件

可以一次选多个文件。`upload` 只在 webterm 启动的 shell 里可用，它通过 webterm
注入的 `WEBTERM_SOCK` 找到当前标签页；上传过程中切换标签页会取消这次请求。

## 复制文字

点键盘栏的「选择」，终端进入可选状态：长按文字选中，用系统菜单复制，再点「完成」
回到滚动模式。

## 配色

终端配色照搬 `~/.config/kitty/kitty.conf` 中的 Adwaita dark 主题：背景 `#1d1d1d`、
前景 `#deddda`、16 色 ANSI 调色板、块状光标。具体数值在 `web/theme.js`。

## 结构

    webterm/          服务端包：HTTP、WebSocket、PTY、上传控制
    web/              前端：页面、xterm.js、配色
    bin/upload        上传客户端
    docs/             设计与协议说明

## 限制

- 默认没有密码，能连到端口的人可以直接操作 shell；发到公网时要么加 --auth，要么只靠
  portal 的链接把门。
- 启用 --auth 时密码只有一个，不区分用户；拿到密码就等于拿到该用户的 shell。
- 密码（启用时）每次启动重新生成，进程退出即失效；webterm 自己不提供公网入口。
- 断线重连只回放最近约 256 KB 输出；更早的历史不保留。
- 标签页不能单独关闭；在 shell 里 `exit` 后，再点该数字会重新启动它。
