<script setup lang="ts">
import { Position } from '@element-plus/icons-vue'
import { ref } from 'vue'

const props = defineProps<{ busy: boolean; model: string }>()
const emit = defineEmits<{ send: [text: string] }>()

const text = ref('')

function submit() {
  const value = text.value.trim()
  if (!value || props.busy) return
  emit('send', value)
  text.value = ''
}
</script>

<template>
  <div class="input-bar">
    <div class="input-box">
      <el-input
        v-model="text"
        class="input-el"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 6 }"
        resize="none"
        placeholder="请描述症状、持续时间和伴随情况…"
        @keydown.enter.exact.prevent="submit"
      />
      <div class="input-actions">
        <span class="spacer" />
        <span class="model-name">{{ model }}</span>
        <el-button
          class="send-btn"
          :icon="Position"
          :disabled="busy"
          title="发送"
          @click="submit"
        />
      </div>
    </div>
    <div class="hint">Enter 发送 · Shift+Enter 换行</div>
    <div class="disclaimer">
      本工具仅提供就诊科室参考，不构成医疗诊断；急危重症请立即拨打 120
    </div>
  </div>
</template>
