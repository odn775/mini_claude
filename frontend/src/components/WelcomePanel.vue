<script setup lang="ts">
defineProps<{ suggestions: string[] }>()
defineEmits<{ pick: [text: string] }>()
</script>

<template>
  <div class="welcome">
    <h1>请描述您的不适</h1>
    <p class="welcome-sub">说说症状、持续多久了，我会帮您判断该挂哪个科</p>
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
