<script setup lang="ts">
import Composer from '@/components/Composer.vue'
import MessageList from '@/components/MessageList.vue'
import WelcomePanel from '@/components/WelcomePanel.vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()

const suggestions = [
  '咳嗽有痰三天了',
  '胃痛反酸',
  '皮肤起了红疹很痒',
  '头痛头晕',
]
</script>

<template>
  <main class="main">
    <WelcomePanel v-if="store.showWelcome" :suggestions="suggestions" @pick="store.send" />
    <MessageList v-else />
    <Composer :busy="store.busy" :model="store.model" @send="store.send" />
  </main>
</template>
