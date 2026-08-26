/* 公共脚本：导航高亮 + API 助手 + toast + 任务轮询 */
(function () {
  "use strict";

  // 导航高亮
  var page = (location.pathname.split("/").pop() || "index.html").replace(".html", "");
  document.querySelectorAll(".nav a").forEach(function (a) {
    var href = a.getAttribute("href").replace("/", "").replace(".html", "");
    if (href === page || (page === "index" && href === "")) a.classList.add("active");
  });

  // 移动端导航收折：注入 ☰ 按钮，点击切换 .nav-open（窄屏下 .nav 默认为下拉）
  (function navToggle() {
    var nav = document.querySelector(".nav");
    var tb = document.querySelector(".topbar");
    if (!nav || !tb || tb.querySelector(".nav-toggle")) return;
    var b = document.createElement("button");
    b.type = "button"; b.className = "nav-toggle"; b.setAttribute("aria-label", "打开菜单"); b.textContent = "☰";
    tb.appendChild(b);
    b.onclick = function (e) { e.stopPropagation(); tb.classList.toggle("nav-open"); };
    document.addEventListener("click", function (e) {
      if (!tb.contains(e.target)) tb.classList.remove("nav-open");
    });
  })();

  // API 助手
  // 从错误响应里提取后端 detail（如 400 的引导文案），兜底给 HTTP 状态
  async function errorOf(r) {
    try {
      var j = await r.json();
      return j && j.detail ? j.detail : "HTTP " + r.status;
    } catch (e) { return "HTTP " + r.status; }
  }

  window.api = {
    async get(path) {
      var r = await fetch(path);
      if (!r.ok) throw new Error(await errorOf(r));
      return r.json();
    },
    async post(path, body) {
      var r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      if (!r.ok) throw new Error(await errorOf(r));
      return r.json();
    },
    async del(path) {
      var r = await fetch(path, { method: "DELETE" });
      if (!r.ok) throw new Error(await errorOf(r));
      return r.json();
    },
  };

  // toast
  window.toast = function (msg, isErr) {
    var el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
    el.textContent = msg;
    el.style.background = isErr ? "rgba(140,30,30,0.95)" : "rgba(11,17,27,0.95)";
    el.classList.remove("show");
    void el.offsetWidth;
    el.classList.add("show");
  };

  // 任务轮询：提交后查询直到 done/error
  window.pollTask = async function (taskId, onDone, interval) {
    interval = interval || 800;
    var t = await api.get("/api/tasks/" + taskId);
    var bar = document.getElementById("taskProgress");
    if (bar && t.progress) bar.style.width = Math.round(t.progress * 100) + "%";
    if (t.status === "done") { if (bar) bar.style.width = "100%"; onDone && onDone(t.result); return t; }
    if (t.status === "error") { toast("任务失败：" + (t.error || "未知错误"), true); return t; }
    setTimeout(function () { pollTask(taskId, onDone, interval); }, interval);
  };
})();

/* ===== 自定义下拉组件 =====
   原生 <select>/<option> 展开菜单由操作系统渲染（暗色主题下常为白底白字/白底黑字混搭，可读性差），
   且 CSS 无法强制其背景。此组件自动增强页面内所有 <select>：
   - 隐藏原生控件（保留 options/value 供 JS 读取，现有页面代码零改动）
   - 用 div 渲染深色列表：默认项浅灰、悬浮项深底高亮、选中项蓝色底 + ✓
   - 支持点击/箭头展开、方向键/Enter/Esc、点击外部关闭
   - 漂移校正：页面脚本异步设置 select.value 或动态增删 options 时自动同步按钮文本与列表 */
(function () {
  "use strict";

  function uiSelectBuild(sel) {
    // 防重复包装
    if (sel.parentElement && sel.parentElement.classList.contains("ui-select")) return;

    var box = document.createElement("div");
    box.className = "ui-sel-box";
    var val = document.createElement("span");
    val.className = "val";
    box.appendChild(val);
    var arrow = document.createElement("span");
    arrow.className = "arrow";
    box.appendChild(arrow);
    var list = document.createElement("div");
    list.className = "ui-sel-list";
    var wrap = document.createElement("div");
    wrap.className = "ui-select";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(box);
    wrap.appendChild(list);
    // 基础可访问性
    box.setAttribute("tabindex", "0");
    box.setAttribute("role", "combobox");
    box.setAttribute("aria-expanded", "false");

    function selectedOption() { return sel.options && sel.options[sel.selectedIndex]; }

    function sync() {
      var o = selectedOption();
      val.textContent = o ? o.text : (sel.value || "—");
      val.title = val.textContent;
    }

    function highlight() {
      var i = sel.selectedIndex;
      Array.prototype.forEach.call(list.children, function (it, k) {
        it.classList.toggle("sel", k === i);
      });
    }

    function render() {
      list.innerHTML = "";
      Array.prototype.forEach.call(sel.options || [], function (o, i) {
        var it = document.createElement("div");
        it.className = "ui-sel-item" + (o.selected ? " sel" : "");
        var label = document.createElement("span");
        label.textContent = o.textContent || o.text || "\u00a0";
        var tick = document.createElement("span");
        tick.className = "tick";
        tick.textContent = "\u2713";
        it.appendChild(label);
        it.appendChild(tick);
        it.onclick = function (ev) {
          ev.stopPropagation();
          sel.selectedIndex = i;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
          close();
          sync();
          highlight();
        };
        list.appendChild(it);
      });
      sync();
      highlight();
    }

    function open() {
      list.classList.add("open");
      box.classList.add("open");
      box.setAttribute("aria-expanded", "true");
    }
    function close() {
      list.classList.remove("open");
      box.classList.remove("open");
      box.setAttribute("aria-expanded", "false");
    }
    function toggle() {
      if (list.classList.contains("open")) close(); else open();
    }

    box.onclick = function () { toggle(); };
    // 点击外部关闭
    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target) && list.classList.contains("open")) close();
    });
    // 原生 select 主动触发 change（页面代码触发的场景）
    sel.addEventListener("change", function () { sync(); highlight(); });
    // 键盘：方向键选择、Enter 确认/展开、Esc 关闭
    box.addEventListener("keydown", function (e) {
      var isOpen = list.classList.contains("open");
      if (!isOpen) {
        if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          open();
        }
        return;
      }
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var cur = sel.selectedIndex;
        var n = e.key === "ArrowDown"
          ? Math.min(sel.options.length - 1, cur + 1)
          : Math.max(0, cur - 1);
        sel.selectedIndex = n;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sync();
        highlight();
        var it = list.children[n];
        if (it && it.scrollIntoView) it.scrollIntoView({ block: "nearest" });
        return;
      }
      if (e.key === "Enter") { e.preventDefault(); close(); }
    });
    // 漂移校正（低频）：捕获异步 set value / 动态增删 options 的差异
    window.setInterval(function () {
      if (!wrap.isConnected) return;
      var changed = false;
      var optLen = sel.options ? sel.options.length : 0;
      if (optLen !== list.children.length) changed = true;
      else {
        var o = selectedOption();
        if (o && o.textContent !== val.textContent) changed = true;
      }
      if (changed) render(); else { sync(); highlight(); }
    }, 600);

    render();
  }

  function uiSelectInitAll() {
    document.querySelectorAll("select").forEach(uiSelectBuild);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", uiSelectInitAll);
  }
  // 立即执行：脚本位于 </body> 前，DOM 已就绪，避免首次导航时原生 select 长时间未被包装
  uiSelectInitAll();
})();
