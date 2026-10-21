<script setup lang="ts">
import { computed } from 'vue'

import { useChatStore } from '@/stores/chat'

const store = useChatStore()

const display = computed(() => {
  if (store.kbError) return store.kbError
  const status = store.kb
  if (status.building) return '正在重建索引…'
  if (!status.exists || !status.total_chunks) return '暂无资料，可直接提问'
  const parts = [`已收录 ${status.total_chunks} 条`]
  if (status.files.length) parts.push(`${status.files.length} 份文档`)
  if (status.last_error) parts.push(`上次重建失败: ${status.last_error}`)
  return parts.join(' · ')
})
</script>

<template>
  <div class="kb-panel">
    <div class="kb-title">医学知识库</div>
    <div class="kb-status">{{ display }}</div>
    <el-button
      class="kb-rebuild"
      :loading="store.kb.building"
      :disabled="store.kb.building"
      @click="store.rebuildKb()"
    >
      重建索引
    </el-button>
  </div>
</template>
