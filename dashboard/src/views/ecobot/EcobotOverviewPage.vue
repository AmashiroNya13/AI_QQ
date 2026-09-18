<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord, type EcobotStatus } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';

const loading = ref(false);
const error = ref('');
const status = ref<EcobotStatus | null>(null);
const state = ref<EcobotRecord | null>(null);

const countLabels: Record<string, string> = {
  qq_users: 'QQ 用户',
  qq_groups: 'QQ群',
  qq_messages: '已记录消息',
  qq_outbound_messages: '主动消息',
  qq_binary_assets: '媒体资源',
  qq_relationships: '人物关系',
  ecobot2_memory_episodes: '情节记忆',
  ecobot2_memory_retrievals: '主动重建',
  ecobot2_persona_increments: '人格增量',
  ecobot2_temporal_relations: '时间关系',
  ecobot2_grievances: '记仇记录',
};

const knownValues: Record<string, string> = {
  idle: '待机',
  calm: '平静',
  online: '在线',
  'quietly observing the current scene': '安静地观察当前场景',
};

const triggerNames: Record<string, string> = {
  initialize: '初始化',
  message: '收到消息',
  idle: '空闲驱动',
  time_due: '定时结束',
  plan: '行为规划',
};

function formatStateValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) return value.length ? value.map(String).join('、') : '-';
  return knownValues[String(value)] || String(value);
}

const metrics = computed(() =>
  Object.entries(status.value?.counts || {}).map(([key, value]) => ({
    key,
    label: countLabels[key] || key,
    value,
  })),
);

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [status.value, state.value] = await Promise.all([
      ecobotApi.status(),
      ecobotApi.state(),
    ]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="Ecobot 总览" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>

    <div class="metric-grid">
      <article v-for="metric in metrics" :key="metric.key" class="metric-item">
        <span>{{ metric.label }}</span>
        <strong>{{ metric.value.toLocaleString() }}</strong>
      </article>
    </div>

    <section class="detail-section">
      <h2>当前人物状态</h2>
      <div v-if="state" class="state-grid">
        <div><span>活动</span><strong>{{ formatStateValue(state.activity) }}</strong></div>
        <div><span>行为</span><strong>{{ formatStateValue(state.behavior) }}</strong></div>
        <div><span>场景</span><strong>{{ formatStateValue(state.scene) }}</strong></div>
        <div><span>地点</span><strong>{{ formatStateValue(state.location) }}</strong></div>
        <div><span>情绪</span><strong>{{ formatStateValue(state.mood) }}</strong></div>
        <div><span>目标</span><strong>{{ formatStateValue(state.goal) }}</strong></div>
      </div>
      <p v-else class="empty-text">尚未形成状态记录</p>
    </section>

    <section class="detail-section">
      <h2>最近行为批次</h2>
      <div v-if="status?.latest_batch" class="batch-line">
        <strong>{{ triggerNames[String(status.latest_batch.trigger)] || status.latest_batch.trigger || '-' }}</strong>
        <span>{{ status.latest_batch.channel_id || '-' }}</span>
        <span>{{ status.latest_batch.status || '-' }}</span>
        <span>{{ status.latest_batch.stop_reason || '-' }}</span>
      </div>
      <p v-else class="empty-text">暂无行为批次</p>
    </section>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.metric-grid {
  display: grid;
  gap: 1px;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  background: rgba(var(--v-theme-on-surface), 0.12);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 6px;
  overflow: hidden;
}
.metric-item { background: rgb(var(--v-theme-surface)); min-height: 94px; padding: 16px; }
.metric-item span, .state-grid span { color: rgba(var(--v-theme-on-surface), 0.58); display: block; font-size: .78rem; }
.metric-item strong { display: block; font-size: 1.65rem; margin-top: 8px; }
.detail-section { border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12); margin-top: 28px; padding-top: 20px; }
h2 { font-size: 1rem; font-weight: 650; letter-spacing: 0; margin: 0 0 14px; }
.state-grid { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.state-grid strong { display: block; margin-top: 5px; overflow-wrap: anywhere; }
.batch-line { align-items: center; display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.batch-line span, .empty-text { color: rgba(var(--v-theme-on-surface), 0.58); }
@media (max-width: 720px) { .batch-line { grid-template-columns: 1fr; } }
</style>
