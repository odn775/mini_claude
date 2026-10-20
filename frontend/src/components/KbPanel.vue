<script setup lang="ts">
import { computed } from 'vue'

import { useChatStore } from '@/stores/chat'

const store = useChatStore()

/** 状态文案：与旧版一致，另加「上次重建失败」提示。 */
const display = computed(() => {
  if (store.kbError) return store.kbError
  const status = store.kb
  if (status.building) return '正在重建索引…'
  const parts: string[] = []
  if (status.exists) {
    parts.push(`已构建 · ${status.total_chunks} 块`)
    if (status.files.length) parts.push(`(${status.files.length} 文件)`)
  } else {
    parts.push('未构建')
  }
  if (status.last_error) parts.push(`上次重建失败: ${status.last_error}`)
  return parts.join(' · ')
})
</script>

<template>
  <div class="kb-panel">
    <div class="kb-title">知识库</div>
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
