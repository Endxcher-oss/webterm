// Browser side: xterm rendering, WebSocket transport, key bar, and upload prompts.

import { terminalTheme } from "./theme.js";

const { Terminal } = globalThis;
const FitAddon = globalThis.FitAddon?.FitAddon || globalThis.FitAddon;

const els = {
  term: document.getElementById("term"),
  dot: document.getElementById("dot"),
  status: document.getElementById("status"),
  reconnect: document.getElementById("reconnect"),
  zoomIn: document.getElementById("zoom-in"),
  zoomOut: document.getElementById("zoom-out"),
  tabs: document.getElementById("tabs"),
  keys: document.getElementById("keys"),
  panel: document.getElementById("upload"),
  uploadDest: document.getElementById("upload-dest"),
  uploadProgress: document.getElementById("upload-progress"),
  pick: document.getElementById("upload-pick"),
  cancel: document.getElementById("upload-cancel"),
  fileInput: document.getElementById("file-input"),
};

const CONTROL_KEYS = {
  esc: "\x1b",
  tab: "\t",
  up: "\x1b[A",
  down: "\x1b[B",
  left: "\x1b[D",
  right: "\x1b[C",
};

let token = "";
let ws = null;
let pendingUpload = null;
let ctrlArmed = false;
let lastSeen = 0;
let shellExited = false;
let selecting = false;

const SLOT_MAX = 4;

function readSlot() {
  const value = Number(sessionStorage.getItem("webterm.slot"));
  return Number.isInteger(value) && value >= 1 && value <= SLOT_MAX ? value : 1;
}

let currentSlot = readSlot();

function updateTabs() {
  for (const button of els.tabs.querySelectorAll("button")) {
    button.classList.toggle("active", Number(button.dataset.slot) === currentSlot);
  }
}

function switchSlot(number) {
  if (!Number.isInteger(number) || number < 1 || number > SLOT_MAX) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    currentSlot = number;
    sessionStorage.setItem("webterm.slot", String(number));
    updateTabs();
    return;
  }
  send({ type: "attach", slot: number });
}

function onAttached(number) {
  currentSlot = number;
  shellExited = false;
  sessionStorage.setItem("webterm.slot", String(number));
  term.reset();
  updateTabs();
  setStatus("已连接", "ok");
  scheduleFit();
}

const FONT_MIN = 1;
const FONT_MAX = 30;
const FONT_STEP = 1;

function readFontSize() {
  const stored = localStorage.getItem("webterm.fontSize");
  if (stored === null) return 15;
  const value = Number(stored);
  if (!Number.isFinite(value)) return 15;
  return Math.min(FONT_MAX, Math.max(FONT_MIN, Math.round(value)));
}

let fontSize = readFontSize();

const term = new Terminal({
  theme: terminalTheme,
  fontFamily: 'ui-monospace, "DejaVu Sans Mono", monospace',
  fontSize,
  lineHeight: 1.15,
  cursorBlink: true,
  cursorStyle: "block",
  drawBoldTextInBrightColors: false,
  scrollback: 5000,
  allowTransparency: false,
  macOptionIsMeta: true,
});
const fit = new FitAddon();
term.loadAddon(fit);
term.open(els.term);

function readToken() {
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("token");
  if (fromUrl) {
    sessionStorage.setItem("webterm.token", fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  return sessionStorage.getItem("webterm.token") || "";
}

function setStatus(text, state) {
  els.status.textContent = text;
  els.dot.dataset.state = state || "";
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}

function setCtrl(state) {
  ctrlArmed = state;
  const button = els.keys.querySelector('[data-key="ctrl"]');
  if (button) button.classList.toggle("active", ctrlArmed);
}

function fitTerminal() {
  try {
    fit.fit();
  } catch {
    return;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    send({ type: "resize", cols: term.cols, rows: term.rows });
  }
}

function updateZoomButtons() {
  els.zoomOut.disabled = fontSize <= FONT_MIN;
  els.zoomIn.disabled = fontSize >= FONT_MAX;
}

function setFontSize(next) {
  fontSize = Math.min(FONT_MAX, Math.max(FONT_MIN, Math.round(next)));
  localStorage.setItem("webterm.fontSize", String(fontSize));
  term.options.fontSize = fontSize;
  updateZoomButtons();
  fitTerminal();
}

let touchY = null;
let touchX = null;
let touchDelta = 0;
let touchScrolling = false;

function lineHeight() {
  const rows = els.term.querySelector(".xterm-rows");
  if (!rows || !term.rows) return 0;
  return rows.getBoundingClientRect().height / term.rows;
}

els.term.addEventListener(
  "touchstart",
  (event) => {
    if (selecting) return;
    if (event.touches.length !== 1) {
      touchY = null;
      return;
    }
    touchY = event.touches[0].clientY;
    touchX = event.touches[0].clientX;
    touchDelta = 0;
    touchScrolling = false;
  },
  { passive: true },
);

els.term.addEventListener(
  "touchmove",
  (event) => {
    if (selecting) return;
    if (touchY === null || event.touches.length !== 1) return;
    const point = event.touches[0];
    const dy = point.clientY - touchY;
    const dx = point.clientX - touchX;
    if (!touchScrolling) {
      if (Math.abs(dy) < 6) return;
      if (Math.abs(dx) > Math.abs(dy)) {
        touchY = null;
        return;
      }
      touchScrolling = true;
    }
    touchDelta += dy;
    touchY = point.clientY;
    const height = lineHeight();
    if (height <= 0) return;
    if (term.buffer.active.type === "alternate") {
      if (!term.element || Math.abs(touchDelta) < height * 1.5) return;
      const direction = touchDelta > 0 ? -1 : 1;
      touchDelta = 0;
      term.element.dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: direction * 120,
          deltaMode: 0,
          bubbles: true,
          cancelable: true,
          clientX: point.clientX,
          clientY: point.clientY,
        }),
      );
      event.preventDefault();
      return;
    }
    const lines = Math.trunc(touchDelta / height);
    if (lines === 0) return;
    touchDelta -= lines * height;
    term.scrollLines(-lines);
    event.preventDefault();
  },
  { passive: false },
);

function endTouch() {
  touchY = null;
  touchScrolling = false;
}

els.term.addEventListener("touchend", endTouch, { passive: true });
els.term.addEventListener("touchcancel", endTouch, { passive: true });

let scheduled = false;
function scheduleFit() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    fitTerminal();
  });
}

function connect() {
  if (ws) {
    ws.onclose = null;
    ws.onmessage = null;
    ws.onerror = null;
    try {
      ws.close();
    } catch {
      // the socket is already gone
    }
    ws = null;
  }
  term.reset();
  setStatus("连接中…", "wait");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    lastSeen = Date.now();
    shellExited = false;
    setStatus("已连接", "ok");
    els.reconnect.hidden = true;
    send({ type: "attach", slot: currentSlot });
    term.focus();
  };
  ws.onmessage = (event) => {
    lastSeen = Date.now();
    if (typeof event.data === "string") {
      handleControl(event.data);
    } else {
      term.write(new Uint8Array(event.data));
    }
  };
  ws.onclose = () => {
    ws = null;
    pendingUpload = null;
    els.panel.hidden = true;
    if (!shellExited) setStatus("连接已断开", "bad");
    els.reconnect.hidden = false;
  };
  ws.onerror = () => {};
}

async function boot() {
  token = readToken();
  if (!token) {
    setStatus("缺少 token", "bad");
    term.writeln("地址里没有 token，请从启动 webterm 时打印的链接打开。");
    return;
  }
  try {
    const response = await fetch(`/ping?token=${encodeURIComponent(token)}`, { cache: "no-store" });
    if (!response.ok) {
      setStatus("token 无效或已重启", "bad");
      term.writeln("token 被拒绝，请重启 webterm 并打开新生成的链接。");
      return;
    }
  } catch {
    setStatus("无法连接服务", "bad");
    return;
  }
  connect();
}

function handleControl(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    return;
  }
  if (message.type === "upload_request") {
    showUpload(message);
  } else if (message.type === "upload_abort") {
    finishUpload("上传已取消", "wait");
  } else if (message.type === "attached") {
    onAttached(message.slot);
  } else if (message.type === "attach_error") {
    setStatus(message.message || "无法打开标签页", "bad");
  } else if (message.type === "exit") {
    if (message.slot === currentSlot) {
      shellExited = true;
      setStatus(`shell 已退出 (${message.code})，点标签页重新启动`, "bad");
    }
  }
}

function showUpload(message) {
  pendingUpload = message;
  els.uploadDest.textContent = `上传到 ${message.dest}${message.overwrite ? "（允许覆盖）" : ""}`;
  els.uploadProgress.textContent = "";
  els.pick.disabled = false;
  els.cancel.hidden = false;
  els.panel.hidden = false;
  setStatus("等待选择文件", "wait");
}

function finishUpload(text, state) {
  pendingUpload = null;
  els.panel.hidden = true;
  els.uploadProgress.textContent = "";
  if (text) setStatus(text, state);
}

function postFile(id, file, onProgress) {
  return new Promise((resolve, reject) => {
    const query = new URLSearchParams({ id, name: file.name, token });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/upload?${query}`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => onProgress(event.loaded, event.total);
    xhr.onload = () => {
      if (xhr.status === 200) {
        resolve(xhr.response);
      } else {
        reject(new Error((xhr.response && xhr.response.error) || `HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(file);
  });
}

async function uploadFiles(files) {
  const request = pendingUpload;
  if (!request) return;
  els.pick.disabled = true;
  els.cancel.hidden = true;
  const errors = [];
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    try {
      await postFile(request.id, file, (loaded, total) => {
        const percent = total ? Math.floor((loaded / total) * 100) : 0;
        els.uploadProgress.textContent = `${index + 1}/${files.length} ${file.name} ${percent}%`;
      });
    } catch (error) {
      errors.push({ name: file.name, message: error.message });
    }
  }
  send({ type: "upload_done", id: request.id, errors });
  finishUpload(
    errors.length ? `上传完成，${errors.length} 个失败` : `已接收 ${files.length} 个文件`,
    errors.length ? "wait" : "ok",
  );
}

term.onData((data) => {
  if (ctrlArmed && data.length === 1) {
    const code = data.toUpperCase().charCodeAt(0);
    if (code >= 64 && code < 128) data = String.fromCharCode(code & 0x1f);
    setCtrl(false);
  }
  send({ type: "input", data });
});

function setSelecting(state) {
  selecting = state;
  document.body.classList.toggle("selecting", selecting);
  const button = els.keys.querySelector('[data-key="select"]');
  if (button) {
    button.classList.toggle("active", selecting);
    button.textContent = selecting ? "完成" : "选择";
  }
  if (selecting) {
    setStatus("长按选择文字，复制后点完成", "wait");
  } else {
    window.getSelection()?.removeAllRanges();
    setStatus("已连接", "ok");
  }
}

for (const type of ["mousedown", "touchstart", "touchmove", "contextmenu"]) {
  els.term.addEventListener(
    type,
    (event) => {
      if (selecting) event.stopImmediatePropagation();
    },
    true,
  );
}

els.keys.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const key = button.dataset.key;
  if (key === "select") {
    setSelecting(!selecting);
    return;
  }
  if (key === "ctrl") {
    setCtrl(!ctrlArmed);
    term.focus();
    return;
  }
  const value = CONTROL_KEYS[key];
  if (value !== undefined) {
    send({ type: "input", data: value });
    term.focus();
  }
});

els.pick.addEventListener("click", () => els.fileInput.click());

els.fileInput.addEventListener("change", () => {
  const files = Array.from(els.fileInput.files || []);
  els.fileInput.value = "";
  if (files.length) void uploadFiles(files);
});

els.cancel.addEventListener("click", () => {
  if (!pendingUpload) return;
  send({ type: "upload_cancel", id: pendingUpload.id });
  finishUpload("已取消", "wait");
});

els.reconnect.addEventListener("click", () => {
  void boot();
});

els.zoomOut.addEventListener("click", () => setFontSize(fontSize - FONT_STEP));
els.zoomIn.addEventListener("click", () => setFontSize(fontSize + FONT_STEP));

els.tabs.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  switchSlot(Number(button.dataset.slot));
});

setInterval(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN || document.visibilityState !== "visible") return;
  if (Date.now() - lastSeen > 55000) {
    ws.close();
    return;
  }
  send({ type: "ping" });
}, 20000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && ws && ws.readyState === WebSocket.OPEN) {
    lastSeen = Date.now();
    send({ type: "ping" });
  }
});

new ResizeObserver(scheduleFit).observe(els.term);
window.addEventListener("orientationchange", scheduleFit);
window.visualViewport?.addEventListener("resize", scheduleFit);
document.fonts?.ready.then(scheduleFit);

updateTabs();
updateZoomButtons();
fitTerminal();
void boot();

