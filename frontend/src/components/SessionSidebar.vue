<script setup lang="ts">
import { Close, Search } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { useChatStore } from '@/stores/chat'

import KbPanel from './KbPanel.vue'

const store = useChatStore()
const filter = ref('')
/** 默认收起，由用户自行展开 */
const expanded = ref(false)

const items = computed(() => {
  const keyword = filter.value.trim().toLowerCase()
  if (!keyword) return store.sessions
  return store.sessions.filter((s) => s.title.toLowerCase().includes(keyword))
})

function remove(id: string) {
  void store.deleteSession(id)
}
</script>

<template>
  <aside class="sidebar">
    <section class="side-section" :class="{ open: expanded }">
      <button
        class="side-section-head"
        type="button"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        <svg class="side-chev" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            v-if="expanded"
            d="M4 6l4 4 4-4"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
          <path
            v-else
            d="M6 4l4 4-4 4"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span>问诊记录</span>
        <span class="side-count">{{ store.sessions.length }}</span>
      </button>

      <template v-if="expanded">
        <el-input
          v-model="filter"
          class="search"
          placeholder="搜索问诊记录"
          :prefix-icon="Search"
          clearable
        />
        <div class="session-list">
          <div
            v-for="s in items"
            :key="s.id"
            class="session-item"
            :class="{ active: s.id === store.currentId }"
            :title="s.title"
          >
            <span class="title" @click="store.openSession(s.id)">{{ s.title }}</span>
            <span v-if="s.source === 'cli'" class="src">CLI</span>
            <el-button
              class="del"
              text
              size="small"
              :icon="Close"
              title="删除记录"
              @click.stop="remove(s.id)"
            />
          </div>
          <div v-if="!items.length" class="empty">暂无问诊记录</div>
        </div>
      </template>
    </section>

    <KbPanel />
  </aside>
</template>
