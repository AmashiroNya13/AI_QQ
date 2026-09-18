<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const tab = ref<'episodes' | 'retrievals' | 'persona' | 'relations' | 'policies' | 'grievances'>('episodes');
const episodes = ref<EcobotRecord[]>([]);
const retrievals = ref<EcobotRecord[]>([]);
const persona = ref<EcobotRecord[]>([]);
const relations = ref<EcobotRecord[]>([]);
const policies = ref<EcobotRecord[]>([]);
const grievances = ref<EcobotRecord[]>([]);
const columns = {
  episodes: [
    { key: 'event_kind', title: '情节类型' }, { key: 'subject_id', title: '主体' },
    { key: 'summary', title: '情节摘要' }, { key: 'importance', title: '重要度' },
    { key: 'started_at', title: '发生时间' },
  ],
  retrievals: [
    { key: 'stop_reason', title: '停止原因' }, { key: 'max_hops', title: '展开层数' },
    { key: 'episode_ids', title: '召回情节' }, { key: 'actions', title: '检索动作' },
    { key: 'duration_ms', title: '耗时' }, { key: 'created_at', title: '检索时间' },
  ],
  persona: [
    { key: 'category', title: '人格类别' }, { key: 'statement', title: '人格增量' },
    { key: 'status', title: '状态' }, { key: 'confidence', title: '置信度' },
    { key: 'stability', title: '稳定度' }, { key: 'evidence', title: '证据' },
  ],
  relations: [
    { key: 'subject_id', title: '主体' }, { key: 'predicate', title: '关系' },
    { key: 'object_id', title: '对象' }, { key: 'object', title: '对象内容' },
    { key: 'valid_from', title: '生效时间' }, { key: 'valid_to', title: '结束时间' },
    { key: 'confidence', title: '置信度' },
  ],
  policies: [
    { key: 'policy_name', title: '策略' }, { key: 'version', title: '版本' },
    { key: 'status', title: '状态' }, { key: 'config', title: '配置' },
    { key: 'score', title: '评估分数' }, { key: 'updated_at', title: '更新时间' },
  ],
  grievances: [
    { key: 'target_id', title: '可能相关对象' }, { key: 'reason', title: '原因' },
    { key: 'responsibility_confidence', title: '归因置信度' }, { key: 'intensity', title: '强度' },
    { key: 'repair_expected', title: '修复期待' }, { key: 'repetition_count', title: '重复次数' },
    { key: 'status', title: '状态' }, { key: 'last_triggered_at', title: '最近触发' },
  ],
};

const items = computed(() => ({
  episodes: episodes.value,
  retrievals: retrievals.value,
  persona: persona.value,
  relations: relations.value,
  policies: policies.value,
  grievances: grievances.value,
}[tab.value]));

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [episodes.value, retrievals.value, persona.value, relations.value, policies.value, grievances.value] = await Promise.all([
      ecobotApi.memoryEpisodes(300), ecobotApi.memoryRetrievals(200),
      ecobotApi.personaIncrements('', 200), ecobotApi.temporalRelations('', 300), ecobotApi.memoryPolicies(100), ecobotApi.grievances('', 200),
    ]);
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="记忆系统" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <v-tabs v-model="tab" color="primary" class="mb-4">
      <v-tab value="episodes">情节记忆</v-tab>
      <v-tab value="retrievals">主动重建记录</v-tab>
      <v-tab value="persona">人格空地</v-tab>
      <v-tab value="relations">时间关系</v-tab>
      <v-tab value="policies">记忆策略</v-tab>
      <v-tab value="grievances">记仇与归因</v-tab>
    </v-tabs>
    <v-alert v-if="tab === 'persona'" type="info" variant="tonal" class="mb-4">
      候选人格会先留在数据库里，达到配置的重复证据与置信度后才进入当前人格空地；基础人格不会被自动覆盖
    </v-alert>
    <EcobotTable :columns="columns[tab]" :items="items" :loading="loading" empty-text="当前还没有可展示的记忆记录" />
  </v-container>
</template>

<style scoped>.ecobot-page { max-width: 1440px; }</style>
