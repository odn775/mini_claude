<script setup lang="ts">
import type { ToolRun } from '@/api/types'

defineProps<{ tool: ToolRun }>()
</script>

<template>
  <el-collapse-item class="tool-card" :name="tool.key">
    <template #title>
      <span class="tool-head">
        <span class="dot" :class="tool.status" />
        <span class="tw">{{ tool.name }}</span>
        <span class="ts">{{ tool.status === 'running' ? '运行中' : '完成' }}</span>
      </span>
    </template>
    <pre class="args">{{ tool.args }}</pre>
    <pre v-if="tool.status === 'done'" class="res">{{ tool.result }}</pre>
  </el-collapse-item>
</template>
