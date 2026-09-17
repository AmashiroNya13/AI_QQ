<script setup lang="ts">
import type { EcobotRecord } from '@/api/ecobot';

defineProps<{
  columns: Array<{ key: string; title: string }>;
  items: EcobotRecord[];
  loading?: boolean;
  emptyText?: string;
}>();

defineEmits<{
  rowClick: [item: EcobotRecord];
}>();

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
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
            <span class="cell-value">{{ displayValue(item[column.key]) }}</span>
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
