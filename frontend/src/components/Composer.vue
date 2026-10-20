<script setup lang="ts">
import { Position } from '@element-plus/icons-vue'
import type { CheckboxValueType } from 'element-plus'
import { computed, ref } from 'vue'

const props = defineProps<{ busy: boolean; model: string; allowBash: boolean }>()
const emit = defineEmits<{ send: [text: string]; 'update:allowBash': [value: boolean] }>()

const text = ref('')

const bash = computed<CheckboxValueType>({
  get: () => props.allowBash,
  set: (value) => emit('update:allowBash', Boolean(value)),
})

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
        placeholder="描述你想要构建的内容"
        @keydown.enter.exact.prevent="submit"
      />
      <div class="input-actions">
        <el-tooltip content="关闭后模型将无法执行 shell 命令" placement="top">
          <el-checkbox v-model="bash" label="允许执行命令" />
        </el-tooltip>
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
  </div>
</template>
