<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const activeTab = ref<'batches' | 'actions' | 'traces'>('batches');
const loading = ref(false);
const error = ref('');
const batches = ref<EcobotRecord[]>([]);
const actions = ref<EcobotRecord[]>([]);
const traces = ref<EcobotRecord[]>([]);
const selectedTrace = ref<EcobotRecord | null>(null);

const valueLabels: Record<string, string> = {
  message: '消息触发',
  idle: '空闲心跳',
  observe: '观察',
  analyze_infer: '分析推断',
  desire: '欲望判断',
  plan: '行为规划',
  reflect: '结果反思',
  waiting: '等待',
  observing: '观察中',
  inferring: '推断中',
  desiring: '判断欲望中',
  planning: '规划中',
  acting: '执行中',
  reflecting: '反思中',
  completed: '已完成',
  success: '成功',
  failed: '失败',
  error: '错误',
  silent_by_desire: '欲望判断决定保持沉默',
  private_cognition_blocked: '检测到内部思考，已阻止发送',
  action_budget_exhausted: '工具动作次数达到上限',
  action_round_budget_exhausted: '动作轮数达到上限',
  reflection_could_not_replan: '反思阶段无法继续规划',
};

function localizedRows(rows: EcobotRecord[]): EcobotRecord[] {
  return rows.map((row) => Object.fromEntries(
    Object.entries(row).map(([key, value]) => [
      key,
      typeof value === 'string' && valueLabels[value] ? valueLabels[value] : value,
    ]),
  ));
}

const items = computed(() => {
  if (activeTab.value === 'batches') return localizedRows(batches.value);
  if (activeTab.value === 'actions') return localizedRows(actions.value);
  return localizedRows(traces.value);
});
const columns = computed(() => {
  if (activeTab.value === 'batches') return [
    { key: 'batch_number', title: '序号' }, { key: 'channel_id', title: '频道' },
    { key: 'trigger', title: '触发方式' }, { key: 'priority', title: '优先级' },
    { key: 'status', title: '状态' }, { key: 'stop_reason', title: '停止原因' },
    { key: 'created_at', title: '创建时间' },
  ];
  if (activeTab.value === 'actions') return [
    { key: 'action_type', title: '动作类型' }, { key: 'description', title: '动作内容' },
    { key: 'scene', title: '场景' }, { key: 'status', title: '状态' },
    { key: 'started_at', title: '开始时间' }, { key: 'completed_at', title: '完成时间' },
    { key: 'completion_reason', title: '完成原因' },
  ];
  return [
    { key: 'phase', title: 'AI 阶段' }, { key: 'provider_id', title: '模型' },
    { key: 'status', title: '状态' }, { key: 'duration_ms', title: '耗时(ms)' },
    { key: 'batch_id', title: '思考批次' }, { key: 'created_at', title: '时间' },
  ];
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [batches.value, actions.value, traces.value] = await Promise.all([
      ecobotApi.batches(200), ecobotApi.actions(200), ecobotApi.traces(300),
    ]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

function display(value: unknown) {
  if (value === null || value === undefined || value === '') return '未记录';
  if (typeof value === 'string' && valueLabels[value]) return valueLabels[value];
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="思考与行为记录" subtitle="检查批次决策、工具动作和每一次模型阶段调用" :loading="loading" @refresh="load" />
    <v-tabs v-model="activeTab" color="primary" class="mb-4">
      <v-tab value="batches" prepend-icon="mdi-source-branch">思考批次</v-tab>
      <v-tab value="actions" prepend-icon="mdi-run-fast">行为动作</v-tab>
      <v-tab value="traces" prepend-icon="mdi-text-box-search-outline">AI 阶段追踪</v-tab>
    </v-tabs>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <div :class="{ 'trace-table': activeTab === 'traces' }">
      <EcobotTable :columns="columns" :items="items" :loading="loading" :empty-text="activeTab === 'traces' ? '尚无 AI 阶段调用记录' : '暂无数据'" @row-click="item => activeTab === 'traces' && (selectedTrace = item)" />
    </div>

    <v-dialog :model-value="Boolean(selectedTrace)" max-width="900" scrollable @update:model-value="value => !value && (selectedTrace = null)">
      <v-card v-if="selectedTrace">
        <v-card-title class="d-flex align-center">
          AI 阶段详情：{{ display(selectedTrace.phase) }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" aria-label="关闭" @click="selectedTrace = null" />
        </v-card-title>
        <v-card-text>
          <div class="trace-meta">
            <span>模型：{{ display(selectedTrace.provider_id) }}</span>
            <span>状态：{{ display(selectedTrace.status) }}</span>
            <span>耗时：{{ display(selectedTrace.duration_ms) }} ms</span>
            <span>批次：{{ display(selectedTrace.batch_id) }}</span>
          </div>
          <h3>系统提示词</h3><pre>{{ display(selectedTrace.system_prompt) }}</pre>
          <h3>阶段输入</h3><pre>{{ display(selectedTrace.input_prompt) }}</pre>
          <h3>模型输出</h3><pre>{{ display(selectedTrace.output_text) }}</pre>
          <template v-if="selectedTrace.error"><h3>错误</h3><pre class="error-text">{{ display(selectedTrace.error) }}</pre></template>
        </v-card-text>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.trace-table { cursor: pointer; }
.trace-meta { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-bottom: 24px; color: rgba(var(--v-theme-on-surface), 0.68); font-size: 0.84rem; }
h3 { font-size: 0.9rem; letter-spacing: 0; margin: 20px 0 8px; }
pre { background: rgba(var(--v-theme-on-surface), 0.055); border: 1px solid rgba(var(--v-theme-on-surface), 0.1); border-radius: 6px; font-family: ui-monospace, Consolas, monospace; font-size: 0.8rem; line-height: 1.55; margin: 0; max-height: 320px; overflow: auto; padding: 14px; white-space: pre-wrap; word-break: break-word; }
.error-text { color: rgb(var(--v-theme-error)); }
</style>
