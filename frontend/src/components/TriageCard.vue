<script setup lang="ts">
import { computed } from 'vue'

import type { TriageResult } from '@/api/types'
import { URGENCY_META } from '@/utils/triage'

const props = defineProps<{ data: TriageResult }>()

const badge = computed(() => URGENCY_META[props.data.urgency]?.label ?? '普通门诊')
</script>

<template>
  <div class="triage-card" :data-urgency="data.urgency">
    <div class="triage-head">
      <span class="triage-badge">{{ badge }}</span>
      <span class="triage-dept">{{ data.primary_department }}</span>
      <span v-if="data.alternate_department" class="triage-alt">
        备选 {{ data.alternate_department }}
      </span>
    </div>
    <div v-if="data.reasoning" class="triage-row">
      <span class="triage-k">依据</span>
      <span class="triage-v">{{ data.reasoning }}</span>
    </div>
    <div v-if="data.advice" class="triage-row">
      <span class="triage-k">建议</span>
      <span class="triage-v">{{ data.advice }}</span>
    </div>
  </div>
</template>
