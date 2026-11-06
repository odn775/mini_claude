<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useChatStore } from '@/stores/chat'

defineProps<{ view: 'chat' | 'stats' }>()
const emit = defineEmits<{ 'update:view': [view: 'chat' | 'stats'] }>()

const store = useChatStore()
const { busy } = storeToRefs(store)

function newChat() {
  emit('update:view', 'chat')
  store.newChat()
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <svg class="brand-logo" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect
          x="2.5"
          y="2.5"
          width="19"
          height="19"
          rx="6"
          stroke="currentColor"
          stroke-width="1.5"
          opacity="0.35"
        />
        <path
          d="M12 7.5v9M7.5 12h9"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
      <div class="brand-text">
        <span class="brand-name">智能导诊助手</span>
        <span class="brand-sub">AI 预问诊 · 仅供参考</span>
      </div>
    </div>

    <nav class="top-nav">
      <button
        type="button"
        class="nav-btn"
        :class="{ active: view === 'chat' }"
        @click="emit('update:view', 'chat')"
      >
        导诊
      </button>
      <button
        type="button"
        class="nav-btn"
        :class="{ active: view === 'stats' }"
        @click="emit('update:view', 'stats')"
      >
        统计分析
      </button>
    </nav>

    <span class="spacer" />
    <el-button class="new-chat" :disabled="busy" @click="newChat">新问诊</el-button>
  </header>
</template>
