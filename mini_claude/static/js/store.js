/* 响应式全局状态 + 业务动作（会话 / 知识库 / SSE 聊天流）。
 *
 * 消息模型（store.messages）：
 * - { role: "user",      text }
 * - { role: "assistant", text }                     历史消息
 * - { role: "turn", text, tools[], thinking, statusText }  流式中的一轮回答
 * - { role: "error",    text }
 *
 * turn.tools[] 项：{ key, name, status: "running"|"done", args, result }
 */
(function () {
  "use strict";

  const { reactive } = Vue;

  const store = reactive({
    currentId: null, // null = 新会话
    busy: false,
    sessions: [],
    model: "",
    allowBash: true,
    messages: [],
    showWelcome: false,
    kb: { building: false, exists: false, total_chunks: 0, files: [], last_error: "" },
    kbError: "",
    scrollTick: 0, // 自增以驱动消息区滚动
  });

  function touch() {
    store.scrollTick++;
  }

  /* ── 会话 ── */

  async function loadSessions() {
    store.sessions = await Api.listSessions();
  }

  function showWelcome() {
    store.currentId = null;
    store.messages = [];
    store.showWelcome = true;
    touch();
  }

  async function openSession(id) {
    try {
      const s = await Api.getSession(id);
      store.currentId = s.id;
      store.messages = (s.messages || []).map((m) => ({
        role: m.role,
        text: m.content,
      }));
      store.showWelcome = false;
      touch();
    } catch (e) {
      showWelcome();
    }
  }

  async function deleteSession(id) {
    await Api.deleteSession(id);
    if (store.currentId === id) showWelcome();
    await loadSessions();
  }

  function newChat() {
    if (store.busy) return;
    showWelcome();
  }

  /* ── 知识库 ── */

  let kbTimer = null;

  function applyKb(d) {
    Object.assign(store.kb, d);
    store.kbError = "";
  }

  async function refreshKb() {
    try {
      const d = await Api.kbStatus();
      applyKb(d);
      if (d.building && kbTimer === null) {
        kbTimer = setInterval(async () => {
          try {
            const d2 = await Api.kbStatus();
            applyKb(d2);
            if (!d2.building) {
              clearInterval(kbTimer);
              kbTimer = null;
            }
          } catch (e) {
            /* 轮询失败等下一轮 */
          }
        }, 2000);
      }
    } catch (e) {
      store.kbError = "状态获取失败";
    }
  }

  async function rebuildKb() {
    if (!confirm("重建知识库索引会调用 embedding API（消耗少量额度），并覆盖现有索引。确定重建？")) return;
    try {
      await Api.kbRebuild();
      refreshKb();
    } catch (err) {
      alert("重建失败: " + err.message);
    }
  }

  /* ── 聊天（SSE 流处理） ── */

  let activeTurn = null; // 当前一轮回答的容器（store.messages 中的一项）
  let turnBuf = ""; // 流式文本缓冲（节流后才写入 turn.text）
  let renderTimer = null;

  function flushTurn() {
    if (activeTurn) {
      activeTurn.text = turnBuf;
      touch();
    }
  }

  /* 60ms 节流渲染，避免每个 delta 都重解析 markdown */
  function scheduleRender() {
    if (renderTimer) return;
    renderTimer = setTimeout(() => {
      renderTimer = null;
      flushTurn();
    }, 60);
  }

  function stopThinking() {
    if (renderTimer) {
      clearTimeout(renderTimer);
      renderTimer = null;
    }
    if (activeTurn) activeTurn.thinking = false;
  }

  function handleEvent(ev) {
    if (ev.type === "session") {
      store.currentId = ev.id;
      return;
    }
    const turn = activeTurn;
    if (!turn) return;

    if (ev.type === "text_delta") {
      turnBuf += ev.content;
      stopThinking(); // 文本开始流式，去掉「正在思考…」
      scheduleRender();
    } else if (ev.type === "tool_call") {
      turn.statusText = "正在调用 " + (ev.name || "tool") + "…";
      turn.tools.push({
        key: ev.call_id || ev.name,
        name: ev.name || "tool",
        status: "running",
        args: JSON.stringify(ev.arguments || {}, null, 2),
        result: "",
      });
      touch();
    } else if (ev.type === "tool_result") {
      const key = ev.call_id || ev.name;
      let t = turn.tools.find((x) => x.key === key && x.status === "running");
      if (!t) t = turn.tools.find((x) => x.key === key);
      if (t) {
        t.status = "done";
        let content = (ev.content || "").trim();
        if (!content) content = "(无输出)";
        if (content.length > 4000) content = content.slice(0, 4000) + "\n…(已截断)";
        t.result = content;
        touch();
      }
    } else if (ev.type === "text") {
      turnBuf = ev.content; // 用完整文本做一次干净渲染
      stopThinking();
      flushTurn();
    } else if (ev.type === "error") {
      stopThinking();
      store.messages.push({ role: "error", text: ev.message || "发生错误" });
      touch();
    }
  }

  async function send(text) {
    const t = (text || "").trim();
    if (store.busy || !t) return;

    store.showWelcome = false;
    store.messages.push({ role: "user", text: t });

    turnBuf = "";
    activeTurn = reactive({
      role: "turn",
      text: "",
      tools: [],
      thinking: true,
      statusText: "正在思考…",
    });
    store.messages.push(activeTurn);
    store.busy = true;
    touch();

    try {
      await Api.streamChat(
        { session_id: store.currentId, message: t, allow_bash: store.allowBash },
        handleEvent
      );
    } catch (err) {
      stopThinking();
      store.messages.push({ role: "error", text: (err && err.message) || "网络错误" });
    } finally {
      stopThinking();
      flushTurn(); // 确保缓冲里的最后一段文本渲染出来
      activeTurn = null;
      store.busy = false;
      touch();
      loadSessions().catch(() => {});
    }
  }

  /* ── 初始化 ── */

  async function init() {
    Api.getConfig()
      .then((cfg) => {
        store.model = cfg.model || "";
      })
      .catch(() => {});
    refreshKb();
    await loadSessions().catch(() => {});
    if (store.sessions.length) {
      openSession(store.sessions[0].id);
    } else {
      showWelcome();
    }
  }

  window.Store = {
    store,
    init,
    send,
    newChat,
    openSession,
    deleteSession,
    rebuildKb,
    refreshKb,
    showWelcome,
    touch,
  };
})();
