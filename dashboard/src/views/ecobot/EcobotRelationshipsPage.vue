<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const activeTab = ref<'affinities' | 'events' | 'relationships'>('affinities');
const affinities = ref<EcobotRecord[]>([]);
const events = ref<EcobotRecord[]>([]);
const relationships = ref<EcobotRecord[]>([]);
const selectedUserId = ref('');
const selectedAffinity = ref<EcobotRecord | null>(null);
const saving = ref(false);
const notice = ref('');
const editForm = ref({
  affinity_score: 0,
  trust_score: 0,
  familiarity: 0,
  special_level: 'none' as 'none' | 'unforgivable' | 'supreme',
  reason: '',
});

const affinityColumns = [
  { key: 'user_id', title: 'QQ 号' },
  { key: 'current_nickname', title: '昵称' },
  { key: 'stage', title: '关系阶段' },
  { key: 'special_locked', title: '人工锁定' },
  { key: 'affinity_score', title: '好感' },
  { key: 'trust_score', title: '信任' },
  { key: 'familiarity', title: '熟悉度' },
  { key: 'irritation', title: '短期烦躁' },
  { key: 'interaction_count', title: '互动次数' },
  { key: 'last_reason', title: '最近变化原因' },
  { key: 'last_interaction_at', title: '最近互动' },
];
const eventColumns = [
  { key: 'user_id', title: 'QQ 号' },
  { key: 'raw_affinity_delta', title: 'AI评估变化' },
  { key: 'applied_affinity_delta', title: '好感变化' },
  { key: 'applied_trust_delta', title: '信任变化' },
  { key: 'familiarity_delta', title: '熟悉度变化' },
  { key: 'irritation_delta', title: '烦躁变化' },
  { key: 'confidence', title: '判断置信度' },
  { key: 'previous_stage', title: '原阶段' },
  { key: 'new_stage', title: '新阶段' },
  { key: 'reason', title: '变化理由' },
  { key: 'created_at', title: '时间' },
];
const relationshipColumns = [
  { key: 'person_a_qq_id', title: '人物 A' },
  { key: 'person_b_qq_id', title: '人物 B' },
  { key: 'current_relation_type', title: '关系' },
  { key: 'current_summary', title: '关系记忆' },
  { key: 'interaction_strength', title: '互动强度' },
  { key: 'reciprocity', title: '互惠度' },
  { key: 'confidence', title: '置信度' },
  { key: 'last_evaluated_at', title: '评估时间' },
];

const items = computed(() => {
  if (activeTab.value === 'affinities') return affinities.value;
  if (activeTab.value === 'events') return events.value;
  return relationships.value;
});
const columns = computed(() => {
  if (activeTab.value === 'affinities') return affinityColumns;
  if (activeTab.value === 'events') return eventColumns;
  return relationshipColumns;
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [affinities.value, events.value, relationships.value] = await Promise.all([
      ecobotApi.affinities(300),
      ecobotApi.affinityEvents(selectedUserId.value, 500),
      ecobotApi.relationships(200),
    ]);
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

async function showAffinityHistory(item: EcobotRecord) {
  selectedUserId.value = String(item.user_id || '');
  selectedAffinity.value = null;
  activeTab.value = 'events';
  await load();
}

function openAffinityEditor(item: EcobotRecord) {
  if (activeTab.value !== 'affinities') return;
  selectedAffinity.value = item;
  editForm.value = {
    affinity_score: Number(item.affinity_score || 0),
    trust_score: Number(item.trust_score || 0),
    familiarity: Number(item.familiarity || 0),
    special_level: String(item.special_level || 'none') as 'none' | 'unforgivable' | 'supreme',
    reason: '',
  };
}

function closeAffinityEditor() {
  if (saving.value) return;
  selectedAffinity.value = null;
}

async function saveAffinity() {
  if (!selectedAffinity.value) return;
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const userId = String(selectedAffinity.value.user_id || '');
    await ecobotApi.updateAffinity(userId, editForm.value);
    selectedAffinity.value = null;
    notice.value = `QQ ${userId} 的关系数值已保存`;
    await load();
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { saving.value = false; }
}

async function showAllEvents() {
  selectedUserId.value = '';
  activeTab.value = 'events';
  await load();
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="人物关系与好感" subtitle="区分机器人对人物的情感状态，以及群成员彼此的社会关系" :loading="loading" @refresh="load" />
    <v-tabs v-model="activeTab" color="primary" class="mb-4">
      <v-tab value="affinities" prepend-icon="mdi-account-heart-outline">机器人好感</v-tab>
      <v-tab value="events" prepend-icon="mdi-history">好感变化</v-tab>
      <v-tab value="relationships" prepend-icon="mdi-account-multiple-outline">人物关系</v-tab>
    </v-tabs>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" closable class="mb-5" @click:close="notice = ''">{{ notice }}</v-alert>
    <div v-if="activeTab === 'events' && selectedUserId" class="filter-line">
      <span>正在查看 QQ {{ selectedUserId }} 的变化记录</span>
      <v-btn variant="text" prepend-icon="mdi-filter-remove-outline" @click="showAllEvents">显示全部</v-btn>
    </div>
    <div :class="{ 'clickable-table': activeTab === 'affinities' }">
      <EcobotTable :columns="columns" :items="items" :loading="loading" @row-click="openAffinityEditor" />
    </div>

    <v-dialog :model-value="Boolean(selectedAffinity)" max-width="680" persistent @update:model-value="value => !value && closeAffinityEditor()">
      <v-card v-if="selectedAffinity">
        <v-card-title class="d-flex align-center">
          调整关系：{{ selectedAffinity.current_nickname || selectedAffinity.user_id }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" aria-label="关闭" :disabled="saving" @click="closeAffinityEditor" />
        </v-card-title>
        <v-card-subtitle>QQ {{ selectedAffinity.user_id }} · 当前阶段 {{ selectedAffinity.stage }}</v-card-subtitle>
        <v-card-text>
          <v-alert type="info" variant="tonal" class="mb-5">“罪大恶极”和“至高”只能在这里设置或解除。锁定后 AI 仍记录判断，但不能自动改变好感等级</v-alert>
          <div class="special-level mb-5">
            <label>特殊等级</label>
            <v-btn-toggle v-model="editForm.special_level" mandatory divided color="primary" variant="outlined">
              <v-btn value="none">普通</v-btn>
              <v-btn value="unforgivable">罪大恶极</v-btn>
              <v-btn value="supreme">至高</v-btn>
            </v-btn-toggle>
          </div>
          <div class="score-editor">
            <label>好感度</label>
            <v-slider v-model.number="editForm.affinity_score" :min="-100" :max="100" :step="0.1" thumb-label color="primary" hide-details />
            <v-text-field v-model.number="editForm.affinity_score" type="number" min="-100" max="100" step="0.1" density="compact" variant="outlined" hide-details />

            <label>信任度</label>
            <v-slider v-model.number="editForm.trust_score" :min="-100" :max="100" :step="1" thumb-label color="success" hide-details />
            <v-text-field v-model.number="editForm.trust_score" type="number" min="-100" max="100" density="compact" variant="outlined" hide-details />

            <label>熟悉度</label>
            <v-slider v-model.number="editForm.familiarity" :min="0" :max="100" :step="1" thumb-label color="warning" hide-details />
            <v-text-field v-model.number="editForm.familiarity" type="number" min="0" max="100" density="compact" variant="outlined" hide-details />
          </div>
          <v-textarea v-model="editForm.reason" class="mt-5" label="调整理由" placeholder="例如：修正导入数据、补录既有关系" maxlength="500" counter rows="2" auto-grow variant="outlined" />
        </v-card-text>
        <v-card-actions>
          <v-btn prepend-icon="mdi-history" variant="text" :disabled="saving" @click="showAffinityHistory(selectedAffinity)">查看变化记录</v-btn>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="closeAffinityEditor">取消</v-btn>
          <v-btn color="primary" prepend-icon="mdi-content-save" :loading="saving" @click="saveAffinity">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.clickable-table { cursor: pointer; }
.filter-line { align-items: center; display: flex; justify-content: space-between; margin-bottom: 12px; }
.filter-line span { color: rgba(var(--v-theme-on-surface), 0.68); font-size: 0.85rem; }
.special-level { align-items: center; display: flex; flex-wrap: wrap; gap: 12px; }
.special-level label { font-size: 0.9rem; font-weight: 600; }
.score-editor { align-items: center; display: grid; gap: 18px 16px; grid-template-columns: 72px minmax(180px, 1fr) 96px; }
.score-editor label { font-size: 0.9rem; font-weight: 600; }
@media (max-width: 620px) {
  .score-editor { grid-template-columns: 1fr 88px; }
  .score-editor label { grid-column: 1 / -1; }
}
</style>
