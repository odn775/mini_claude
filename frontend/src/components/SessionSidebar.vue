<script setup lang="ts">
import { Close, Search } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { useChatStore } from '@/stores/chat'

import KbPanel from './KbPanel.vue'

const store = useChatStore()
const filter = ref('')

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
    <div class="sidebar-title">工作区</div>
    <el-input
      v-model="filter"
      class="search"
      placeholder="搜索会话"
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
          title="删除会话"
          @click.stop="remove(s.id)"
        />
      </div>
      <div v-if="!items.length" class="empty">暂无会话</div>
    </div>
    <KbPanel />
  </aside>
</template>
