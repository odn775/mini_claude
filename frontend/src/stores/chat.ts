/** 聊天状态（Pinia）：会话列表、消息流、SSE 事件处理、知识库状态。 */

import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'

import { api } from '@/api/client'
import type { ChatMessage, KbStatus, SessionSummary, StreamEvent } from '@/api/types'

const EMPTY_KB: KbStatus = {
  building: false,
  last_error: null,
  exists: false,
  total_chunks: 0,
  files: [],
}

const TOOL_RESULT_LIMIT = 4000

export const useChatStore = defineStore('chat', () => {
  /* ── 状态 ── */
  const currentId = ref<string | null>(null)
  const busy = ref(false)
  const sessions = ref<SessionSummary[]>([])
  const model = ref('')
  const allowBash = ref(true)
  const messages = ref<ChatMessage[]>([])
  const showWelcome = ref(false)
  const kb = ref<KbStatus>({ ...EMPTY_KB })
  const kbError = ref('')
  /** 自增以驱动消息区滚动 */
  const scrollTick = ref(0)

  /* 流式一轮的临时状态（不需要响应式，所以不放 ref） */
  let activeTurn: ChatMessage | null = null
  let turnBuf = ''
  let renderTimer: ReturnType<typeof setTimeout> | null = null
  let kbTimer: ReturnType<typeof setInterval> | null = null

  function touch() {
    scrollTick.value++
  }

  /** 60ms 节流渲染，避免每个 delta 都重解析 markdown */
  function flushTurn() {
    if (activeTurn) activeTurn.text = turnBuf
    touch()
  }

  function scheduleRender() {
    if (renderTimer) return
    renderTimer = setTimeout(() => {
      renderTimer = null
      flushTurn()
    }, 60)
  }

  function stopThinking() {
    if (renderTimer) {
      clearTimeout(renderTimer)
      renderTimer = null
    }
    if (activeTurn) activeTurn.thinking = false
  }

  /* ── SSE 事件 ── */

  function handleEvent(event: StreamEvent) {
    const turn = activeTurn
    if (!turn) return

    switch (event.type) {
      case 'text_delta':
        turnBuf += event.content
        stopThinking() // 文本开始流式，去掉「正在思考…」
        scheduleRender()
        break

      case 'tool_call':
        turn.statusText = `正在调用 ${event.name}…`
        turn.tools?.push({
          key: event.call_id || event.name,
          name: event.name,
          status: 'running',
          args: JSON.stringify(event.arguments ?? {}, null, 2),
          result: '',
        })
        touch()
        break

      case 'tool_result': {
        const key = event.call_id || event.name
        const list = turn.tools ?? []
        const target =
          list.find((t) => t.key === key && t.status === 'running') ??
          list.find((t) => t.key === key)
        if (target) {
          target.status = 'done'
          let content = (event.content || '').trim() || '(无输出)'
          if (content.length > TOOL_RESULT_LIMIT) {
            content = content.slice(0, TOOL_RESULT_LIMIT) + '\n…(已截断)'
          }
          target.result = content
          touch()
        }
        break
      }

      case 'text':
        turnBuf = event.content
        stopThinking()
        flushTurn()
        break

      case 'notice':
        turn.statusText = event.message
        break

      case 'error':
        stopThinking()
        messages.value.push({ role: 'error', text: event.message || '发生错误' })
        touch()
        break

      default:
        // usage / compact / done 只影响后端指标与收尾，界面无需呈现
        break
    }
  }

  /* ── 会话 ── */

  async function loadSessions() {
    sessions.value = await api.listSessions()
  }

  function showWelcomePage() {
    currentId.value = null
    messages.value = []
    showWelcome.value = true
    touch()
  }

  async function openSession(id: string) {
    if (busy.value || id === currentId.value) return
    try {
      const detail = await api.getSession(id)
      currentId.value = detail.id
      messages.value = detail.messages.map((m) => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: m.content,
      }))
      showWelcome.value = false
      touch()
    } catch {
      showWelcomePage()
    }
  }

  async function deleteSession(id: string) {
    await api.deleteSession(id)
    if (currentId.value === id) showWelcomePage()
    await loadSessions()
  }

  function newChat() {
    if (busy.value) return
    showWelcomePage()
  }

  /* ── 知识库 ── */

  function applyKb(status: KbStatus) {
    kb.value = status
    kbError.value = ''
  }

  async function refreshKb() {
    try {
      const status = await api.kbStatus()
      applyKb(status)
      // 重建进行中时轮询，直到结束
      if (status.building && kbTimer === null) {
        kbTimer = setInterval(async () => {
          try {
            const next = await api.kbStatus()
            applyKb(next)
            if (!next.building && kbTimer !== null) {
              clearInterval(kbTimer)
              kbTimer = null
            }
          } catch {
            /* 轮询失败等下一轮 */
          }
        }, 2000)
      }
    } catch {
      kbError.value = '状态获取失败'
    }
  }

  async function rebuildKb() {
    if (
      !window.confirm(
        '重建知识库索引会调用 embedding API（消耗少量额度），并覆盖现有索引。确定重建？',
      )
    ) {
      return
    }
    try {
      await api.kbRebuild()
      await refreshKb()
    } catch (err) {
      window.alert(`重建失败: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  /* ── 发消息 ── */

  async function send(rawText: string) {
    const text = (rawText || '').trim()
    if (busy.value || !text) return

    showWelcome.value = false
    messages.value.push({ role: 'user', text })

    turnBuf = ''
    // 必须用 reactive 包一层：闭包持有的是这个响应式代理，
    // 流式期间对它赋值才会触发界面更新（Push 进数组的是同一份代理）。
    const turn = reactive<ChatMessage>({
      role: 'turn',
      text: '',
      tools: [],
      thinking: true,
      statusText: '正在思考…',
    })
    activeTurn = turn
    messages.value.push(turn)
    busy.value = true
    touch()

    try {
      // 新会话：先在库里建会话，再往 /sessions/{id}/messages 发消息
      if (!currentId.value) {
        const created = await api.createSession()
        currentId.value = created.id
      }
      await api.streamMessages(
        currentId.value,
        { message: text, allow_bash: allowBash.value },
        handleEvent,
      )
    } catch (err) {
      stopThinking()
      messages.value.push({
        role: 'error',
        text: err instanceof Error ? err.message : '网络错误',
      })
    } finally {
      stopThinking()
      flushTurn() // 确保缓冲里的最后一段文本渲染出来
      activeTurn = null
      busy.value = false
      touch()
      void loadSessions().catch(() => {})
    }
  }

  /* ── 初始化 ── */

  async function init() {
    try {
      const config = await api.getConfig()
      model.value = config.model
      allowBash.value = config.default_allow_bash
    } catch {
      /* 配置拿不到不影响聊天 */
    }
    void refreshKb()
    try {
      await loadSessions()
    } catch {
      /* 首次加载会话失败时留在欢迎页 */
    }
    if (sessions.value.length) {
      await openSession(sessions.value[0].id)
    } else {
      showWelcomePage()
    }
  }

  return {
    currentId,
    busy,
    sessions,
    model,
    allowBash,
    messages,
    showWelcome,
    kb,
    kbError,
    scrollTick,
    init,
    send,
    newChat,
    openSession,
    deleteSession,
    rebuildKb,
    refreshKb,
  }
})
