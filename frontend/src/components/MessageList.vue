<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import { useChatStore } from '@/stores/chat'

import MessageItem from './MessageItem.vue'

const store = useChatStore()
const container = ref<HTMLElement | null>(null)

// store.scrollTick 每次自增表示「内容变了」，滚到底部
watch(
  () => store.scrollTick,
  () => {
    void nextTick(() => {
      if (container.value) container.value.scrollTop = container.value.scrollHeight
    })
  },
)
</script>

<template>
  <div ref="container" class="messages">
    <div class="messages-inner">
      <MessageItem v-for="(msg, i) in store.messages" :key="i" :msg="msg" />
    </div>
  </div>
</template>
