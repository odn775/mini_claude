<script setup lang="ts">
import ChatView from '@/views/ChatView.vue'
import SessionSidebar from '@/components/SessionSidebar.vue'
import TopBar from '@/components/TopBar.vue'
import { useChatStore } from '@/stores/chat'

// 三个根元素直接落到 #app 的 grid 上（布局 CSS 不需要额外包裹层）
useChatStore().init()
</script>

<template>
  <TopBar />
  <SessionSidebar />
  <ChatView />
</template>
