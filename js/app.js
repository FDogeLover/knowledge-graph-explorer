(function () {
  "use strict";

  var G = (window.__KG = {
    data: null,
    nodesById: {},
    nodeIndex: [],
    catById: {},
    selectedKey: null,
    hub: null,
    world: null,
    zoom: 1,
    pan: { x: 0, y: 0 },
    physics: null,
    raf: null,
    searchText: {},
    searchOrder: [],
    hiddenCats: {},
    hintFaded: false
  });

  var stage = document.getElementById("stage");
  var detailCard = document.getElementById("detailCard");
  var searchInput = document.getElementById("searchInput");
  var searchPanel = document.getElementById("searchPanel");
  var searchWrap = document.getElementById("searchWrap");
  var toastEl = document.getElementById("toast");
  var hintEl = document.getElementById("hint");
  var legendEl = document.getElementById("legend");

  function $(id) { return document.getElementById(id); }

  /* ================= 工具 ================= */
  function showToast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.classList.remove("show");
    void toastEl.offsetWidth;
    toastEl.classList.add("show");
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ===== 全局拖拽锁：同一时间只允许一种窗口级拖动，且可靠清理 ===== */
  var dragListeners = null;
  function clearDrag() {
    if (!dragListeners) return;
    var d = dragListeners;
    dragListeners = null;
    window.removeEventListener("mousemove", d.move);
    window.removeEventListener("mouseup", d.up);
    window.removeEventListener("pointerup", d.up);
    window.removeEventListener("pointercancel", d.abort);
    window.removeEventListener("blur", d.abort);
  }
  /* move: 移动回调；fin: 结束时回调（无论正常松手还是被抢占都调用） */
  function startDrag(move, fin) {
    clearDrag(); // 抢占：清掉上一个未完结的拖动（修复串扰）
    var up = function () { clearDrag(); if (fin) fin(); };
    var abort = function () { clearDrag(); };
    dragListeners = { move: move, up: up, abort: abort };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", abort);
    window.addEventListener("blur", abort); // 失焦兜底，避免"卡住"
  }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

  /* ================= 数据加载 ================= */
  function loadData(cb) {
    fetch("data/humans.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (json) { G.data = json; cb(); })
      .catch(function (err) {
        showToast("加载数据失败：" + (err && err.message ? err.message : err));
      });
  }

  function buildSearchIndex() {
    G.searchText = {};
    G.searchOrder = [];
    Object.keys(G.nodesById).forEach(function (key) {
      var n = G.nodesById[key];
      G.searchText[key] = (n.label + " " + n.desc + " " + (n.qa || []).map(function (q) { return q.q + " " + q.a; }).join(" ")).toLowerCase();
      G.searchOrder.push(key);
    });
    // 推荐排序：核心（度中心性高）在前
    G.searchOrder.sort(function (a, b) {
      return G.nodesById[b].edges.length - G.nodesById[a].edges.length ||
        (a < b ? -1 : 1);
    });
  }

  /* ================= BFS 定层级中心 ================= */
  function computeHub() {
    var keys = Object.keys(G.nodesById), n = keys.length;
    var best = keys[0], bestEcc = Infinity;
    for (var i = 0; i < n; i++) {
      var ecc = eccentricity(keys[i]);
      if (ecc < bestEcc) { bestEcc = ecc; best = keys[i]; }
    }
    G.hub = best;
    // 每个节点层等级（到 hub 的距离，超出置 3）
    var d = bfsDist(best);
    keys.forEach(function (k) { G.nodesById[k].level = Math.min(d[k], 3); });
  }
  function eccentricity(start) { return Math.max.apply(null, Object.values(bfsDist(start))); }
  function bfsDist(start) {
    var q = [start], d = {}; d[start] = 0;
    while (q.length) {
      var cur = q.shift();
      G.nodesById[cur].edges.forEach(function (e) {
        var nb = e.from === cur ? e.to : e.from;
        if (!(nb in d)) { d[nb] = d[cur] + 1; q.push(nb); }
      });
    }
    return d;
  }

  /* ================= 布局建立 ================= */
  function setup() {
    G.data.categories.forEach(function (c) { G.catById[c.id] = c; });
    G.data.nodes.forEach(function (n) {
      G.nodesById[n.id] = {
        idx: n.id, label: n.label, desc: n.desc,
        cat: G.catById[n.category] || {},
        qa: n.qa || [], edges: [], pos: null, vel: { x: 0, y: 0 },
        el: null, level: 3, hidden: false, focus: false, dim: false
      };
    });
    G.data.edges.forEach(function (e) {
      var a = G.nodesById[e.from], b = G.nodesById[e.to];
      var edge = {
        from: a.idx, to: b.idx, label: e.label,
        colorA: a.cat.color, colorB: b.cat.color,
        el: null, labelEl: null, active: false, hover: false, hidden: false
      };
      a.edges.push(edge); b.edges.push(edge);
    });

    computeHub();
    buildSearchIndex();
    loadRecent();
    initWorld();
    initPhysics();
    renderLegend();
    seedStars();
    initPalette();
    bindEvents();

    // 载入后聚焦枢纽节点，并自适应视图让首屏全部节点可见
    focusNodeHub();
    fitView(null);
    startHintFade();
  }

  /* ================= 画布 ================= */
  function initWorld() {
    var vp = document.createElement("div");
    vp.id = "viewport";
    vp.style.cssText = "position:absolute;inset:0;transform-origin:0 0;will-change:transform;";
    var edgeEl = document.createElement("div"); edgeEl.id = "edgeLayer";
    edgeEl.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;overflow:visible;";
    // 独立标签层：文字始终水平（不随连线旋转），位于连线之上、节点之下
    var labelEl = document.createElement("div"); labelEl.id = "edgeLabelLayer";
    labelEl.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;overflow:visible;";
    var nodeEl = document.createElement("div"); nodeEl.id = "nodeLayer";
    nodeEl.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;overflow:visible;";
    vp.appendChild(edgeEl); vp.appendChild(labelEl); vp.appendChild(nodeEl);
    stage.appendChild(vp);
    G.world = vp;
  }
  function applyTransform() {
    G.world.style.transform = "translate(" + G.pan.x + "px," + G.pan.y + "px) scale(" + G.zoom + ")";
  }
  function zoomAt(cx, cy, factor) {
    var newZoom = Math.min(3, Math.max(0.4, G.zoom * factor));
    var k = newZoom / G.zoom;
    G.pan.x = cx - (cx - G.pan.x) * k;
    G.pan.y = cy - (cy - G.pan.y) * k;
    G.zoom = newZoom;
    applyTransform();
    refreshEdgeLabels();
  }
  function resetViewTo(nodeKey) {
    var n = G.nodesById[nodeKey];
    if (!n) { fitView(null); return; }
    G.zoom = 1.55;
    var cx = stage.clientWidth / 2, cy = stage.clientHeight / 2;
    G.pan.x = cx - n.pos.x * G.zoom;
    G.pan.y = cy - n.pos.y * G.zoom;
    applyTransform();
    refreshEdgeLabels();
  }
  /* 自适应视角：把给定关键节点（缺省=全部可见）的首屏装进画布 */
  function fitView(keys) {
    var list = keys || Object.keys(G.nodesById).filter(function (k) { return nodeVisible(G.nodesById[k]); });
    if (!list.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    list.forEach(function (k) {
      var p = G.nodesById[k].pos;
      if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
    });
    var bw = (maxX - minX) + 180, bh = (maxY - minY) + 180;
    var W = stage.clientWidth || 800, H = stage.clientHeight || 600;
    var z = Math.min(W / bw, H / bh, 2.4);
    G.zoom = Math.max(0.5, z);
    var cx = stage.clientWidth / 2 || 0, cy = stage.clientHeight / 2 || 0;
    var bx = (minX + maxX) / 2, by = (minY + maxY) / 2;
    G.pan.x = cx - bx * G.zoom;
    G.pan.y = cy - by * G.zoom;
    applyTransform();
    refreshEdgeLabels();
  }

  /* ================= 物理（放大后的受力） ================= */
  function initPhysics() {
    var W = stage.clientWidth || 1000, H = stage.clientHeight || 680;
    var R = Math.min(W, H) * 0.42;
    var cx = W / 2, cy = H / 2;
    var keys = Object.keys(G.nodesById);
    keys.forEach(function (key, i) {
      var ang = (i / keys.length) * Math.PI * 2;
      G.nodesById[key].pos = { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang) };
    });
    G.physics = { rep: 5200, spring: 0.055, rest: 195, center: 0.01, damp: 0.85, maxV: 16 };
  }
  function stepPhysics() {
    var P = G.physics, keys = Object.keys(G.nodesById), n = keys.length, i, j;
    for (i = 0; i < n; i++) {
      var a = G.nodesById[keys[i]];
      for (j = i + 1; j < n; j++) {
        var b = G.nodesById[keys[j]];
        var dx = b.pos.x - a.pos.x, dy = b.pos.y - a.pos.y;
        var d = Math.hypot(dx, dy) + 1;
        var f = P.rep / (d * d);
        if (d > 300) f = 0;
        var fx = (dx / d) * f, fy = (dy / d) * f;
        a.vel.x -= fx; a.vel.y -= fy; b.vel.x += fx; b.vel.y += fy;
      }
    }
    G.data.edges.forEach(function (e) {
      var a = G.nodesById[e.from], b = G.nodesById[e.to];
      var dx = b.pos.x - a.pos.x, dy = b.pos.y - a.pos.y;
      var d = Math.hypot(dx, dy) + 1;
      var f = (d - P.rest) * P.spring;
      var fx = (dx / d) * f, fy = (dy / d) * f;
      a.vel.x += fx; a.vel.y += fy; b.vel.x -= fx; b.vel.y -= fy;
    });
    var cx = stage.clientWidth / 2 || 0, cy = stage.clientHeight / 2 || 0, speed = 0;
    keys.forEach(function (key) {
      var n = G.nodesById[key];
      n.vel.x += (cx - n.pos.x) * P.center;
      n.vel.y += (cy - n.pos.y) * P.center;
      n.vel.x *= P.damp; n.vel.y *= P.damp;
      var v = Math.hypot(n.vel.x, n.vel.y);
      if (v > P.maxV) { n.vel.x = (n.vel.x / v) * P.maxV; n.vel.y = (n.vel.y / v) * P.maxV; }
      n.pos.x += n.vel.x; n.pos.y += n.vel.y; speed += v;
    });
    return speed / n < 0.06;
  }

  /* ================= 渲染 ================= */
  function ensureNodeEl(key) {
    var n = G.nodesById[key];
    if (n.el) return n.el;
    var el = document.createElement("div");
    el.className = "node lv" + n.level;
    el.style.setProperty("--nc", n.cat.color);
    el.style.borderColor = n.cat.color;
    el.style.color = n.cat.color;
    var span = document.createElement("span");
    span.className = "node-text";
    span.textContent = n.label;
    el.appendChild(span);
    el.addEventListener("mousedown", function (ev) { onNodeDown(ev, key); });
    el.addEventListener("click", function (ev) { ev.stopPropagation(); focusNode(key); });
    el.addEventListener("dblclick", function (ev) { ev.stopPropagation(); dblDeep(key); });
    el.addEventListener("mouseenter", function () { if (!G.draggingNode) hoverNode(key); });
    el.addEventListener("mouseleave", function () { unhoverNode(); });
    document.getElementById("nodeLayer").appendChild(el);
    n.el = el;
    return el;
  }
  function ensureEdge(edge) {
    if (edge.el) return edge;
    var layer = document.getElementById("edgeLayer");
    var el = document.createElement("div"); el.className = "edge";
    var line = document.createElement("div"); line.className = "edge-line";
    line.style.background = "linear-gradient(90deg," + edge.colorA + "," + edge.colorB + ")";
    line.style.boxShadow = "0 0 6px " + edge.colorA + "44";
    el.appendChild(line);
    layer.appendChild(el);
    // 标签放入独立水平层（不再嵌入旋转容器）
    var lab = document.createElement("div");
    lab.className = "edge-label";
    lab.textContent = edge.label;
    lab.dataset.a = edge.colorA; lab.dataset.b = edge.colorB;
    document.getElementById("edgeLabelLayer").appendChild(lab);
    edge.el = el; edge.labelEl = lab;
    return el;
  }
  function nodeVisible(n) {
    return !n.hidden && !G.hiddenCats[n.cat.id];
  }
  function applyHidden() {
    var keys = Object.keys(G.nodesById);
    keys.forEach(function (k) {
      var n = G.nodesById[k];
      n.hidden = !!G.hiddenCats[n.cat.id];
      if (n.el) n.el.style.display = n.hidden ? "none" : "";
      updateNodeRender(k);
    });
    G.data.edges.forEach(function (e) {
      var edge = findEdge(e);
      if (!edge || !edge.el) return;
      var a = G.nodesById[e.from], b = G.nodesById[e.to];
      edge.hidden = !nodeVisible(a) || !nodeVisible(b);
      edge.el.style.display = edge.hidden ? "none" : "";
    });
  }
  function updateNodeRender(key) {
    var n = G.nodesById[key], el = n.el;
    if (!el || n.hidden) return;
    el.style.left = n.pos.x + "px";
    el.style.top = n.pos.y + "px";
    el.style.color = n.cat.color;
  }
  /* 关系标签密度控制：交互/聚焦边恒显；否则仅在高缩放时显示浅层级主网络 */
  function showLinkLabel(edge, a, b) {
    if (edge.hidden) return false;
    if (edge.hover || edge.active) return true;
    if (!G.selectedKey) {
      return G.zoom >= 1.18 && a.level <= 1 && b.level <= 1;
    }
    return false; // 有聚焦时只保留关联边，其余降噪
  }
  function updateEdgeRender(edge) {
    if (!edge.el) return;
    var a = G.nodesById[edge.from], b = G.nodesById[edge.to];
    if (edge.hidden) {
      edge.el.style.display = "none";
      if (edge.labelEl) edge.labelEl.style.display = "none";
      return;
    }
    edge.el.style.display = "";
    var dx = b.pos.x - a.pos.x, dy = b.pos.y - a.pos.y;
    var len = Math.hypot(dx, dy), ang = Math.atan2(dy, dx) * 180 / Math.PI;
    edge.el.style.left = a.pos.x + "px";
    edge.el.style.top = a.pos.y + "px";
    edge.el.style.width = len + "px";
    edge.el.style.transform = "rotate(" + ang + "deg)";
    edge.el.style.height = (G.zoom > 1.4 ? 3 : 2) + "px";
    edge.el.classList.toggle("active", !!edge.active);
    edge.el.classList.toggle("hover", !!edge.hover);
    edge.el.classList.toggle("hovering", !!edge.hovering);
    if (edge.labelEl) {
      var show = showLinkLabel(edge, a, b);
      edge.labelEl.style.display = show ? "" : "none";
      if (show) {
        // 中点取景，垂直方向上移一条小间距；文字始终水平
        edge.labelEl.style.left = ((a.pos.x + b.pos.x) / 2) + "px";
        edge.labelEl.style.top = ((a.pos.y + b.pos.y) / 2 - 16 / G.zoom) + "px";
        edge.labelEl.style.borderColor = edge.colorA;
        edge.labelEl.style.color = "#e8edf5";
      }
    }
  }
  function findEdge(e) {
    var a = G.nodesById[e.from];
    return (a.edges || []).find(function (x) { return x.to === e.to; });
  }
  function renderFrame() {
    Object.keys(G.nodesById).forEach(function (key) {
      var n = G.nodesById[key];
      ensureNodeEl(key);
      n.el.classList.toggle("focus", !!n.focus);
      n.el.classList.toggle("dim", !!n.dim && !n.focus);
      updateNodeRender(key);
    });
    G.data.edges.forEach(function (e) {
      var edge = findEdge(e);
      if (!edge) return;
      ensureEdge(edge);
      updateEdgeRender(edge);
    });
    var ll = document.getElementById("edgeLabelLayer");
    if (ll) ll.classList.toggle("zoom-hi", G.zoom >= 1.18);
  }
  function refreshEdgeLabels() { renderFrame(); }

  /* ================= 动画（载入收敛一次后冻结布局） ================= */
  var fittedOnce = false;
  function animate() {
    var busy = false;
    for (var i = 0; i < 3; i++) { if (!stepPhysics()) busy = true; }
    renderFrame();
    if (!busy && !fittedOnce) { fittedOnce = true; fitView(null); } // 收敛后取景当前布局
    if (busy) G.raf = requestAnimationFrame(animate); else G.raf = null;
  }
  function kickPhysics() {
    if (!fittedOnce) { if (!G.raf) G.raf = requestAnimationFrame(animate); }
  }

  /* ================= 聚焦 / 高亮 ================= */
  function neighborOf(a, b) {
    if (a === b) return true;
    return (G.nodesById[a].edges || []).some(function (e) { return e.from === b || e.to === b; });
  }

  /* 依据 selectedKey 重算整体明暗/发光（不重建详情面板） */
  function applyDimFocus() {
    var key = G.selectedKey;
    Object.keys(G.nodesById).forEach(function (k) {
      var n = G.nodesById[k];
      n.focus = !!key && n.idx === key;
      n.dim = key ? (n.focus ? false : !neighborOf(key, k)) : false;
    });
    G.data.edges.forEach(function (e) {
      var eg = findEdge(e);
      if (!eg) return;
      eg.active = !!key && (eg.from === key || eg.to === key);
      eg.hover = false;
    });
  }

  /* 悬停临时聚焦：相关节点 100%，无关降暗，相关连线发光 */
  function hoverNode(key) {
    Object.keys(G.nodesById).forEach(function (k) {
      var n = G.nodesById[k];
      n.dim = n.hidden ? false : !neighborOf(key, k);
      n.focus = false;
    });
    G.data.edges.forEach(function (e) {
      var eg = findEdge(e);
      if (!eg) return;
      var rel = (eg.from === key || eg.to === key);
      eg.hovering = rel;
      eg.active = rel;
      eg.hover = rel;
    });
    renderFrame();
  }
  function unhoverNode() {
    G.data.edges.forEach(function (e) {
      var eg = findEdge(e);
      if (!eg) return;
      eg.hovering = false;
      eg.hover = false;
    });
    applyDimFocus();
    renderFrame();
  }

  function trackRecent(key) {
    G.recent = G.recent || [];
    G.recent = [key].concat(G.recent.filter(function (k) { return k !== key; })).slice(0, 6);
    try { localStorage.setItem("kg_recent", JSON.stringify(G.recent)); } catch (e) { /* 忽略 */ }
  }
  function loadRecent() {
    try { G.recent = JSON.parse(localStorage.getItem("kg_recent") || "[]") || []; }
    catch (e) { G.recent = []; }
  }

  function focusNode(key) {
    G.selectedKey = key;
    applyDimFocus();
    trackRecent(key);
    showDetail(key);
    refreshAI(key);
    renderFrame();
  }
  function clearFocus() {
    if (G.selectedKey) {
      Object.keys(G.nodesById).forEach(function (k) { G.nodesById[k].focus = false; G.nodesById[k].dim = false; });
      G.data.edges.forEach(function (e) { var eg = findEdge(e); if (eg) { eg.active = false; eg.hover = false; eg.hovering = false; } });
      G.selectedKey = null;
      hideDetail();
      renderFrame();
    }
  }
  function dblDeep(key) {
    var rect = stage.getBoundingClientRect();
    var n = G.nodesById[key];
    var sx = rect.left + G.pan.x + n.pos.x * G.zoom;
    var sy = rect.top + G.pan.y + n.pos.y * G.zoom;
    zoomAt(sx - rect.left, sy - rect.top, 1.6);
    focusNode(key);
  }

  /* 节点拖拽 + 单击聚焦（不触发整图重排，避免缩成一团） */
  function onNodeDown(e, key) {
    if (e.button !== 0) return;
    e.stopPropagation();
    G.draggingNode = true;
    var n = G.nodesById[key];
    var start = { x: e.clientX, y: e.clientY };
    var orig = { x: n.pos.x, y: n.pos.y };
    var moved = false, dragging = true;
    var move = function (ev) {
      if (!dragging) return;
      moved = true;
      n.pos.x = orig.x + (ev.clientX - start.x) / G.zoom;
      n.pos.y = orig.y + (ev.clientY - start.y) / G.zoom;
      updateNodeRender(key);
      G.data.edges.forEach(function (eg) { var ed = findEdge(eg); if (ed) updateEdgeRender(ed); });
    };
    var fin = function () {
      G.draggingNode = false;
      if (!moved) focusNode(key); // 纯单击＝聚焦
      // 已拖动：保持用户摆放位置，不再重启物理
    };
    startDrag(move, fin); // 抢占式：自动清理上一个未完结拖动（修复串扰）
  }

  /* ================= 详情侧栏 ================= */
  function showDetail(key) {
    var n = G.nodesById[key];
    if (!n) return;
    detailCard.hidden = false;
    detailCard.className = "detail glass show";
    detailCard.innerHTML = "";
    var closeBtn = document.createElement("button");
    closeBtn.className = "detail-close"; closeBtn.textContent = "×";
    closeBtn.onclick = function (ev) { ev.stopPropagation(); clearFocus(); };
    var cat = document.createElement("div");
    cat.className = "detail-cat";
    cat.innerHTML = '<span class="detail-dot" style="background:' + n.cat.color + ';color:' + n.cat.color + ';box-shadow:0 0 10px ' + n.cat.color + '"></span>' + (n.cat.name || "");
    var h = document.createElement("h2"); h.textContent = n.label;
    var lv = document.createElement("div"); lv.className = "detail-level";
    lv.textContent = "层级 " + (n.level + 1) + "/4 · 关联 " + n.edges.length + " 条";
    var d = document.createElement("div"); d.className = "detail-desc"; d.textContent = n.desc;

    detailCard.appendChild(closeBtn);
    detailCard.appendChild(cat);
    detailCard.appendChild(h);
    detailCard.appendChild(lv);
    detailCard.appendChild(d);

    // 关联追问
    var h3 = document.createElement("h3"); h3.textContent = "关联追问";
    detailCard.appendChild(h3);
    var qaList = document.createElement("div"); qaList.className = "qa-list";
    (n.qa.length ? n.qa : [{ q: "暂无预设追问", a: "可点击右侧相关概念继续探索。" }]).forEach(function (qaItem) {
      var box = document.createElement("div"); box.className = "qa";
      var qBtn = document.createElement("button"); qBtn.className = "qa-q";
      qBtn.innerHTML = '<span>' + escapeHtml(qaItem.q) + '</span><span class="qa-caret">›</span>';
      var aWrap = document.createElement("div"); aWrap.className = "qa-a";
      var p = document.createElement("p"); p.textContent = qaItem.a;
      aWrap.appendChild(p);
      qBtn.addEventListener("click", function () { box.classList.toggle("open"); });
      box.appendChild(qBtn); box.appendChild(aWrap);
      qaList.appendChild(box);
    });
    detailCard.appendChild(qaList);

    // 相关概念
    var rel = document.createElement("div"); rel.className = "rel-label"; rel.textContent = "相关概念";
    detailCard.appendChild(rel);
    var chips = document.createElement("div"); chips.className = "rel-chips";
    var seen = {};
    n.edges.forEach(function (e) {
      var nb = e.from === key ? e.to : e.from;
      if (seen[nb]) return; seen[nb] = true;
      var nbNode = G.nodesById[nb];
      var chip = document.createElement("button"); chip.className = "rel-chip";
      chip.style.color = nbNode.cat.color;
      chip.innerHTML = '<span class="rc-dot" style="background:' + nbNode.cat.color + '"></span>' + nbNode.label;
      chip.addEventListener("click", function () { focusNode(nb); });
      chips.appendChild(chip);
    });
    if (!chips.childElementCount) { chips.textContent = "暂无直接关联概念"; chips.style.color = "var(--c-text-dim)"; }
    detailCard.appendChild(chips);

    // 展开知识链
    var chain = document.createElement("button"); chain.className = "chain-btn";
    chain.textContent = "展开这条知识链 →";
    chain.addEventListener("click", function () { dblDeep(key); });
    detailCard.appendChild(chain);
  }
  function hideDetail() {
    if (!detailCard.classList.contains("show")) return;
    detailCard.classList.remove("show");
    detailCard.classList.add("dim");
    setTimeout(function () {
      if (!detailCard.classList.contains("show")) { detailCard.classList.remove("dim"); detailCard.hidden = true; }
    }, 200);
  }

  /* ================= AI 知识向导 ================= */
  function aiEl(id) { return document.getElementById(id); }
  function refreshAI(key) {
    if (key && aiEl("aiTopic")) aiEl("aiTopic").textContent = G.nodesById[key].label;
    renderAISuggest(key);
  }
  function renderAISuggest(key) {
    var box = aiEl("aiSuggest");
    if (!box) return;
    box.innerHTML = "";
    var n = G.nodesById[key];
    if (!n) return;
    var sugg = [];
    n.edges.slice(0, 3).forEach(function (e) {
      var nb = e.from === key ? e.to : e.from;
      sugg.push("它与「" + G.nodesById[nb].label + "」有什么关系？");
    });
    sugg.push("用一句话解释「" + n.label + "」");
    sugg.push("展开这条知识链");
    sugg.forEach(function (s) {
      var b = document.createElement("button"); b.className = "sugg"; b.textContent = s;
      b.addEventListener("click", function () { askAI(s); });
      box.appendChild(b);
    });
  }
  function askAI(question) {
    var chat = aiEl("aiChat");
    chat.hidden = false;
    var you = document.createElement("div"); you.className = "bubble you"; you.textContent = question;
    chat.appendChild(you);

    var key = G.selectedKey;
    var node = key ? G.nodesById[key] : null;
    var label = node ? node.label : "人类简史";
    var reply;

    // 优先命中预设追问的真实答案
    var hit = node && node.qa.reduce(function (best, qa) {
      var s = overlapScore(question, qa.q);
      return s > best.score ? { score: s, ans: qa.a } : best;
    }, { score: 0, ans: null });
    if (hit && hit.score >= 2) {
      reply = hit.ans;
    } else if (question.indexOf("展开") >= 0 || question.indexOf("知识链") >= 0) {
      reply = "好的，帮你把「" + label + "」放大聚焦，可顺着相关概念继续探索。";
    } else if (question.indexOf("一句话") >= 0) {
      reply = "「" + label + "」是《人类简史》" + (node ? node.cat.name : "核心概念") + "板块的关键概念，与其他概念的关联共同构成理解人类文明的坐标系。";
    } else {
      reply = "我对这个问题的回答尚未收录。当前是原型演示、非实时大模型，我只会基于预设知识回答。建议在右侧详情面板查看「" + label + "」的「关联追问」，那里有针对该概念的预设答案。";
    }
    var ai = document.createElement("div"); ai.className = "bubble ai";
    ai.textContent = "（原型提示 · 基于预设知识，非实时模型）" + reply;
    chat.appendChild(ai);
    chat.scrollTop = chat.scrollHeight;
    aiEl("aiInput").value = "";
  }
  /* 估算两段文本的重叠度：统计共同出现的连续双字片段 */
  function overlapScore(a, b) {
    var setB = {}, score = 0;
    for (var i = 0; i < b.length - 1; i++) setB[b.slice(i, i + 2)] = true;
    for (var j = 0; j < a.length - 1; j++) if (setB[a.slice(j, j + 2)]) score++;
    return score;
  }
  function toggleAI(show) {
    var p = aiEl("aiPanel");
    if (typeof show !== "boolean") show = p.hidden;
    if (show) {
      p.hidden = false;
      refreshAI(G.selectedKey || G.hub);
      renderAISuggest(G.selectedKey || G.hub);
    } else { p.hidden = true; }
  }

  /* ================= 图例：过滤器 ================= */
  function renderLegend() {
    legendEl.innerHTML = '<div class="legend-title">知识类型 <span class="lt-reset" id="legendReset">重置</span></div>';
    G.data.categories.forEach(function (c) {
      var count = G.data.nodes.filter(function (n) { return n.category === c.id; }).length;
      var item = document.createElement("div");
      item.className = "legend-item";
      item.dataset.cat = c.id;
      item.innerHTML = '<span class="legend-swatch" style="background:' + c.color + ';color:' + c.color + '"></span>' + c.name + '<span class="legend-count">' + count + '</span>';
      item.addEventListener("click", function () { toggleCat(c.id); });
      legendEl.appendChild(item);
    });
    $("legendReset").addEventListener("click", function (ev) {
      ev.stopPropagation();
      G.hiddenCats = {};
      legendEl.querySelectorAll(".legend-item").forEach(function (it) { it.classList.remove("off"); });
      applyHidden(); onSearch(); // 保持当前视图，不触发重排
    });
  }
  function toggleCat(id) {
    if (G.hiddenCats[id]) delete G.hiddenCats[id];
    else G.hiddenCats[id] = true;
    var item = legendEl.querySelector('.legend-item[data-cat="' + id + '"]');
    if (item) item.classList.toggle("off", !!G.hiddenCats[id]);
    applyHidden();
    onSearch(); // 保持当前视图，不触发物理重排
  }

  /* ================= 搜索（Ctrl+K Palette） ================= */
  function openSearch() {
    searchInput.focus();
    searchInput.select();
  }
  function renderSearchPanel() {
    var term = searchInput.value.trim().toLowerCase();
    var list;
    if (!term) list = G.searchOrder.slice(0, 6); // 推荐
    else list = searchResults(term).slice(0, 8);
    if (!list.length) { searchPanel.hidden = true; return; }
    searchPanel.hidden = false;
    searchPanel.innerHTML = "";
    var sec = document.createElement("div");
    sec.className = "sp-sec";
    sec.textContent = term ? "搜索结果（" + list.length + "）" : "推荐概念";
    searchPanel.appendChild(sec);
    list.forEach(function (k) {
      var no = G.nodesById[k];
      var it = document.createElement("div"); it.className = "sp-item";
      it.innerHTML = '<span class="sp-dot" style="background:' + no.cat.color + ';color:' + no.cat.color + '"></span>' + no.label + '<span class="sp-tag">' + (no.cat.name || "") + '</span>';
      it.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
        searchInput.value = no.label;
        searchPanel.hidden = true;
        focusNode(k);
        matchesCache = null;
      });
      searchPanel.appendChild(it);
    });
  }
  /* 标签加权召回：先取标签/标题命中，无标签命中才退回到描述/追问全文 */
  function searchResults(term) {
    var label = [], others = [];
    G.searchOrder.forEach(function (k) {
      if (G.nodesById[k].label.toLowerCase().indexOf(term) >= 0) label.push(k);
      else if (G.searchText[k].indexOf(term) >= 0) others.push(k);
    });
    return label.length ? label : others;
  }
  var matchesCache = null;
  function currentMatches() {
    var term = searchInput.value.trim().toLowerCase();
    if (!term) return [];
    if (matchesCache && matchesCache.term === term) return matchesCache.list;
    var list = searchResults(term);
    matchesCache = { term: term, list: list };
    return list;
  }
  function onSearch() {
    var term = searchInput.value.trim().toLowerCase();
    renderSearchPanel();
    if (!term) {
      markMatchesVisible([]);
      if (G.selectedKey) { focusNode(G.selectedKey); }
      return;
    }
    var list = currentMatches();
    if (!list.length) { markMatchesVisible([]); showToast("未找到匹配概念"); return; }
    markMatchesVisible(list);
    focusNode(list[0]);
  }
  function markMatchesVisible(list) {
    Object.keys(G.nodesById).forEach(function (k) {
      var n = G.nodesById[k];
      n.dim = list.length ? list.indexOf(k) < 0 : false;
      n.focus = false;
    });
    G.data.edges.forEach(function (e) {
      var eg = findEdge(e);
      if (!eg) return;
      var inA = list.indexOf(eg.from) >= 0, inB = list.indexOf(eg.to) >= 0;
      eg.active = list.length ? (inA || inB) : false;
      eg.hover = false;
    });
    renderFrame();
  }

  /* ================= 交互 ================= */
  function bindEvents() {
    stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      var rect = stage.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.12 : 0.9);
    }, { passive: false });

    // 画布平移：空白处（stage/viewport/节点层空隙/连线）均可拖动
    stage.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      // 节点由 onNodeDown 单独处理（也已 stopPropagation），这里再兜底排除
      if (e.target && e.target.closest && e.target.closest(".node")) return;
      var sx = e.clientX, sy = e.clientY;
      var from = { x: G.pan.x, y: G.pan.y };
      var move = function (ev) {
        G.pan.x = from.x + (ev.clientX - sx);
        G.pan.y = from.y + (ev.clientY - sy);
        applyTransform();
      };
      startDrag(move, null); // 独立拖拽锁，占用则不冲突
    });

    // 触屏：单指平移 + 双指缩放
    var lastTouch = null, pinch = null;
    function tdist(a, b) { return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
    stage.addEventListener("touchstart", function (e) {
      if (e.touches.length === 2) {
        pinch = { d0: tdist(e.touches[0], e.touches[1]), z0: G.zoom };
        lastTouch = null;
      } else if (e.touches.length === 1) {
        pinch = null;
        lastTouch = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      }
    }, { passive: true });
    stage.addEventListener("touchmove", function (e) {
      e.preventDefault();
      if (e.touches.length === 2 && pinch) {
        var d = tdist(e.touches[0], e.touches[1]);
        if (d > 0) {
          var rect = stage.getBoundingClientRect();
          var mx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
          var my = (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
          var factor = (pinch.z0 * (d / pinch.d0)) / G.zoom;
          pinch.z0 = G.zoom; pinch.d0 = d;
          zoomAt(mx, my, factor);
        }
      } else if (e.touches.length === 1 && lastTouch) {
        var t = e.touches[0];
        G.pan.x += t.clientX - lastTouch.x; G.pan.y += t.clientY - lastTouch.y;
        lastTouch = { x: t.clientX, y: t.clientY };
        applyTransform();
      }
    }, { passive: false });
    stage.addEventListener("touchend", function () { lastTouch = null; pinch = null; }, { passive: true });

    // 搜索
    searchInput.addEventListener("input", onSearch);
    searchInput.addEventListener("focus", function () { renderSearchPanel(); });
    searchInput.addEventListener("blur", function () { setTimeout(function () { searchPanel.hidden = true; }, 150); });
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { searchInput.value = ""; searchPanel.hidden = true; onSearch(); searchInput.blur(); }
      if (e.key === "Enter") { var m = currentMatches(); if (m.length) { searchPanel.hidden = true; focusNode(m[0]); } }
      if (e.key === "ArrowDown") {
        var items = searchPanel.querySelectorAll(".sp-item");
        if (items.length) { e.preventDefault(); items[0].dispatchEvent(new MouseEvent("mousedown")); }
      }
    });
    $("searchWrap") && $("searchWrap").addEventListener("mousedown", function (e) {
      if (e.target.closest(".sp-item")) return;
      openSearch();
    });

    // 快捷键
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
      else if (e.key === "/") { e.preventDefault(); openPalette(); }
      else if (e.key === "Escape") {
        if (!paletteEl.hidden) closePalette();
        else if (!searchPanel.hidden) searchPanel.hidden = true;
      }
      else if (e.key === "?") { e.preventDefault(); toggleHint(true); }
    });

    // 分享
    $("shareBtn").addEventListener("click", captureShare);
    // 导航
    document.querySelector('[data-topic]').addEventListener("click", function () { resetPan(); showToast("当前主题：《人类简史》"); });
    // AI
    $("aiFab").addEventListener("click", function () { toggleAI(); });
    $("aiClose").addEventListener("click", function () { toggleAI(false); });
    $("aiSend").addEventListener("click", function () {
      var q = $("aiInput").value.trim();
      if (q) askAI(q);
    });
    $("aiInput").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var q = $("aiInput").value.trim(); if (q) askAI(q); }
    });
    $("addTopic").addEventListener("click", function () { showToast("添加主题功能规划中"); });
  }

  function resetPan() {
    if (!Object.keys(G.nodesById).length) return;
    fitView(null); // 复位：让全部可见节点装进画布
  }
  function focusNodeHub() {
    var k = G.hub || Object.keys(G.nodesById)[0];
    focusNode(k);
    return k;
  }

  /* ================= 全屏命令面板 ================= */
  var paletteEl = $("palette"), paletteInput = $("paletteInput"), paletteResults = $("paletteResults");
  var paletteSel = -1;

  function openPalette() {
    paletteEl.hidden = false;
    paletteInput.value = "";
    paletteSel = -1;
    renderPalette();
    setTimeout(function () { paletteInput.focus(); }, 30);
  }
  function closePalette() {
    paletteEl.hidden = true;
    if (paletteInput.value) { paletteInput.value = ""; onSearch(); }
  }
  function renderPalette() {
    var term = paletteInput.value.trim().toLowerCase();
    paletteResults.innerHTML = "";
    var frag = document.createDocumentFragment();
    var items = [];

    var addSection = function (title, keys) {
      if (!keys.length) return;
      var sec = document.createElement("div"); sec.className = "pr-sec"; sec.textContent = title;
      frag.appendChild(sec);
      keys.forEach(function (k) { items.push(k); });
    };

    if (!term) {
      var recent = (G.recent || []).filter(function (k) { return G.nodesById[k]; });
      addSection("最近探索", recent);
      addSection("推荐", G.searchOrder.slice(0, 6).filter(function (k) { return recent.indexOf(k) < 0; }));
    } else {
      addSection("搜索结果", searchResults(term).slice(0, 8));
    }

    items.forEach(function (k, i) {
      var no = G.nodesById[k];
      var it = document.createElement("div"); it.className = "pr-item" + (i === paletteSel ? " sel" : "");
      it.dataset.k = k; it.dataset.i = i;
      it.innerHTML = '<span class="pr-dot" style="background:' + no.cat.color + ';color:' + no.cat.color + '"></span>' + no.label +
        '<span class="pr-arrow">→</span>';
      it.addEventListener("mouseenter", function () { setPaletteSel(i); });
      it.addEventListener("mousedown", function (ev) { ev.preventDefault(); choosePalette(k); });
      frag.appendChild(it);
    });

    if (!items.length) {
      var empty = document.createElement("div"); empty.className = "pr-empty";
      empty.textContent = term ? "未找到「" + term + "」相关的概念" : "";
      frag.appendChild(empty);
    }
    paletteResults.appendChild(frag);
  }
  function setPaletteSel(i) {
    paletteSel = i;
    var its = paletteResults.querySelectorAll(".pr-item");
    its.forEach(function (it, idx) { it.classList.toggle("sel", idx === i); });
    if (its[i]) its[i].scrollIntoView({ block: "nearest" });
  }
  function choosePalette(k) {
    closePalette();
    focusNode(k);
  }
  function initPalette() {
    paletteEl.querySelectorAll("[data-pclose]").forEach(function (b) {
      b.addEventListener("click", closePalette);
    });
    paletteInput.addEventListener("input", function () { paletteSel = -1; renderPalette(); });
    paletteInput.addEventListener("keydown", function (e) {
      var items = paletteResults.querySelectorAll(".pr-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPaletteSel(items.length ? (paletteSel + 1) % items.length : -1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (items.length) setPaletteSel(paletteSel <= 0 ? items.length - 1 : paletteSel - 1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (items[paletteSel]) choosePalette(items[paletteSel].dataset.k);
      }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    });
  }
  function startHintFade() {
    setTimeout(toggleHint, 6000, false);
  }
  function toggleHint(force) {
    var show = typeof force === "boolean" ? force : !hintEl.classList.contains("fade");
    if (!show) {
      hintEl.classList.add("fade");
      setTimeout(function () { if (hintEl.classList.contains("fade")) hintEl.hidden = true; }, 600);
    } else {
      hintEl.hidden = false;
      hintEl.classList.remove("fade");
      setTimeout(function () { toggleHint(false); }, 6000);
    }
  }

  /* ================= 星尘 ================= */
  function seedStars() {
    var wrap = document.querySelector(".bg-stars");
    if (!wrap) return;
    var frag = document.createDocumentFragment();
    for (var i = 0; i < 60; i++) {
      var s = document.createElement("span");
      var size = 1 + Math.random() * 1.6;
      var x = Math.random() * 100, y = Math.random() * 100;
      s.style.cssText = "position:absolute;left:" + x + "%;top:" + y + "%;width:" + size + "px;height:" + size + "px;border-radius:50%;background:#fff;opacity:" + (0.15 + Math.random() * 0.4) + ";box-shadow:0 0 4px #fff;animation:twinkle " + (3 + Math.random() * 5) + "s ease-in-out " + (Math.random() * 4) + "s infinite;";
      frag.appendChild(s);
    }
    var st = document.createElement("style");
    st.textContent = "@keyframes twinkle{0%,100%{opacity:.15}50%{opacity:.5}}";
    document.head.appendChild(st);
    wrap.appendChild(frag);
  }

  /* ================= 分享截图（纯 SVG 生成，规避 canvas 污染） ================= */
  function captureShare() {
    showToast("正在生成分享图…");
    var svgText = buildSnapshotSvg();
    try {
      var blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var canvas = $("shotCanvas");
      var img = new Image();
      img.onload = function () {
        try {
          canvas.width = img.width; canvas.height = img.height;
          var ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0);
          var data;
          try { data = canvas.toDataURL("image/png"); }
          catch (e) { URL.revokeObjectURL(url); data = url; } // 仍受限则退回 SVG
          openSnapshot(data, url);
        } catch (err) { URL.revokeObjectURL(url); openSnapshot(url, url); }
      };
      img.onerror = function () { showToast("截图生成失败，请重试"); URL.revokeObjectURL(url); };
      img.src = url;
    } catch (err) { showToast("截图失败：" + (err && err.message ? err.message : err)); }
  }
  function openSnapshot(src, svgUrl) {
    var w = window.open("");
    if (!w) { showToast("浏览器拦截新窗口，请允许弹窗后重试"); return; }
    w.document.title = "知识图谱探索器 · 《人类简史》";
    w.document.body.style.margin = "0";
    w.document.body.style.background = "#0b111b";
    w.document.body.style.fontFamily = "sans-serif";
    var img = w.document.createElement("img");
    img.src = src; img.style.cssText = "display:block;margin:0 auto;max-width:100%;";
    w.document.body.appendChild(img);
    var a = w.document.createElement("a");
    a.href = src; a.download = "知识图谱-screenshot.png";
    a.textContent = "点击下载图片（或右键/长按保存）";
    a.style.cssText = "display:block;text-align:center;padding:12px;color:#fff;font-size:14px;";
    w.document.body.appendChild(a);
    showToast("已生成，可在新页下载图片");
    if (svgUrl && svgUrl !== src) URL.revokeObjectURL(svgUrl);
  }

  function buildSnapshotSvg() {
    var NS = "http://www.w3.org/2000/svg";
    // 计算可见节点的屏幕坐标边界
    var nodes = Object.keys(G.nodesById).filter(function (k) { return nodeVisible(G.nodesById[k]); });
    var margin = 90;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    var items = nodes.map(function (k) {
      var n = G.nodesById[k];
      var sx = G.pan.x + n.pos.x * G.zoom;
      var sy = G.pan.y + n.pos.y * G.zoom;
      if (sx < minX) minX = sx; if (sx > maxX) maxX = sx;
      if (sy < minY) minY = sy; if (sy > maxY) maxY = sy;
      return { n: n, sx: sx, sy: sy };
    });
    if (!items.length) { minX = 0; minY = 0; maxX = 800; maxY = 600; }
    var W = maxX - minX + margin * 2, H = maxY - minY + margin * 2;
    if (W < 900) W = 900; if (H < 640) H = 640;
    var ox = minX - margin, oy = minY - margin;
    var parts = [];
    parts.push('<svg xmlns="' + NS + '" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">');
    parts.push('<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b111b"/><stop offset="1" stop-color="#16243d"/></linearGradient></defs>');
    parts.push('<rect width="' + W + '" height="' + H + '" fill="url(#bg)"/>');
    parts.push('<text x="' + (W / 2) + '" y="34" font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="17" font-weight="700" fill="#e8edf5" text-anchor="middle" letter-spacing="2">知识图谱探索器 · 《人类简史》</text>');

    // 边
    G.data.edges.forEach(function (e) {
      var a = G.nodesById[e.from], b = G.nodesById[e.to];
      if (!nodeVisible(a) || !nodeVisible(b)) return;
      var x1 = a.pos.x * G.zoom + G.pan.x - ox, y1 = a.pos.y * G.zoom + G.pan.y - oy;
      var x2 = b.pos.x * G.zoom + G.pan.x - ox, y2 = b.pos.y * G.zoom + G.pan.y - oy;
      parts.push('<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '" stroke="' + a.cat.color + '" stroke-opacity="0.5" stroke-width="2" stroke-linecap="round"/>');
    });

    // 节点
    var sizes = { "0": 56, "1": 44, "2": 36, "3": 30 };
    var fSizes = { "0": 15, "1": 12.5, "2": 11, "3": 10 };
    items.forEach(function (it) {
      var n = it.n, x = it.sx - ox, y = it.sy - oy;
      var rr = sizes[String(n.level)] || 40, fs = fSizes[String(n.level)] || 11;
      parts.push('<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + rr + '" fill="' + n.cat.color + '" fill-opacity="0.14" stroke="' + n.cat.color + '" stroke-width="2"/>');
      parts.push('<text x="' + x.toFixed(1) + '" y="' + (y + 1).toFixed(1) + '" font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="' + fs + '" font-weight="600" fill="' + n.cat.color + '" text-anchor="middle" dominant-baseline="middle">' + escapeXml(n.label) + '</text>');
    });

    parts.push('<text x="' + (W / 2) + '" y="' + (H - 20) + '" font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="12" fill="#93a0b3" text-anchor="middle">AI 生成的实验性交互原型 · 深度学习 · 供交流</text>');
    parts.push('</svg>');
    return parts.join("");
  }
  function escapeXml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ============ 启动 ============ */
  loadData(function () { if (G.data) setup(); });
})();