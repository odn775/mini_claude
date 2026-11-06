<script setup lang="ts">
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GeoComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { TriageStats } from '@/api/types'

echarts.use([BarChart, GeoComponent, GridComponent, TooltipComponent, CanvasRenderer])

/** 时间窗选项：0 表示全部 */
const WINDOWS = [
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
  { label: '全部', days: 0 },
]

const days = ref(7)
const stats = ref<TriageStats | null>(null)
const loading = ref(false)
const error = ref('')

const organRef = ref<HTMLDivElement | null>(null)
const barRef = ref<HTMLDivElement | null>(null)
let organChart: ReturnType<typeof echarts.init> | null = null
let barChart: ReturnType<typeof echarts.init> | null = null

const emergencyRate = computed(() => {
  const s = stats.value
  if (!s || !s.triage_count) return '—'
  return `${Math.round((s.emergency_count / s.triage_count) * 100)}%`
})

const isEmpty = computed(() => !stats.value || stats.value.triage_count === 0)

/** 器官图纯装饰：只做悬停高亮，不绑定任何数据 */
let mapReady: Promise<void> | null = null

function ensureOrganMap(): Promise<void> {
  if (mapReady) return mapReady
  mapReady = fetch('/organ.svg')
    .then((resp) => resp.text())
    .then((svg) => {
      echarts.registerMap('organ_diagram', { svg })
    })
  return mapReady
}

function renderOrgan() {
  if (!organRef.value) return
  organChart = organChart ?? echarts.init(organRef.value)
  ensureOrganMap().then(() => {
    organChart?.setOption({
      geo: {
        map: 'organ_diagram',
        roam: false,
        left: 4,
        right: 4,
        top: 4,
        bottom: 4,
        label: { show: false },
        itemStyle: {
          color: '#e8f4f7',
          borderColor: '#a5d4df',
          borderWidth: 1,
        },
        emphasis: {
          label: { show: false },
          itemStyle: { color: '#0e7490', borderColor: '#0e7490' },
        },
        select: { disabled: true },
      },
    })
  })
}

function renderBars() {
  if (!barRef.value) return
  barChart = barChart ?? echarts.init(barRef.value)

  const rows = stats.value?.systems ?? []
  // 横向条形图：类目轴从下往上画，这里反转让访问最多的排在最上面
  const names = rows.map((r) => r.name).reverse()
  const values = rows.map((r) => r.count).reverse()
  const top = Math.max(0, ...rows.map((r) => r.count))
  const topIndex = values.lastIndexOf(top)

  barChart.setOption(
    {
      grid: { left: 4, right: 36, top: 8, bottom: 4, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const list = params as { dataIndex: number }[]
          const row = rows[rows.length - 1 - list[0].dataIndex]
          if (!row) return ''
          const detail = row.departments
            .map((d) => `${d.name} ${d.count}`)
            .join('　')
          return `${row.name}：${row.count} 次<br/>${detail}`
        },
      },
      xAxis: { type: 'value', show: false, max: top || 1 },
      yAxis: {
        type: 'category',
        data: names,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#63707c', fontSize: 13, margin: 12 },
      },
      series: [
        {
          type: 'bar',
          barWidth: '52%',
          data: values.map((v, i) => ({
            value: v,
            itemStyle: {
              color: i === topIndex ? '#0e7490' : '#a5d4df',
              borderRadius: [0, 4, 4, 0],
            },
          })),
          label: {
            show: true,
            position: 'right',
            color: '#63707c',
            fontSize: 12,
          },
        },
      ],
    },
    true,
  )
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    stats.value = await api.triageStats(days.value)
    renderBars()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function onResize() {
  organChart?.resize()
  barChart?.resize()
}

watch(days, load)

onMounted(() => {
  renderOrgan()
  void load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  organChart?.dispose()
  barChart?.dispose()
  organChart = null
  barChart = null
})
</script>

<template>
  <main class="stats">
    <div class="stats-inner">
      <div class="stats-head">
        <h1>导诊统计</h1>
        <div class="stats-windows">
          <button
            v-for="w in WINDOWS"
            :key="w.days"
            type="button"
            class="win"
            :class="{ active: days === w.days }"
            @click="days = w.days"
          >
            {{ w.label }}
          </button>
        </div>
      </div>

      <div v-if="error" class="stats-error">{{ error }}</div>

      <div class="stats-cards">
        <div class="card">
          <span class="card-label">总问诊量</span>
          <span class="card-value">{{ stats?.total_consultations ?? '—' }}</span>
        </div>
        <div class="card">
          <span class="card-label">急诊占比</span>
          <span class="card-value">{{ emergencyRate }}</span>
        </div>
        <div class="card">
          <span class="card-label">覆盖科室</span>
          <span class="card-value">{{ stats?.department_count ?? '—' }}</span>
        </div>
      </div>

      <div class="stats-body" :class="{ loading }">
        <section class="panel organ-panel">
          <div class="panel-title">人体器官示意</div>
          <div ref="organRef" class="organ-chart" />
          <div class="panel-note">示意图仅作装饰，不承载统计数据</div>
        </section>

        <section class="panel bar-panel">
          <div class="panel-title">各疾病系统访问量</div>
          <div v-if="isEmpty" class="bar-empty">暂无统计数据，先完成几次导诊</div>
          <div ref="barRef" class="bar-chart" :class="{ dim: isEmpty }" />
        </section>
      </div>

      <div class="stats-foot">
        * 一次「给出科室建议」计 1 次访问；追问与范围外提问只计入总问诊量。
        时间窗按会话最近活跃时间统计。
      </div>
    </div>
  </main>
</template>
