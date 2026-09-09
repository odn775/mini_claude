/* API 封装层：所有后端 HTTP 调用（含 SSE 流式解析）集中在这里。 */
(function () {
  "use strict";

  async function json(resp) {
    return resp.json();
  }

  const Api = {
    /* 配置 */
    getConfig: () => fetch("/api/config").then(json),

    /* 会话管理 */
    listSessions: () => fetch("/api/sessions").then(json),
    getSession: (id) => fetch("/api/sessions/" + id).then(json),
    deleteSession: (id) => fetch("/api/sessions/" + id, { method: "DELETE" }),

    /* 用量统计 */
    getUsageSessions: () => fetch("/api/usage/sessions").then(json),

    /* 知识库 */
    kbStatus: () => fetch("/api/kb/status").then(json),
    kbRebuild: async () => {
      const r = await fetch("/api/kb/rebuild", { method: "POST" });
      if (r.status === 409) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || "正在重建中");
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },

    /* 聊天（SSE 流式）。onEvent 逐条接收 JSON 事件 */
    streamChat: async (payload, onEvent) => {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "HTTP " + resp.status }));
        throw new Error(err.detail || "请求失败");
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = block.split("\n").find((l) => l.indexOf("data:") === 0);
          if (!line) continue;
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            /* 忽略无法解析的帧 */
          }
        }
      }
    },
  };

  window.Api = Api;
})();
