<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { ChatMessage } from '@/api/types'
import { renderMarkdown } from '@/utils/markdown'
import { isHiddenTool } from '@/utils/tools'

import EmergencyAlert from './EmergencyAlert.vue'
import ToolCard from './ToolCard.vue'
import TriageCard from './TriageCard.vue'

const props = defineProps<{ msg: ChatMessage }>()

const html = computed(() => renderMarkdown(props.msg.text))

/** 医疗界面只展示与导诊相关的工具调用 */
const visibleTools = computed(() =>
  (props.msg.tools ?? []).filter((t) => !isHiddenTool(t.name)),
)

/** 工具卡片默认展开；用户手动折叠后不被新的工具覆盖。 */
const opened = ref<string[]>([])
watch(
  () => visibleTools.value.map((t) => t.key).join('|'),
  () => {
    for (const key of visibleTools.value.map((t) => t.key)) {
      if (!opened.value.includes(key)) opened.value.push(key)
    }
  },
  { immediate: true },
)
</script>

<template>
  <template v-if="msg.role === 'user'">
    <EmergencyAlert v-if="msg.emergency && msg.emergency.length" :labels="msg.emergency" />
    <div class="msg user">
      <div class="bubble">{{ msg.text }}</div>
    </div>
  </template>

  <div v-else-if="msg.role === 'error'" class="msg error">
    <div class="bubble">{{ msg.text }}</div>
  </div>

  <div v-else-if="msg.role === 'assistant'" class="msg assistant">
    <div class="bubble md" v-html="html" />
  </div>

  <!-- 流式中的一轮：工具卡片 + 分诊卡 + 增量文本 + 状态行 -->
  <div v-else class="msg assistant">
    <div class="bubble">
      <el-collapse v-if="visibleTools.length" v-model="opened" class="activity">
        <ToolCard v-for="(tool, i) in visibleTools" :key="i" :tool="tool" />
      </el-collapse>
      <TriageCard v-if="msg.triage" :data="msg.triage" />
      <div v-if="msg.text" class="md" v-html="html" />
      <div v-if="msg.thinking" class="status-line">
        <span class="spinner" /><span>{{ msg.statusText }}</span>
      </div>
    </div>
  </div>
</template>
