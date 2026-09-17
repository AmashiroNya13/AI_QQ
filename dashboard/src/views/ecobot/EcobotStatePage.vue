<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const state = ref<EcobotRecord | null>(null);
const history = ref<EcobotRecord[]>([]);
const stateFields = [
  { key: 'activity', label: '当前活动' },
  { key: 'behavior', label: '具体行为' },
  { key: 'scene', label: '所在场景' },
  { key: 'location', label: '位置' },
  { key: 'focus', label: '关注对象' },
  { key: 'companions', label: '同行者' },
  { key: 'mood', label: '情绪' },
  { key: 'energy', label: '精力' },
  { key: 'hunger', label: '饥饿度' },
  { key: 'fatigue', label: '疲劳度' },
  { key: 'social_drive', label: '社交意愿' },
  { key: 'goal', label: '当前目标' },
  { key: 'activity_started_at', label: '活动开始时间' },
  { key: 'expected_end_at', label: '预计结束时间' },
  { key: 'updated_at', label: '更新时间' },
];
const columns = [
  { key: 'version', title: '版本' },
  { key: 'trigger', title: '触发方式' },
  { key: 'reason', title: '变化原因' },
  { key: 'batch_id', title: '批次' },
  { key: 'changed_at', title: '变化时间' },
  { key: 'next_state_json', title: '状态快照' },
];

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

function formatDate(value: unknown): string {
  if (!value) return '-';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}

function formatStateValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (['updated_at', 'activity_started_at', 'expected_end_at'].includes(key)) return formatDate(value);
  if (Array.isArray(value)) return value.length ? value.map(String).join('、') : '-';
  return knownValues[String(value)] || String(value);
}

function formatSnapshot(value: unknown): string {
  let snapshot: Record<string, unknown>;
  try {
    snapshot = typeof value === 'string' ? JSON.parse(value) : (value as Record<string, unknown>);
  } catch {
    return String(value || '-');
  }
  if (!snapshot || typeof snapshot !== 'object') return '-';
  return stateFields
    .filter(({ key }) => snapshot[key] !== null && snapshot[key] !== undefined && snapshot[key] !== '')
    .map(({ key, label }) => `${label}：${formatStateValue(key, snapshot[key])}`)
    .join('\n');
}

const localizedHistory = computed(() => history.value.map((item) => ({
  ...item,
  trigger: triggerNames[String(item.trigger)] || item.trigger,
  changed_at: formatDate(item.changed_at),
  next_state_json: formatSnapshot(item.next_state_json),
})));

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [state.value, history.value] = await Promise.all([
      ecobotApi.state(),
      ecobotApi.stateHistory(),
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
    <EcobotPageHeader title="人物状态" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <section class="state-strip">
      <div v-for="field in stateFields" :key="field.key">
        <span>{{ field.label }}</span><strong>{{ formatStateValue(field.key, state?.[field.key]) }}</strong>
      </div>
    </section>
    <h2>状态变化历史</h2>
    <EcobotTable :columns="columns" :items="localizedHistory" :loading="loading" />
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.state-strip { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 30px; }
.state-strip span { color: rgba(var(--v-theme-on-surface), .56); display: block; font-size: .75rem; }
.state-strip strong { display: block; margin-top: 5px; overflow-wrap: anywhere; }
h2 { font-size: 1rem; letter-spacing: 0; margin: 0 0 12px; }
</style>
