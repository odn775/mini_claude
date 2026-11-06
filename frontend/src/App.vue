<script setup lang="ts">
import { ref } from 'vue'

import SessionSidebar from '@/components/SessionSidebar.vue'
import TopBar from '@/components/TopBar.vue'
import { useChatStore } from '@/stores/chat'
import ChatView from '@/views/ChatView.vue'
import StatsView from '@/views/StatsView.vue'

// 三个根元素直接落到 #app 的 grid 上（布局 CSS 不需要额外包裹层）
useChatStore().init()

/** 导诊 / 统计切换；项目只有两个视图，不引入 vue-router */
const view = ref<'chat' | 'stats'>('chat')
</script>

<template>
  <TopBar v-model:view="view" />
  <SessionSidebar />
  <ChatView v-if="view === 'chat'" />
  <StatsView v-else />
</template>
