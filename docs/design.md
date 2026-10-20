# 设计与协议

## 进程模型

一个 `webterm` 进程提供页面、维护 4 个常驻终端槽位（`webterm/slot.py`）和一个上传控制
socket。槽位按需创建：默认只启动 1 号，浏览器点 2/3/4 时才启动对应 shell，之后一直
保留到进程退出。

WebSocket 连接本身不拥有 shell，只“挂”在某个槽位上。断线、切后台、刷新页面只是断开
挂载，shell 继续运行；重新连接会重新 attach 并回放最近 256 KB 的输出。多个客户端可以
同时挂到同一个槽位：输出广播给所有人，输入来自任意一方。

槽位里的 shell 退出后不会自动重启；槽位保留最后的输出和“已退出”状态，再点一次对应
数字才会重新启动。

进程收到 SIGTERM 时先关连接，再 `SIGHUP` 每个槽位的进程组（2 秒内没退出再 `SIGKILL`）。

## 认证

默认不认证：不给 `--auth` 时，`/`、`/ping`、`/ws`、`/upload` 一律放行，启动横幅写明
「无密码」。这种模式适合本机或私网；在公网入口下等于谁拿到链接谁就能操作 shell。

加 `--auth` 时：

- 启动时生成 8 位密码，分两段四位，字符集去掉了 `0/O/1/I/l` 这类易混字符，只打印在终端。
- 登录页 `POST /login` 提交表单，服务端用 `hmac.compare_digest` 比对；成功后下发
  `webterm_session` cookie，值是 `<过期时间>.<HMAC-SHA256>`。
- 签名密钥是进程级随机数（`secrets.token_bytes(32)`，不落盘），所以进程一重启，之前
  所有 cookie 自动失效，不需要维护吊销列表。
- cookie 带 `HttpOnly`、`SameSite=Strict`、30 天有效期；只有请求带 `cf-visitor`
  （说明是 Cloudflare 边缘转发来的）时才加 `Secure`，否则本机 http 访问收不到 cookie。
- `/ping`、`/ws`、`/upload` 都只认这个 cookie；WebSocket 与 `/upload` 仍检查 `Origin`：
  带了 Origin 就必须与 `Host` 同主机名，端口和协议不比（隧道、反代会改写这两个维度，
  主机名才是真正的信任边界）。不匹配的请求记为一行 `origin mismatch` 到 stderr。
- 登录 POST 不做 `Origin` 检查。密码本身就是要防的那道门，伪造登录在没有密码时毫无
  收益；而部分内置浏览器（微信预览、沙箱 iframe）会以 `null` 源提交表单，检查只会
  误伤。`Origin: null` 仍进不了 `/ws` 和 `/upload`，因为 `*.trycloudflare.com` 同属
  一个 site，`SameSite=Strict` 挡不住兄弟隧道，这两个端点必须靠 Origin 把关。
- 密码是进程级的，不是会话级的：同一个密码可以在多台设备上分别登录，各自拿到独立 cookie。

## 公网入口

默认启动流程是先开隧道、再对外服务：`cloudflared tunnel --url http://127.0.0.1:<port>`
在 Cloudflare 边缘注册一条快速隧道，只建立出站连接，因此不需要公网地址，也不需要在
路由器上做端口映射。隧道地址是随机的，每次启动都不同。

- 解析 URL 的正则排除了保留子域 `api.`，否则会拿到 `api.trycloudflare.com`，打开只有
  `{"code":10005,"message":"Method Not Allowed"}`。
- 固定用 `--protocol http2`：QUIC 走 UDP 7844，被运营商或企业网封掉时 cloudflared 仍能
  注册成功，但手机端会看到 `error 1033`。
- 启动 cloudflared 前会删掉环境里的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等变量。
  shell 里的本地代理（Clash 之类）会被子进程继承，把隧道边缘连接绕进本地代理，表现同样
  是 1033。
- 二维码用的 `webterm/qr.py` 是自带实现：字节模式、纠错等级 M、版本 1–10（最多 213 字节），
  掩码按 ISO/IEC 18004 的四条罚分规则评测。终端里用白色方块表示浅色模块、黑底空格表示
  深色模块。
- 隧道进程意外退出时服务一并停止（`App.stop()`），而不是留下一个没有公网入口的进程。

## 传输

### WebSocket

RFC 6455 的最小实现（`webterm/ws.py`）：不支持扩展和子协议，客户端帧必须带掩码，
单条消息上限 1 MiB。服务端到客户端：

- 二进制帧：PTY 的原始输出，前端直接交给 `term.write()`，避免 UTF-8 被帧边界截断。
- 文本帧：控制消息。

客户端到服务端一律是文本 JSON：

| 消息 | 作用 |
| --- | --- |
| `{"type":"input","data":"..."}` | 键盘输入，服务端编码成 UTF-8 写入 PTY |
| `{"type":"resize","cols":N,"rows":N}` | 调整当前槽位的 PTY 窗口，范围 1–2000 |
| `{"type":"attach","slot":N}` | 挂到 1–4 号槽位，服务端回 `attached` 并回放输出 |
| `{"type":"ping"}` | 保活，服务端回 `{"type":"pong"}` |
| `{"type":"upload_done","id":"...","errors":[...]}` | 浏览器报告上传结束 |
| `{"type":"upload_cancel","id":"..."}` | 用户取消了上传 |

服务端到客户端的控制消息：`attached`、`attach_error`、`upload_request`、`upload_abort`、
`exit`（带槽位号）、`pong`。

PTY 输出先进入每个连接的发送队列。任一客户端的队列超过 256 条就暂停读取 PTY，全部降到
64 条以下再恢复，避免慢客户端把输出丢在半路。

### 上传流程

1. shell 里执行 `upload [目录] [-f]`。
2. `upload` 读取 `WEBTERM_SOCK` 和 `WEBTERM_SESSION`（值是槽位号），把
   `{"session","dest","overwrite"}` 写入控制 socket，然后阻塞读取结果行。
3. webterm 找到该槽位最近 attach 的客户端，校验目录，创建请求（`id` 为
   `token_urlsafe(12)`），向它推送 `upload_request`。
4. 浏览器显示面板。用户选文件后，对每个文件发起
   `POST /upload?id=...&name=...`，请求体是文件原始字节，用 XHR 显示进度。
5. 服务端边读边写到 `dest/.webterm-<随机>.part`，完成后 `os.replace` 成目标文件，并把
   `{"type":"file","name","size"}` 追加到控制 socket。
6. 全部结束后浏览器发 `upload_done`，服务端把 `{"type":"done","dest","files","bytes","errors"}`
   写给 `upload`，命令打印汇总并退出。

异常路径：浏览器取消 → `upload_cancel` → `upload` 收到 `{"type":"error"}`；`upload` 被
Ctrl-C → 控制 socket 断开 → 服务端删掉请求并向浏览器推 `upload_abort`；WebSocket 断开 →
所有未完成请求向 `upload` 报“连接已断开”；切换标签页则报“已切换标签页”。

### 控制 socket

路径 `$XDG_RUNTIME_DIR/webterm-<uid>-<pid>/control.sock`（没有 `XDG_RUNTIME_DIR` 时退到
临时目录），目录 0700、socket 0600。它没有额外的应用层认证：能连上的进程本来就和
webterm 同用户，权限不会超过直接开一个 shell。

## 文件写入的边界

- 文件名只取客户端给的 basename，拒绝空名、`.`、`..`、含 `/` 或 NUL、超过 255 字节。
- 目标目录必须已存在；`dest` 经 `realpath` 后要求是目录。
- 默认不覆盖：同名文件已存在返回 409。`-f` 才允许，且用临时文件 + `os.replace`，
  写失败不会留下半个目标文件。
- 上传发生在 webterm 自己的权限下，不尝试降权或沙箱。

## 主题

`web/theme.js` 的数值来自 `~/.config/kitty/kitty.conf` 第 2684–2732 行的 Adwaita dark：

- `background #1d1d1d`、`foreground #deddda`、`cursor #deddda`、`cursor_text_color #1d1d1d`
- `color0`–`color15` 原样搬进 xterm 的 `black`…`brightWhite`
- kitty 的 `selection_background` 是 `none`（按单元格反色显示），xterm 没有逐单元格反色，
  所以用主题里的 `active_border_color #4f4f4f` 作选中背景，保留前景色。

`font_size` 是 kitty 的 16pt，浏览器里用 15px，否则手机上单行放不下太多列；这只影响字号，
不影响配色。光标形状（block）和闪烁沿用 kitty 的配置。

会话环境强制 `TERM=xterm-256color`、`COLORTERM=truecolor`，并删掉从 webterm 进程继承来的
`NO_COLOR`：终端本身支持真彩色，这个变量只会让 htop、fastfetch 之类的程序误以为该输出灰色。

## 触控滚动

xterm 6 只渲染可视区域，`.xterm-viewport` 的 `scrollHeight` 等于 `clientHeight`，没有原生
滚动容器，触摸滑动不会自动生效。前端在终端区域监听单指竖滑，正常屏幕按单元格高度累计位移后调用 `term.scrollLines()`：
向下拖看更早的内容，向上拖回到底部；备用屏幕则合成滚轮事件，让 xterm 转成鼠标滚轮或
上下方向键。横向手势直接放行，交给浏览器。

点键盘栏的「选择」会进入选择模式：打开终端文字的原生可选中性，并临时拦截 xterm 的
鼠标/触摸处理，交给系统长按选择与复制。

回放缓冲只保留最近 256 KB，截断点优先落在换行，其次落在转义序列开头，避免把半截控制
序列当作普通文字显示。

## 保活

移动端锁屏后 TCP 可能悄无声息地断掉。前端可见时每 20 秒发一次 `ping`，55 秒没收到任何
消息就主动关闭 WebSocket，再自动重连。重连前先用 `/ping` 校验会话，通过才重新建立
连接；401 表示 cookie 已失效（多为 webterm 重启过），启用 `--auth` 时直接回登录页。退避间隔从 1 秒翻倍
到 10 秒封顶，页面回到前台时立即重试一次。后台标签页不参与超时判断，避免定时器被节流后
误判。

## 已知取舍

- 公网地址随进程变化，密码（启用 `--auth` 时）每次启动重新生成；没有固定域名，也没有凭据持久化。
- 启用 `--auth` 时，唯一入口是 Cloudflare 的随机域名加一个 8 位密码（约 40 bit，只能在
  线猜，没有限速）；地址和密码同时泄露就等于暴露 shell，只能靠重启进程收回。不加
  `--auth` 则只有随机域名这一层。冷启动时新域名要几十秒才在全网解析出来。
- 只回放最近 256 KB 输出；更早的内容不会保留。
- 槽位没有关闭操作，`exit` 之后需要再点一次标签页才重启。
- 没有多用户、没有会话管理界面；一个进程一个密码（启用时），够用为止。
- 终端查询（DA1、OSC 11 取背景色等）由 xterm.js 自动应答并通过 `onData` 回传，因此
  fish 这类启动时会探测终端的 shell 可以正常工作。
