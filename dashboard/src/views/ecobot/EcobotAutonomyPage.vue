<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const tab = ref<'events' | 'actions' | 'consequences' | 'appraisals' | 'scenes' | 'intentions' | 'attention' | 'revisions' | 'threads' | 'obligations' | 'beliefs' | 'entities' | 'capabilities' | 'schedules' | 'rules' | 'expansions'>('events');
const events = ref<EcobotRecord[]>([]);
const actions = ref<EcobotRecord[]>([]);
const consequences = ref<EcobotRecord[]>([]);
const appraisals = ref<EcobotRecord[]>([]);
const scenes = ref<EcobotRecord[]>([]);
const intentions = ref<EcobotRecord[]>([]);
const attention = ref<EcobotRecord[]>([]);
const revisions = ref<EcobotRecord[]>([]);
const actionBusy = ref(false);
const actionMessage = ref('');
const threads = ref<EcobotRecord[]>([]);
const obligations = ref<EcobotRecord[]>([]);
const beliefs = ref<EcobotRecord[]>([]);
const entities = ref<EcobotRecord[]>([]);
const capabilities = ref<EcobotRecord[]>([]);
const schedules = ref<EcobotRecord[]>([]);
const rules = ref<EcobotRecord[]>([]);
const expansions = ref<EcobotRecord[]>([]);

const columns = {
  events: [
    { key: 'kind', title: '事件' }, { key: 'event_id', title: '事件 ID' },
    { key: 'actor_id', title: '主体' }, { key: 'target_id', title: '目标' }, { key: 'occurred_at', title: '时间' },
  ],
  actions: [
    { key: 'action_type', title: '行动' }, { key: 'status', title: '状态' },
    { key: 'intent_id', title: '意图' }, { key: 'error_code', title: '错误类型' }, { key: 'started_at', title: '开始时间' },
  ],
  consequences: [
    { key: 'kind', title: '后果' }, { key: 'attempt_id', title: '行动尝试' },
    { key: 'actor_id', title: '归因主体' }, { key: 'confidence', title: '置信度' }, { key: 'observed_at', title: '观察时间' },
  ],
  appraisals: [
    { key: 'emotion', title: '情绪' }, { key: 'valence', title: '效价' },
    { key: 'agency_confidence', title: '归因置信度' }, { key: 'intensity', title: '强度' }, { key: 'reason', title: '评价理由' },
  ],
  scenes: [
    { key: 'scene_id', title: '场景' }, { key: 'location_id', title: '地点' },
    { key: 'activity', title: '当前活动' }, { key: 'occupants', title: '在场者' }, { key: 'updated_at', title: '更新时间' },
  ],
  intentions: [
    { key: 'action_type', title: '意图' }, { key: 'status', title: '状态' },
    { key: 'priority', title: '优先级' }, { key: 'reason', title: '原因' }, { key: 'updated_at', title: '更新时间' },
  ],
  attention: [
    { key: 'channel_id', title: '频道' }, { key: 'addressee_id', title: '指向' },
    { key: 'should_reply', title: '是否回复' }, { key: 'confidence', title: '置信度' }, { key: 'reason', title: '判断' },
  ],
  revisions: [
    { key: 'category', title: '演化类别' }, { key: 'status', title: '状态' },
    { key: 'confidence', title: '置信度' }, { key: 'proposal', title: '候选改变' }, { key: 'created_at', title: '创建时间' },
  ],
  threads: [
    { key: 'channel_id', title: '频道' }, { key: 'topic', title: '话题' },
    { key: 'participants', title: '参与者' }, { key: 'salience', title: '显著度' }, { key: 'status', title: '状态' },
  ],
  obligations: [
    { key: 'kind', title: '义务' }, { key: 'source_event_id', title: '来源事件' },
    { key: 'strength', title: '强度' }, { key: 'status', title: '状态' }, { key: 'reason', title: '原因' },
  ],
  beliefs: [
    { key: 'subject', title: '主体' }, { key: 'predicate', title: '事实' },
    { key: 'object', title: '内容' }, { key: 'confidence', title: '置信度' }, { key: 'status', title: '状态' },
  ],
  entities: [
    { key: 'entity_kind', title: '类型' }, { key: 'display_name', title: '名称' },
    { key: 'version', title: '版本' }, { key: 'first_seen_at', title: '首次出现' }, { key: 'last_seen_at', title: '最近出现' },
  ],
  capabilities: [
    { key: 'capability_name', title: '能力' }, { key: 'description', title: '说明' },
    { key: 'available', title: '可用' }, { key: 'requirements', title: '条件' },
  ],
  schedules: [
    { key: 'title', title: '世界事实' }, { key: 'location_id', title: '地点' },
    { key: 'activity', title: '活动' }, { key: 'start_at', title: '开始' }, { key: 'end_at', title: '结束' },
  ],
  rules: [
    { key: 'name', title: '规则' }, { key: 'trigger_action_type', title: '触发行动' },
    { key: 'conditions', title: '条件' }, { key: 'reactions', title: '世界反应' }, { key: 'priority', title: '优先级' },
  ],
  expansions: [
    { key: 'entity_id', title: '拟扩展实体' }, { key: 'description', title: '说明' },
    { key: 'requested_by_intent_id', title: '来源意图' }, { key: 'status', title: '状态' }, { key: 'created_at', title: '创建时间' },
  ],
};

const items = computed(() => ({
  events: events.value, actions: actions.value, consequences: consequences.value, appraisals: appraisals.value,
  scenes: scenes.value, intentions: intentions.value, attention: attention.value, revisions: revisions.value,
  threads: threads.value, obligations: obligations.value, beliefs: beliefs.value, entities: entities.value,
  capabilities: capabilities.value, schedules: schedules.value, rules: rules.value, expansions: expansions.value,
}[tab.value]));

async function load() {
  loading.value = true;
  error.value = '';
  try {
    [events.value, actions.value, consequences.value, appraisals.value, scenes.value, intentions.value, attention.value, revisions.value, threads.value, obligations.value, beliefs.value, entities.value, capabilities.value, schedules.value, rules.value, expansions.value] = await Promise.all([
      ecobotApi.autonomyEvents(300), ecobotApi.autonomyActions(300),
      ecobotApi.autonomyConsequences(300), ecobotApi.autonomyAppraisals(300),
      ecobotApi.autonomyScenes(100), ecobotApi.autonomyIntentions('', 300),
      ecobotApi.autonomyAttention(300), ecobotApi.autonomyRevisions('', 300),
      ecobotApi.autonomyThreads('', 300), ecobotApi.autonomyObligations('pending', 300),
      ecobotApi.autonomyBeliefs('', 300), ecobotApi.autonomyWorldEntities('', 300),
      ecobotApi.autonomyCapabilities(300), ecobotApi.autonomyScheduleFacts(300),
      ecobotApi.autonomyWorldRules(), ecobotApi.autonomySceneExpansions(300),
    ]);
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

onMounted(load);

async function advanceLife() {
  actionBusy.value = true;
  actionMessage.value = '';
  try {
    const result = await ecobotApi.advanceAutonomyTick();
    actionMessage.value = `生命循环已推进：${String(result.resolution_status || '主体等待')}`;
    await load();
  }
  catch (reason) { actionMessage.value = reason instanceof Error ? reason.message : String(reason); }
  finally { actionBusy.value = false; }
}
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="自治闭环" subtitle="查看感知、行动、回执、后果和情绪评价如何串成持续行为" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <v-alert v-if="actionMessage" type="info" variant="tonal" class="mb-5">{{ actionMessage }}</v-alert>
    <div class="d-flex ga-3 mb-5">
      <v-btn color="primary" variant="tonal" :loading="actionBusy" @click="advanceLife">
        手动推进一次生命循环
      </v-btn>
      <span class="text-medium-emphasis align-self-center">这不会直接发送消息，只会推进主体与世界状态</span>
    </div>
    <v-tabs v-model="tab" color="primary" class="mb-4">
      <v-tab value="events">环境事件</v-tab>
      <v-tab value="actions">行动尝试</v-tab>
      <v-tab value="consequences">行动后果</v-tab>
      <v-tab value="appraisals">情绪评价</v-tab>
      <v-tab value="scenes">生活场景</v-tab>
      <v-tab value="intentions">开放意图</v-tab>
      <v-tab value="attention">注意力判断</v-tab>
      <v-tab value="revisions">人格演化候选</v-tab>
      <v-tab value="threads">对话线程</v-tab>
      <v-tab value="obligations">互动义务</v-tab>
      <v-tab value="beliefs">主观信念</v-tab>
      <v-tab value="entities">世界实体</v-tab>
      <v-tab value="capabilities">世界能力</v-tab>
      <v-tab value="schedules">世界事实</v-tab>
      <v-tab value="rules">世界规则</v-tab>
      <v-tab value="expansions">场景扩展</v-tab>
    </v-tabs>
    <EcobotTable :columns="columns[tab]" :items="items" :loading="loading" empty-text="自治内核尚无记录" />
  </v-container>
</template>

<style scoped>.ecobot-page { max-width: 1440px; }</style>
