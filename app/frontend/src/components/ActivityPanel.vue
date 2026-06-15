<script setup lang="ts">
import { NButton, NSpin, NTag } from 'naive-ui'
import type { InstrumentActivity } from '@/api/types'
import { fmtTs, sentLabel, sentType } from '@/composables/format'

defineProps<{
  activity: InstrumentActivity | null
  loading: boolean
}>()
const emit = defineEmits<{ addToGroup: [] }>()
</script>

<template>
  <div class="act-panel">
    <n-spin v-if="loading" size="small" />
    <div v-else-if="activity">
      <!-- 跟踪状态 -->
      <div class="track-row">
        <span class="faint">已跟踪分组:</span>
        <template v-if="activity.tracking_groups.length">
          <n-tag
            v-for="gid in activity.tracking_groups"
            :key="gid"
            size="small"
            type="success"
            :bordered="false"
          >
            {{ gid }}
          </n-tag>
        </template>
        <span v-else class="faint">未跟踪</span>
        <n-button size="tiny" type="primary" secondary style="margin-left: auto" @click="emit('addToGroup')">
          + 加入分组
        </n-button>
      </div>

      <div class="cols">
        <!-- 新闻 -->
        <div>
          <div class="col-title">新闻 <n-tag size="small" :bordered="false">{{ activity.news.length }}</n-tag></div>
          <div v-if="!activity.news.length" class="faint small">暂无新闻（运行 news 任务后填充）</div>
          <div v-for="(n, i) in activity.news" :key="i" class="item">
            <div class="item-head">
              <n-tag size="tiny" :type="sentType(n.sentiment)" :bordered="false">{{ sentLabel(n.sentiment) }}</n-tag>
              <a :href="n.url" target="_blank" rel="noopener" class="item-link">{{ n.title }}</a>
            </div>
            <div class="faint small">{{ n.source }} · {{ fmtTs(n.published) }}</div>
          </div>
        </div>
        <!-- 社区 -->
        <div>
          <div class="col-title">
            T212 社区 <n-tag size="small" :bordered="false">{{ activity.community.length }}</n-tag>
          </div>
          <div v-if="!activity.community.length" class="faint small">暂无社区帖子（运行 community 任务后填充）</div>
          <div v-for="(p, i) in activity.community" :key="i" class="item">
            <div class="item-head">
              <n-tag size="tiny" :type="sentType(p.sentiment)" :bordered="false">{{ sentLabel(p.sentiment) }}</n-tag>
              <span class="faint small">{{ p.author }}</span>
              <span v-if="p.likes > 0" class="amber small">♥ {{ p.likes }}</span>
            </div>
            <div class="muted item-content">{{ p.content }}</div>
            <div class="faint small">{{ fmtTs(p.published) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.act-panel {
  background: var(--panel2);
  border-radius: 6px;
  padding: 12px;
  margin: 4px 0;
}
.track-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 680px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
.col-title {
  font-size: 11px;
  color: var(--faint);
  margin-bottom: 6px;
  letter-spacing: 0.06em;
}
.item {
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
}
.item:last-child {
  border-bottom: none;
}
.item-head {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 2px;
}
.item-link {
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
  line-height: 1.4;
}
.item-link:hover {
  color: var(--amber);
}
.item-content {
  font-size: 12px;
  line-height: 1.4;
}
.small {
  font-size: 11px;
}
</style>
