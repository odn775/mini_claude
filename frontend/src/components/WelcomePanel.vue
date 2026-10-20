<script setup lang="ts">
defineProps<{ suggestions: string[] }>()
defineEmits<{ pick: [text: string] }>()
</script>

<template>
  <div class="welcome">
    <h1>有什么可以帮你的？</h1>
    <div class="chips">
      <el-button
        v-for="text in suggestions"
        :key="text"
        class="chip"
        @click="$emit('pick', text)"
      >
        {{ text }}
      </el-button>
    </div>
  </div>
</template>
