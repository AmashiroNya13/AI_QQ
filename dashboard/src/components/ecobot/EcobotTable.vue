<script setup lang="ts">
import type { EcobotRecord } from '@/api/ecobot';

type EcobotColumn = {
  key: string;
  title: string;
  format?: (value: unknown, item: EcobotRecord) => string;
};

defineProps<{
  columns: EcobotColumn[];
  items: EcobotRecord[];
  loading?: boolean;
  emptyText?: string;
}>();

defineEmits<{
  rowClick: [item: EcobotRecord];
}>();

const labels: Record<string, string> = {
  time_tick: '时间推进',
  scene_changed: '场景变化',
  intent_created: '产生意图',
  world_resolution: '世界解析',
  action_attempt: '开始行动',
  action_receipt: '行动回执',
  consequence: '行动后果',
  appraisal: '情绪评价',
  attention_decision: '注意力判断',
  experience_review: '经历复盘',
  identity_revision: '人格演化候选',
  stimulus_received: '收到刺激',
  platform_notice: '平台通知',
  intent_suppressed: '重复意图已冷却',
  planned: '已计划',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  blocked: '被阻止',
  needs_preparation: '需要准备',
  unknown: '未知',
  start_activity: '开始活动',
  send_expression: '发送表达',
  go_to: '前往地点',
  world_action_applied: '世界行动生效',
  message_delivered: '消息已送达',
  delivery_failed: '消息未送达',
  home: '家',
  library: '图书馆',
  street: '街道',
  main: '主体主场景',
  candidate: '候选',
  active: '已生效',
  pending: '待处理',
};

const shortIdKeys = new Set([
  'event_id', 'attempt_id', 'intent_id', 'batch_id', 'decision_id',
  'consequence_id', 'revision_id', 'thread_id', 'source_event_id',
]);

function formatDate(value: unknown): string {
  if (!value) return '-';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}

function formatNumber(key: string, value: number): string {
  if (key === 'valence') return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`;
  if (key.includes('confidence') || ['priority', 'intensity', 'salience', 'attention_cost', 'relevance', 'controllability'].includes(key)) {
    return `${Math.round(value * 100)}%`;
  }
  if (['energy', 'attention_load'].includes(key)) return `${value.toFixed(1)} / 100`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function formatId(key: string, value: string): string {
  if (key === 'channel_id') {
    const match = value.match(/(?:GroupMessage|group)[:：](.+)$/i);
    if (match) return `QQ群 ${match[1]}`;
    const privateMatch = value.match(/(?:FriendMessage|friend)[:：](.+)$/i);
    if (privateMatch) return `QQ私聊 ${privateMatch[1]}`;
  }
  if (shortIdKeys.has(key) && value.length > 16) return `${value.slice(0, 8)}…${value.slice(-4)}`;
  return labels[value] || value;
}

function parseJson(value: unknown): unknown {
  if (typeof value !== 'string' || !value.trim()) return value;
  try { return JSON.parse(value); } catch { return value; }
}

function objectSummary(value: unknown, depth = 0): string {
  if (value === null || value === undefined || value === '') return '-';
  if (depth > 2) return String(value);
  if (Array.isArray(value)) return value.map((item) => objectSummary(item, depth + 1)).join('、') || '-';
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}：${formatValue(item, key, depth + 1)}`)
      .join('；') || '-';
  }
  return String(value);
}

function formatValue(value: unknown, key = '', depth = 0): string {
  if (value === null || value === undefined || value === '') return '-';
  if (key.endsWith('_at') || key === 'local_time' || key === 'observed_at' || key === 'occurred_at') return formatDate(value);
  const parsed = key.endsWith('_json') || ['payload', 'arguments', 'resolution', 'proposal', 'evidence', 'reactions', 'conditions', 'object', 'attributes'].includes(key)
    ? parseJson(value) : value;
  if (parsed !== value || typeof parsed === 'object') return objectSummary(parsed, depth);
  if (typeof parsed === 'boolean') return parsed ? '是' : '否';
  if (typeof parsed === 'number') return formatNumber(key, parsed);
  if (typeof parsed === 'string') {
    if (shortIdKeys.has(key) || key === 'channel_id' || key === 'location_id' || key === 'status' || key === 'kind' || key === 'action_type' || key === 'trigger') {
      return formatId(key, parsed);
    }
    return labels[parsed] || parsed;
  }
  return String(parsed);
}

function displayValue(value: unknown, key: string, item: EcobotRecord, formatter?: EcobotColumn['format']): string {
  return formatter ? formatter(value, item) : formatValue(value, key);
}
</script>

<template>
  <div class="ecobot-table-wrap">
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <v-table density="compact" hover>
      <thead>
        <tr>
          <th v-for="column in columns" :key="column.key">{{ column.title }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, index) in items" :key="String(item.id ?? item.qq_id ?? item.group_id ?? item.batch_id ?? index)" @click="$emit('rowClick', item)">
          <td v-for="column in columns" :key="column.key">
            <span class="cell-value">{{ displayValue(item[column.key], column.key, item, column.format) }}</span>
          </td>
        </tr>
        <tr v-if="!loading && items.length === 0">
          <td :colspan="columns.length" class="empty-cell">{{ emptyText || '暂无数据' }}</td>
        </tr>
      </tbody>
    </v-table>
  </div>
</template>

<style scoped>
.ecobot-table-wrap {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 6px;
  max-width: 100%;
  overflow: auto;
}

th {
  background: rgb(var(--v-theme-surface));
  color: rgba(var(--v-theme-on-surface), 0.68);
  font-size: 0.75rem;
  position: sticky;
  top: 0;
  white-space: nowrap;
  z-index: 1;
}

td {
  max-width: 360px;
  min-width: 120px;
  vertical-align: top;
}

.cell-value {
  display: block;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.empty-cell {
  color: rgba(var(--v-theme-on-surface), 0.5);
  padding: 32px !important;
  text-align: center;
}
</style>
