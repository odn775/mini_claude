/* Vue 组件层：TopBar / SideBar / Welcome / MessageList / MessageItem / InputBar / ChatMain。
 * 根组件 App 采用多根模板（fragment），直接落到 #app 的 grid 布局上。
 */
(function () {
  "use strict";

  const { computed, ref, watch, nextTick } = Vue;

  /* ── Markdown 渲染 ── */

  const md = window.markdownit
    ? window.markdownit({
        html: false,
        linkify: true,
        highlight(str, lang) {
          if (window.hljs && lang && hljs.getLanguage(lang)) {
            try {
              return (
                '<pre><code class="hljs">' +
                hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
                "</code></pre>"
              );
            } catch (e) {}
          }
          return (
            '<pre><code class="hljs">' +
            window.markdownit().utils.escapeHtml(str) +
            "</code></pre>"
          );
        },
      })
    : null;

  function renderMd(text) {
    if (md) return md.render(text || "");
    const esc = (s) =>
      String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    return esc(text || "").replace(/\n/g, "<br>");
  }

  /* ── 顶栏 ── */

  const TopBar = {
    props: { busy: Boolean },
    emits: ["new-chat"],
    template: `
      <header class="topbar">
        <button class="new-chat" :disabled="busy" @click="$emit('new-chat')">新会话</button>
        <span class="spacer"></span>
      </header>`,
  };

  /* ── 侧边栏（会话列表 + 知识库面板） ── */

  const SideBar = {
    setup() {
      const store = Store.store;
      const filter = ref("");

      const items = computed(() => {
        const kw = filter.value.trim().toLowerCase();
        return store.sessions.filter((s) => !kw || s.title.toLowerCase().includes(kw));
      });

      const kbDisplay = computed(() => {
        if (store.kbError) return store.kbError;
        const k = store.kb;
        if (k.building) return "正在重建索引…";
        const parts = [];
        if (k.exists) {
          parts.push("已构建 · " + k.total_chunks + " 块");
          if (k.files && k.files.length) parts.push("(" + k.files.length + " 文件)");
        } else {
          parts.push("未构建");
        }
        if (k.last_error) parts.push("上次重建失败: " + k.last_error);
        return parts.join(" · ");
      });

      const open = (id) => Store.openSession(id);
      const del = (id) => Store.deleteSession(id);
      const rebuild = () => Store.rebuildKb();

      return { store, filter, items, kbDisplay, open, del, rebuild };
    },
    template: `
      <aside class="sidebar">
        <div class="sidebar-title">工作区</div>
        <input class="search" type="text" placeholder="搜索会话" v-model="filter">
        <div class="session-list">
          <div v-for="s in items" :key="s.id"
               class="session-item" :class="{ active: s.id === store.currentId }"
               :title="s.title">
            <span class="title" @click="open(s.id)">{{ s.title }}</span>
            <button class="del" title="删除会话" @click.stop="del(s.id)">×</button>
          </div>
        </div>
        <div class="kb-panel">
          <div class="kb-title">知识库</div>
          <div class="kb-status">{{ kbDisplay }}</div>
          <button class="kb-rebuild" :disabled="store.kb.building" @click="rebuild">重建索引</button>
        </div>
      </aside>`,
  };

  /* ── 欢迎页 ── */

  const Welcome = {
    emits: ["pick"],
    data() {
      return {
        suggestions: [
          "解读这个项目的架构",
          "帮我重构某个模块",
          "解释这段代码",
          "检查代码质量问题",
        ],
      };
    },
    template: `
      <div class="welcome">
        <h1>有什么可以帮你的？</h1>
        <div class="chips">
          <div v-for="t in suggestions" :key="t" class="chip" @click="$emit('pick', t)">{{ t }}</div>
        </div>
      </div>`,
  };

  /* ── 单条消息 ── */

  const MessageItem = {
    props: { msg: Object },
    setup(props) {
      const html = computed(() => renderMd(props.msg.text));
      return { html };
    },
    template: `
      <div v-if="msg.role === 'user'" class="msg user">
        <div class="bubble">{{ msg.text }}</div>
      </div>

      <div v-else-if="msg.role === 'error'" class="msg error">
        <div class="bubble">{{ msg.text }}</div>
      </div>

      <div v-else-if="msg.role === 'assistant'" class="msg assistant">
        <div class="bubble md" v-html="html"></div>
      </div>

      <div v-else class="msg assistant">
        <div class="bubble">
          <div v-if="msg.tools.length" class="activity">
            <details v-for="(t, i) in msg.tools" :key="i" class="tool-card" open>
              <summary>
                <span class="dot" :class="t.status"></span>
                <span class="tw">{{ t.name }}</span>
                <span class="ts">{{ t.status === 'running' ? '运行中' : '完成' }}</span>
              </summary>
              <pre class="args">{{ t.args }}</pre>
              <pre v-if="t.status === 'done'" class="res">{{ t.result }}</pre>
            </details>
          </div>
          <div v-if="msg.text" class="md" v-html="html"></div>
          <div v-if="msg.thinking" class="status-line">
            <span class="spinner"></span><span>{{ msg.statusText }}</span>
          </div>
        </div>
      </div>`,
  };

  /* ── 消息列表（含自动滚动） ── */

  const MessageList = {
    components: { MessageItem },
    setup() {
      const store = Store.store;
      const el = ref(null);
      watch(
        () => store.scrollTick,
        () => {
          nextTick(() => {
            if (el.value) el.value.scrollTop = el.value.scrollHeight;
          });
        }
      );
      return { store, el };
    },
    template: `
      <div class="messages" ref="el">
        <div class="messages-inner">
          <MessageItem v-for="(m, i) in store.messages" :key="i" :msg="m"></MessageItem>
        </div>
      </div>`,
  };

  /* ── 输入区 ── */

  const InputBar = {
    props: { busy: Boolean, model: String, allowBash: Boolean },
    emits: ["send", "update:allowBash"],
    data() {
      return { text: "" };
    },
    methods: {
      onKeydown(e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.submit();
        }
      },
      onInput(e) {
        const el = e.target;
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 160) + "px";
      },
      submit() {
        const t = this.text.trim();
        if (!t || this.busy) return;
        this.$emit("send", t);
        this.text = "";
        if (this.$refs.ta) this.$refs.ta.style.height = "auto";
      },
    },
    template: `
      <div class="input-bar">
        <div class="input-box">
          <textarea ref="ta" class="input-el" rows="1"
                    placeholder="描述你想要构建的内容"
                    v-model="text" @keydown="onKeydown" @input="onInput"></textarea>
          <div class="input-actions">
            <label class="bash-toggle" title="关闭后模型将无法执行 shell 命令">
              <input type="checkbox" :checked="allowBash"
                     @change="$emit('update:allowBash', $event.target.checked)">
              <span>允许执行命令</span>
            </label>
            <span class="spacer"></span>
            <span class="model-name">{{ model }}</span>
            <button class="send-btn" title="发送" :disabled="busy" @click="submit">➤</button>
          </div>
        </div>
        <div class="hint">Enter 发送 · Shift+Enter 换行</div>
      </div>`,
  };

  /* ── 主区域（欢迎页 / 消息 + 输入区） ── */

  const ChatMain = {
    components: { Welcome, MessageList, InputBar },
    setup() {
      const store = Store.store;
      const onSend = (text) => Store.send(text);
      const onPick = (text) => Store.send(text);
      const onNewChat = () => Store.newChat();
      return { store, onSend, onPick, onNewChat };
    },
    template: `
      <main class="main">
        <Welcome v-if="store.showWelcome" @pick="onPick"></Welcome>
        <MessageList v-else></MessageList>
        <InputBar v-model:allow-bash="store.allowBash"
                  :busy="store.busy" :model="store.model"
                  @send="onSend"></InputBar>
      </main>`,
  };

  window.Components = { TopBar, SideBar, ChatMain };
})();
