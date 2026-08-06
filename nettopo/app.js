// ===========================================================
// NetTopo — ネットワーク構成図エディタ（趣味プロジェクト）
//
// 段階1: ブラウザ内で完結する編集UI。
//   - 機器ノードの追加・ドラッグ移動・接続・削除
//   - 機器ごとの 名称/種別/IP/要件/メモ 編集
//   - 複数図面の管理（localStorage へ自動保存）・JSON書き出し/読み込み
// 段階2以降（予定）: サーバ＋DB保存・共有。データ構造はそのまま
// サーバへ送れる形（nodes/links の素朴なJSON）にしてある。
// ===========================================================
"use strict";

// ---------------- 機器種別の定義 ----------------
const DEVICE_TYPES = [
  { id: "internet", name: "インターネット", icon: "☁️" },
  { id: "router",   name: "ルーター",       icon: "🌐" },
  { id: "firewall", name: "ファイアウォール", icon: "🔥" },
  { id: "l3switch", name: "L3スイッチ",     icon: "🔀" },
  { id: "l2switch", name: "L2スイッチ",     icon: "🔁" },
  { id: "loadbalancer", name: "ロードバランサ", icon: "⚖️" },
  { id: "server",   name: "サーバ",         icon: "🖥️" },
  { id: "storage",  name: "ストレージ",     icon: "💾" },
  { id: "ap",       name: "無線AP",         icon: "📶" },
  { id: "pc",       name: "PC",            icon: "💻" },
  { id: "phone",    name: "スマホ",         icon: "📱" },
  { id: "printer",  name: "プリンタ",       icon: "🖨️" },
  { id: "camera",   name: "カメラ",         icon: "📷" },
  { id: "other",    name: "その他",         icon: "📦" },
];
const TYPE_MAP = Object.fromEntries(DEVICE_TYPES.map(t => [t.id, t]));

// ノードの描画サイズ（中心座標 x,y からの矩形）
const NODE_W = 92, NODE_H = 60;

const STORAGE_KEY = "nettopo.diagrams.v1";
const CURRENT_KEY = "nettopo.current.v1";

// ---------------- 状態 ----------------
let diagrams = [];      // 図面一覧（メタ含む全データ）
let current = null;     // 編集中の図面オブジェクト（diagrams 内の参照）
let mode = "select";    // select | connect | delete
let selection = null;   // { kind: "node"|"link", id }
let connectFrom = null; // 接続モードで最初にクリックしたノードid
let viewBox = { x: 0, y: 0, w: 1200, h: 800 };
let saveTimer = null;

const $ = (id) => document.getElementById(id);
const svg = $("canvas");
const layerNodes = $("layer-nodes");
const layerLinks = $("layer-links");

const uid = (prefix) =>
  prefix + "_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 7);

// ---------------- 図面データ ----------------
function newDiagram(name) {
  return { id: uid("dg"), name: name || "無題の構成図", nodes: [], links: [], updatedAt: Date.now() };
}

// 初回起動時のサンプル。空キャンバスだと操作イメージが湧かないため置いておく。
function sampleDiagram() {
  const d = newDiagram("サンプル: 小規模オフィス");
  const mk = (type, label, x, y, extra) =>
    ({ id: uid("nd"), type, label, ip: "", requirements: "", memo: "", x, y, ...(extra || {}) });
  const inet = mk("internet", "インターネット", 460, 80);
  const fw   = mk("firewall", "FW-01", 460, 200, { requirements: "UTM機能あり。フェイルオーバー構成は将来検討。" });
  const rt   = mk("router", "RT-01", 460, 320, { ip: "192.168.0.1/24" });
  const sw   = mk("l2switch", "SW-01", 460, 440, { requirements: "PoE給電（AP用）" });
  const sv   = mk("server", "ファイルサーバ", 260, 560, { ip: "192.168.0.10", memo: "NASでも可" });
  const pc   = mk("pc", "業務PC", 460, 560);
  const ap   = mk("ap", "AP-01", 660, 560);
  d.nodes = [inet, fw, rt, sw, sv, pc, ap];
  const ln = (a, b, label, style) =>
    ({ id: uid("lk"), from: a.id, to: b.id, label: label || "", style: style || "solid" });
  d.links = [
    ln(inet, fw), ln(fw, rt), ln(rt, sw, "1Gbps"),
    ln(sw, sv), ln(sw, pc), ln(sw, ap),
  ];
  return d;
}

function loadAll() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    diagrams = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(diagrams)) diagrams = [];
  } catch (e) {
    diagrams = [];
  }
  if (diagrams.length === 0) diagrams = [sampleDiagram()];
  const curId = localStorage.getItem(CURRENT_KEY);
  current = diagrams.find(d => d.id === curId) || diagrams[0];
}

function persist() {
  current.updatedAt = Date.now();
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(diagrams));
    localStorage.setItem(CURRENT_KEY, current.id);
    setSaveStatus(false);
  } catch (e) {
    // 容量超過など。無音で失われるより明示する。
    setSaveStatus(false, "保存失敗（容量超過?）");
  }
}

// 変更のたびに呼ぶ。少し遅延させてまとめて保存する。
function markDirty() {
  setSaveStatus(true);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(persist, 400);
}

function setSaveStatus(dirty, text) {
  const el = $("save-status");
  el.textContent = text || (dirty ? "保存中…" : "保存済み");
  el.classList.toggle("is-dirty", !!dirty);
}

// ---------------- 参照ヘルパ ----------------
const nodeById = (id) => current.nodes.find(n => n.id === id);
const linkById = (id) => current.links.find(l => l.id === id);

// ---------------- キャンバス描画 ----------------
function applyViewBox() {
  svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
  // グリッド矩形はviewBoxに追従させて常に全面に敷く
  const g = $("grid-bg");
  g.setAttribute("x", viewBox.x - 1000); g.setAttribute("y", viewBox.y - 1000);
  g.setAttribute("width", viewBox.w + 2000); g.setAttribute("height", viewBox.h + 2000);
  const pct = Math.round((svg.clientWidth / viewBox.w) * 100);
  $("zoom-label").textContent = (isFinite(pct) && pct > 0 ? pct : 100) + "%";
}

function renderAll() {
  renderLinks();
  renderNodes();
  updateStatus();
}

function renderNodes() {
  layerNodes.textContent = "";
  for (const n of current.nodes) {
    const t = TYPE_MAP[n.type] || TYPE_MAP.other;
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.classList.add("node");
    if (selection && selection.kind === "node" && selection.id === n.id) g.classList.add("selected");
    if (connectFrom === n.id) g.classList.add("connect-from");
    g.dataset.id = n.id;
    g.setAttribute("transform", `translate(${n.x},${n.y})`);

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.classList.add("body");
    rect.setAttribute("x", -NODE_W / 2); rect.setAttribute("y", -NODE_H / 2);
    rect.setAttribute("width", NODE_W); rect.setAttribute("height", NODE_H);
    rect.setAttribute("rx", 10);
    g.appendChild(rect);

    const icon = text("icon", 0, -2, t.icon);
    g.appendChild(icon);

    const label = text("label", 0, NODE_H / 2 + 16, n.label || t.name);
    g.appendChild(label);

    if (n.ip) g.appendChild(text("sub", 0, NODE_H / 2 + 30, n.ip));

    // 要件・メモが書かれているノードには目印を付ける
    if ((n.requirements || "").trim() || (n.memo || "").trim()) {
      g.appendChild(text("badge", NODE_W / 2 - 12, -NODE_H / 2 + 14, "📝"));
    }
    layerNodes.appendChild(g);
  }
}

function text(cls, x, y, str) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "text");
  if (cls) el.classList.add(cls);
  el.setAttribute("x", x); el.setAttribute("y", y);
  el.textContent = str;
  return el;
}

function renderLinks() {
  layerLinks.textContent = "";
  for (const l of current.links) {
    const a = nodeById(l.from), b = nodeById(l.to);
    if (!a || !b) continue;
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.classList.add("link");
    if (l.style === "dashed") g.classList.add("dashed");
    if (selection && selection.kind === "link" && selection.id === l.id) g.classList.add("selected");
    g.dataset.id = l.id;

    for (const cls of ["visible", "hit"]) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.classList.add(cls);
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      g.appendChild(line);
    }
    if (l.label) {
      const p = linkLabelPos(a, b);
      g.appendChild(text("", p.x, p.y, l.label));
    }
    layerLinks.appendChild(g);
  }
}

// リンクラベルの位置。中点から線の垂直方向へ少しずらし、
// ノード名（ノード直下に描画）との重なりを避ける。
function linkLabelPos(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return {
    x: (a.x + b.x) / 2 + (-dy / len) * 14,
    y: (a.y + b.y) / 2 + (dx / len) * 14 - 4,
  };
}

// ドラッグ中はそのノードと接続線の座標だけ更新する（全再描画を避ける）
function updateNodePosition(n) {
  const g = layerNodes.querySelector(`g[data-id="${n.id}"]`);
  if (g) g.setAttribute("transform", `translate(${n.x},${n.y})`);
  for (const l of current.links) {
    if (l.from !== n.id && l.to !== n.id) continue;
    const lg = layerLinks.querySelector(`g[data-id="${l.id}"]`);
    if (!lg) continue;
    const a = nodeById(l.from), b = nodeById(l.to);
    for (const line of lg.querySelectorAll("line")) {
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    }
    const t = lg.querySelector("text");
    if (t) {
      const p = linkLabelPos(a, b);
      t.setAttribute("x", p.x); t.setAttribute("y", p.y);
    }
  }
}

function updateStatus() {
  const modeNames = { select: "選択モード", connect: "接続モード", delete: "削除モード" };
  $("status-mode").textContent = modeNames[mode];
  $("status-count").textContent =
    `ノード ${current.nodes.length} ／ 接続 ${current.links.length}`;
  const hint = $("canvas-hint");
  if (mode === "connect") {
    hint.textContent = connectFrom
      ? "接続先のノードをクリック（Escで中止）"
      : "接続元のノードをクリック";
  } else if (mode === "delete") {
    hint.textContent = "削除したいノード・線をクリック";
  } else {
    hint.textContent = "";
  }
}

// ---------------- 座標変換 ----------------
function clientToCanvas(ev) {
  const pt = new DOMPoint(ev.clientX, ev.clientY);
  const m = svg.getScreenCTM();
  if (!m) return { x: 0, y: 0 };
  const p = pt.matrixTransform(m.inverse());
  return { x: p.x, y: p.y };
}

function maybeSnap(v) {
  return $("snap-grid").checked ? Math.round(v / 10) * 10 : v;
}

// ---------------- ノード・接続の操作 ----------------
function addNode(typeId) {
  const t = TYPE_MAP[typeId] || TYPE_MAP.other;
  // 表示中央付近に、重ならないよう少しずつずらして置く
  const k = current.nodes.length % 7;
  const n = {
    id: uid("nd"),
    type: t.id,
    label: "",
    ip: "",
    requirements: "",
    memo: "",
    x: maybeSnap(viewBox.x + viewBox.w / 2 + (k - 3) * 24),
    y: maybeSnap(viewBox.y + viewBox.h / 2 + (k - 3) * 18),
  };
  current.nodes.push(n);
  select({ kind: "node", id: n.id });
  markDirty();
  renderAll();
}

function removeNode(id) {
  current.links = current.links.filter(l => l.from !== id && l.to !== id);
  current.nodes = current.nodes.filter(n => n.id !== id);
  if (selection && selection.kind === "node" && selection.id === id) select(null);
  if (connectFrom === id) connectFrom = null;
  markDirty();
  renderAll();
}

function addLink(fromId, toId) {
  if (fromId === toId) return;
  const dup = current.links.some(l =>
    (l.from === fromId && l.to === toId) || (l.from === toId && l.to === fromId));
  if (dup) return;
  const l = { id: uid("lk"), from: fromId, to: toId, label: "", style: "solid" };
  current.links.push(l);
  select({ kind: "link", id: l.id });
  markDirty();
  renderAll();
}

function removeLink(id) {
  current.links = current.links.filter(l => l.id !== id);
  if (selection && selection.kind === "link" && selection.id === id) select(null);
  markDirty();
  renderAll();
}

// ---------------- 選択とプロパティパネル ----------------
function select(sel) {
  selection = sel;
  renderProps();
}

function renderProps() {
  const empty = $("props-empty"), nodeForm = $("props-node"), linkForm = $("props-link");
  empty.hidden = !!selection;
  nodeForm.hidden = !(selection && selection.kind === "node");
  linkForm.hidden = !(selection && selection.kind === "link");

  if (selection && selection.kind === "node") {
    const n = nodeById(selection.id);
    if (!n) { select(null); return; }
    $("prop-label").value = n.label;
    $("prop-type").value = n.type;
    $("prop-ip").value = n.ip || "";
    $("prop-req").value = n.requirements || "";
    $("prop-memo").value = n.memo || "";
  } else if (selection && selection.kind === "link") {
    const l = linkById(selection.id);
    if (!l) { select(null); return; }
    const a = nodeById(l.from), b = nodeById(l.to);
    const nm = (n) => n ? (n.label || (TYPE_MAP[n.type] || TYPE_MAP.other).name) : "?";
    // textContent 代入なのでエスケープ不要（innerHTMLは使わない）
    $("prop-link-ends").textContent = `${nm(a)} ⇄ ${nm(b)}`;
    $("prop-link-label").value = l.label || "";
    $("prop-link-style").value = l.style || "solid";
  }
}

// プロパティ入力 → モデル反映（構造は変わらないため部分再描画で足りるが、
// バッジやラベルの出し分けがあるので素直に全再描画する。規模的に十分軽い）
function bindProps() {
  const sel = document.createElement("select");
  $("prop-type").replaceWith(sel);
  sel.id = "prop-type";
  for (const t of DEVICE_TYPES) {
    const o = document.createElement("option");
    o.value = t.id;
    o.textContent = `${t.icon} ${t.name}`;
    sel.appendChild(o);
  }

  const onNodeInput = () => {
    if (!selection || selection.kind !== "node") return;
    const n = nodeById(selection.id);
    if (!n) return;
    n.label = $("prop-label").value;
    n.type = $("prop-type").value;
    n.ip = $("prop-ip").value;
    n.requirements = $("prop-req").value;
    n.memo = $("prop-memo").value;
    markDirty();
    renderAll();
  };
  for (const id of ["prop-label", "prop-type", "prop-ip", "prop-req", "prop-memo"]) {
    $(id).addEventListener("input", onNodeInput);
  }
  $("prop-delete-node").addEventListener("click", () => {
    if (selection && selection.kind === "node") removeNode(selection.id);
  });

  const onLinkInput = () => {
    if (!selection || selection.kind !== "link") return;
    const l = linkById(selection.id);
    if (!l) return;
    l.label = $("prop-link-label").value;
    l.style = $("prop-link-style").value;
    markDirty();
    renderAll();
  };
  $("prop-link-label").addEventListener("input", onLinkInput);
  $("prop-link-style").addEventListener("input", onLinkInput);
  $("prop-delete-link").addEventListener("click", () => {
    if (selection && selection.kind === "link") removeLink(selection.id);
  });
}

// ---------------- スマホ用ドロワー ----------------
const isMobile = () => window.matchMedia("(max-width: 768px)").matches;

function openDrawer(which) {
  closeDrawers();
  $(which).classList.add("is-open");
  $("drawer-backdrop").hidden = false;
}

function closeDrawers() {
  $("palette").classList.remove("is-open");
  $("props").classList.remove("is-open");
  $("drawer-backdrop").hidden = true;
}

function bindDrawers() {
  $("mb-palette").addEventListener("click", () => openDrawer("palette"));
  $("mb-props").addEventListener("click", () => openDrawer("props"));
  // click だとタップ由来の合成clickが「開いた直後のドロワー」を即閉じしてしまうため
  // pointerdown（新しいタップの開始）でのみ閉じる。
  $("drawer-backdrop").addEventListener("pointerdown", closeDrawers);
  for (const b of document.querySelectorAll(".drawer-close")) {
    b.addEventListener("click", closeDrawers);
  }
}

// ---------------- ポインタ操作（ドラッグ・パン・クリック） ----------------
let drag = null; // { kind: "node", id, dx, dy, moved } | { kind: "pan", sx, sy, vx, vy }

// ピンチズーム用: 画面に触れている指（ポインタ）を追跡する
const activePointers = new Map(); // pointerId -> {x, y}（client座標）
let pinch = null;                 // {d, cx, cy} 直前の2本指の距離と中点

// ポインタ捕捉。ドラッグ中に指がSVG外へ出ても追跡し続けるための補助で、
// 失敗しても操作は成立するため例外は無視する。
function capturePointer(ev) {
  try { svg.setPointerCapture(ev.pointerId); } catch (e) { /* no-op */ }
}

function pinchState() {
  const [a, b] = [...activePointers.values()];
  return { d: Math.hypot(b.x - a.x, b.y - a.y) || 1,
           cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
}

// 2本指の移動に合わせて、中点直下のキャンバス座標を保ったままズーム・パンする
function handlePinch() {
  const np = pinchState();
  const rect = svg.getBoundingClientRect();
  const mx = viewBox.x + (np.cx - rect.left) / rect.width * viewBox.w;
  const my = viewBox.y + (np.cy - rect.top) / rect.height * viewBox.h;
  let factor = pinch.d / np.d; // 指を広げる→拡大（viewBox縮小）
  factor = Math.min(8000 / viewBox.w, Math.max(300 / viewBox.w, factor));
  viewBox.w *= factor;
  viewBox.h *= factor;
  viewBox.x = mx - (np.cx - rect.left) / rect.width * viewBox.w;
  viewBox.y = my - (np.cy - rect.top) / rect.height * viewBox.h;
  applyViewBox();
  pinch = np;
}

svg.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;

  // 2本目の指が着いたらピンチズームへ移行（進行中のドラッグは打ち切る）
  activePointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (activePointers.size === 2) {
    if (drag && drag.kind === "node") {
      const g = layerNodes.querySelector(`g[data-id="${drag.id}"]`);
      if (g) g.classList.remove("dragging");
      if (drag.moved) markDirty();
    }
    drag = null;
    pinch = pinchState();
    return;
  }
  if (activePointers.size > 2) return;

  const nodeG = ev.target.closest("g.node");
  const linkG = ev.target.closest("g.link");
  const p = clientToCanvas(ev);

  if (nodeG) {
    const id = nodeG.dataset.id;
    if (mode === "delete") { removeNode(id); return; }
    if (mode === "connect") {
      if (!connectFrom) {
        connectFrom = id;
      } else {
        addLink(connectFrom, id);
        connectFrom = null;
      }
      renderAll();
      return;
    }
    // 選択モード: ドラッグ開始（クリック確定は pointerup で判定）
    const n = nodeById(id);
    drag = { kind: "node", id, dx: p.x - n.x, dy: p.y - n.y, moved: false };
    nodeG.classList.add("dragging");
    capturePointer(ev);
    return;
  }

  if (linkG) {
    const id = linkG.dataset.id;
    if (mode === "delete") { removeLink(id); return; }
    if (mode === "select") {
      select({ kind: "link", id });
      if (isMobile()) openDrawer("props");
      renderAll();
    }
    return;
  }

  // 背景: パン開始。接続途中ならキャンセル、選択も解除。
  if (connectFrom) { connectFrom = null; renderAll(); }
  if (selection) { select(null); renderAll(); }
  drag = { kind: "pan", sx: ev.clientX, sy: ev.clientY, vx: viewBox.x, vy: viewBox.y };
  capturePointer(ev);
});

svg.addEventListener("pointermove", (ev) => {
  if (activePointers.has(ev.pointerId)) {
    activePointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  }
  if (pinch && activePointers.size >= 2) { handlePinch(); return; }
  if (!drag) return;
  if (drag.kind === "node") {
    const p = clientToCanvas(ev);
    const n = nodeById(drag.id);
    if (!n) { drag = null; return; }
    const nx = maybeSnap(p.x - drag.dx), ny = maybeSnap(p.y - drag.dy);
    if (nx !== n.x || ny !== n.y) drag.moved = true;
    n.x = nx; n.y = ny;
    updateNodePosition(n);
  } else {
    const scale = viewBox.w / svg.clientWidth;
    viewBox.x = drag.vx - (ev.clientX - drag.sx) * scale;
    viewBox.y = drag.vy - (ev.clientY - drag.sy) * scale;
    applyViewBox();
  }
});

svg.addEventListener("pointerup", onPointerEnd);
svg.addEventListener("pointercancel", onPointerEnd);

function onPointerEnd(ev) {
  activePointers.delete(ev.pointerId);
  if (activePointers.size < 2) pinch = null;
  if (!drag) return;
  if (drag.kind === "node") {
    const g = layerNodes.querySelector(`g[data-id="${drag.id}"]`);
    if (g) g.classList.remove("dragging");
    if (drag.moved) {
      markDirty();
    } else {
      // 動かしていなければクリック＝選択。スマホでは編集ドロワーを開く
      select({ kind: "node", id: drag.id });
      if (isMobile()) openDrawer("props");
    }
    renderAll();
  }
  drag = null;
}

// ホイールでカーソル位置を中心にズーム
svg.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const factor = ev.deltaY > 0 ? 1.1 : 1 / 1.1;
  zoomAt(clientToCanvas(ev), factor);
}, { passive: false });

function zoomAt(center, factor) {
  const nw = Math.min(8000, Math.max(300, viewBox.w * factor));
  const ratio = nw / viewBox.w;
  viewBox.x = center.x - (center.x - viewBox.x) * ratio;
  viewBox.y = center.y - (center.y - viewBox.y) * ratio;
  viewBox.w = nw;
  viewBox.h = viewBox.h * ratio;
  applyViewBox();
}

// 全ノードが収まるように表示を合わせる
function zoomToFit() {
  const wrap = $("canvas-wrap");
  const aspect = wrap.clientHeight / Math.max(1, wrap.clientWidth);
  if (current.nodes.length === 0) {
    viewBox = { x: 0, y: 0, w: 1200, h: 1200 * aspect };
  } else {
    const xs = current.nodes.map(n => n.x), ys = current.nodes.map(n => n.y);
    const pad = 120;
    const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
    let w = maxX - minX, h = maxY - minY;
    if (h / w < aspect) h = w * aspect; else w = h / aspect;
    viewBox = { x: (minX + maxX) / 2 - w / 2, y: (minY + maxY) / 2 - h / 2, w, h };
  }
  applyViewBox();
}

// ---------------- モード切替・キーボード ----------------
function setMode(m) {
  mode = m;
  connectFrom = null;
  for (const b of document.querySelectorAll(".mode-btn")) {
    b.classList.toggle("is-active", b.dataset.mode === m);
  }
  renderAll();
}

document.addEventListener("keydown", (ev) => {
  const tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (ev.key === "Escape") {
    connectFrom = null;
    select(null);
    closeDrawers();
    renderAll();
  } else if (ev.key === "Delete" || ev.key === "Backspace") {
    if (selection && selection.kind === "node") removeNode(selection.id);
    else if (selection && selection.kind === "link") removeLink(selection.id);
  } else if (ev.key === "v" || ev.key === "V") setMode("select");
  else if (ev.key === "c" || ev.key === "C") setMode("connect");
  else if (ev.key === "d" || ev.key === "D") setMode("delete");
});

// ---------------- 図面管理（一覧・新規・複製・削除） ----------------
function renderDiagramSelect() {
  const sel = $("diagram-select");
  sel.textContent = "";
  for (const d of diagrams) {
    const o = document.createElement("option");
    o.value = d.id;
    o.textContent = d.name;
    sel.appendChild(o);
  }
  sel.value = current.id;
  $("diagram-name").value = current.name;
}

function switchDiagram(id) {
  const d = diagrams.find(x => x.id === id);
  if (!d) return;
  current = d;
  select(null);
  connectFrom = null;
  localStorage.setItem(CURRENT_KEY, current.id);
  renderDiagramSelect();
  zoomToFit();
  renderAll();
}

function bindDiagramControls() {
  $("diagram-select").addEventListener("change", (ev) => switchDiagram(ev.target.value));

  $("diagram-name").addEventListener("input", (ev) => {
    current.name = ev.target.value || "無題の構成図";
    markDirty();
    // セレクトの表示名も追従させる
    const opt = $("diagram-select").querySelector(`option[value="${current.id}"]`);
    if (opt) opt.textContent = current.name;
  });

  $("btn-new").addEventListener("click", () => {
    const d = newDiagram();
    diagrams.push(d);
    switchDiagram(d.id);
    markDirty();
  });

  $("btn-duplicate").addEventListener("click", () => {
    const copy = JSON.parse(JSON.stringify(current));
    copy.id = uid("dg");
    copy.name = current.name + " のコピー";
    diagrams.push(copy);
    switchDiagram(copy.id);
    markDirty();
  });

  $("btn-delete-diagram").addEventListener("click", () => {
    if (!confirm(`図面「${current.name}」を削除します。よろしいですか？`)) return;
    diagrams = diagrams.filter(d => d.id !== current.id);
    if (diagrams.length === 0) diagrams = [newDiagram()];
    current = diagrams[0];
    switchDiagram(current.id);
    persist();
  });

  // JSON 書き出し（このデータ構造のまま将来のDB保存APIへ送る想定）
  $("btn-export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(current, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${current.name || "diagram"}.nettopo.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  $("btn-import").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (!file) return;
    try {
      const d = JSON.parse(await file.text());
      if (!d || !Array.isArray(d.nodes) || !Array.isArray(d.links)) {
        throw new Error("形式が違います");
      }
      d.id = uid("dg"); // 既存図面との衝突を避けて別図面として取り込む
      d.name = String(d.name || file.name.replace(/\.nettopo\.json$|\.json$/i, ""));
      diagrams.push(d);
      switchDiagram(d.id);
      markDirty();
    } catch (e) {
      alert("読み込みに失敗しました: " + e.message);
    }
  });
}

// ---------------- パレット・表示コントロール ----------------
function bindPalette() {
  const list = $("palette-list");
  for (const t of DEVICE_TYPES) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "palette-item";
    b.title = `${t.name} を追加`;
    const icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = t.icon;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = t.name;
    b.append(icon, name);
    b.addEventListener("click", () => {
      addNode(t.id);
      if (isMobile()) closeDrawers(); // 追加したノードが見えるように閉じる
    });
    list.appendChild(b);
  }
}

function bindViewControls() {
  for (const b of document.querySelectorAll(".mode-btn")) {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  }
  const center = () => ({ x: viewBox.x + viewBox.w / 2, y: viewBox.y + viewBox.h / 2 });
  $("zoom-in").addEventListener("click", () => zoomAt(center(), 1 / 1.2));
  $("zoom-out").addEventListener("click", () => zoomAt(center(), 1.2));
  $("zoom-reset").addEventListener("click", zoomToFit);
  window.addEventListener("resize", applyViewBox);
}

// ---------------- 起動 ----------------
function init() {
  loadAll();
  bindPalette();
  bindProps();
  bindDiagramControls();
  bindViewControls();
  bindDrawers();
  renderDiagramSelect();
  zoomToFit();
  renderAll();
  setSaveStatus(false);
}

init();
