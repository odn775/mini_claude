<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useChatStore } from '@/stores/chat'

const store = useChatStore()
const { busy } = storeToRefs(store)
</script>

<template>
  <header class="topbar">
    <el-button class="new-chat" :disabled="busy" @click="store.newChat()">新会话</el-button>
    <span class="spacer" />
  </header>
</template>
