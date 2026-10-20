<script setup lang="ts">
import Composer from '@/components/Composer.vue'
import MessageList from '@/components/MessageList.vue'
import WelcomePanel from '@/components/WelcomePanel.vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()

const suggestions = [
  '解读这个项目的架构',
  '帮我重构某个模块',
  '解释这段代码',
  '检查代码质量问题',
]
</script>

<template>
  <main class="main">
    <WelcomePanel v-if="store.showWelcome" :suggestions="suggestions" @pick="store.send" />
    <MessageList v-else />
    <Composer
      v-model:allowBash="store.allowBash"
      :busy="store.busy"
      :model="store.model"
      @send="store.send"
    />
  </main>
</template>
