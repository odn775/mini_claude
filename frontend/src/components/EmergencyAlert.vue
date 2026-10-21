<script setup lang="ts">
defineProps<{ labels: string[] }>()
</script>

<template>
  <div class="emergency-alert" role="alert">
    <span class="ea-title">急危重症提示</span>
    <span class="ea-body">
      检测到「{{ labels.join('、') }}」，请立即拨打 120 或前往最近医院急诊科，
      不要等待线上建议。
    </span>
  </div>
</template>
