<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useChatStore } from '@/stores/chat'

const store = useChatStore()
const { busy } = storeToRefs(store)
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
    <span class="spacer" />
    <el-button class="new-chat" :disabled="busy" @click="store.newChat()">新问诊</el-button>
  </header>
</template>
