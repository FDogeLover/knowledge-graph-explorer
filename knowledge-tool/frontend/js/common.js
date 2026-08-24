/* 公共脚本：导航高亮 + API 助手 + toast + 任务轮询 */
(function () {
  "use strict";

  // 导航高亮
  var page = (location.pathname.split("/").pop() || "index.html").replace(".html", "");
  document.querySelectorAll(".nav a").forEach(function (a) {
    var href = a.getAttribute("href").replace("/", "").replace(".html", "");
    if (href === page || (page === "index" && href === "")) a.classList.add("active");
  });

  // API 助手
  window.api = {
    async get(path) {
      var r = await fetch(path);
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },
    async post(path, body) {
      var r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },
    async del(path) {
      var r = await fetch(path, { method: "DELETE" });
      if (!r.ok) throw new Error("HTTP " + r.status);
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
