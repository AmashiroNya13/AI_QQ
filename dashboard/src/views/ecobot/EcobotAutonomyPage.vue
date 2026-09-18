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

const tabDescriptions: Record<string, string> = {
  events: '这里记录世界接收到的刺激和内部变化；展开“发生了什么”可以看到具体内容，不再只看内部编号。',
  actions: '行动是主体已经尝试执行的事情；“行动内容”显示地点、活动或消息正文，“成功”表示执行层完成。',
  consequences: '后果是行动之后世界返回的事实；这里的“事实确认度”不是 AI 自信，而是平台或世界是否明确反馈。',
  appraisals: '情绪评价是主体对后果的主观评价；相关度、正负感受、可控程度和情绪强度分别表示不同维度。',
  scenes: '场景是主体当前所在生活空间的快照，包含时间、地点、活动、物品和版本变化。',
  intentions: '意图是主体想做的事，不等于已经做到；“具体内容”用于区分不同的活动、目标和参数。',
  attention: '注意力判断只是预筛选，不会替主体直接决定回复；主体还会结合线程、关系、欲望和人格继续判断。',
  revisions: '人格演化候选是经历留下的可能改变，只有证据持续积累后才会进入稳定人格。',
};

const tabDescription = computed(() => tabDescriptions[tab.value] || '这里展示自治闭环的一部分记录。');

const columns = {
  events: [
    { key: 'kind', title: '事件' }, { key: 'event_id', title: '事件 ID' },
    { key: 'actor_id', title: '主体' }, { key: 'target_id', title: '目标' },
    { key: 'payload', title: '发生了什么' }, { key: 'occurred_at', title: '时间' },
  ],
  actions: [
    { key: 'action_type', title: '行动' }, { key: 'status', title: '状态' },
    { key: 'intent_id', title: '意图' }, { key: 'arguments', title: '行动内容' },
    { key: 'error_code', title: '错误类型' }, { key: 'started_at', title: '开始时间' }, { key: 'completed_at', title: '完成时间' },
  ],
  consequences: [
    { key: 'kind', title: '后果' }, { key: 'attempt_id', title: '行动尝试' },
    { key: 'actor_id', title: '归因主体' }, { key: 'confidence', title: '事实确认度' },
    { key: 'payload', title: '反馈内容' }, { key: 'observed_at', title: '观察时间' },
  ],
  appraisals: [
    { key: 'emotion', title: '情绪' }, { key: 'relevance', title: '与主体相关度' },
    { key: 'valence', title: '正负感受' }, { key: 'controllability', title: '可控程度' },
    { key: 'agency_confidence', title: '归因把握' }, { key: 'intensity', title: '情绪强度' }, { key: 'reason', title: '评价理由' },
  ],
  scenes: [
    { key: 'scene_id', title: '场景' }, { key: 'location_id', title: '地点' },
    { key: 'local_time', title: '场景时间' }, { key: 'activity', title: '当前活动' },
    { key: 'occupants', title: '在场者' }, { key: 'objects', title: '场景物品' }, { key: 'version', title: '场景版本' }, { key: 'updated_at', title: '更新时间' },
  ],
  intentions: [
    { key: 'action_type', title: '意图' }, { key: 'status', title: '状态' },
    { key: 'target_id', title: '目标' }, { key: 'arguments', title: '具体内容' },
    { key: 'priority', title: '优先级' }, { key: 'reason', title: '主体理由' }, { key: 'updated_at', title: '更新时间' },
  ],
  attention: [
    { key: 'channel_id', title: '频道' }, { key: 'addressee_id', title: '指向' },
    { key: 'thread_id', title: '对话线程' }, { key: 'should_reply', title: '预筛选建议' },
    { key: 'confidence', title: '指向判断把握' }, { key: 'attention_cost', title: '注意力成本' }, { key: 'reason', title: '判断依据' },
  ],
  revisions: [
    { key: 'category', title: '演化类别' }, { key: 'status', title: '状态' },
    { key: 'confidence', title: '证据把握' }, { key: 'proposal', title: '候选改变' }, { key: 'evidence', title: '依据' }, { key: 'created_at', title: '创建时间' },
  ],
  threads: [
    { key: 'channel_id', title: '频道' }, { key: 'topic', title: '话题' },
    { key: 'participants', title: '参与者' }, { key: 'addressee_id', title: '指向' }, { key: 'salience', title: '显著度' }, { key: 'status', title: '状态' },
  ],
  obligations: [
    { key: 'kind', title: '义务' }, { key: 'source_event_id', title: '来源事件' },
    { key: 'strength', title: '强度' }, { key: 'status', title: '状态' }, { key: 'reason', title: '原因' },
  ],
  beliefs: [
    { key: 'subject', title: '主体' }, { key: 'predicate', title: '事实' },
    { key: 'object', title: '内容' }, { key: 'confidence', title: '事实把握' }, { key: 'status', title: '状态' },
  ],
  entities: [
    { key: 'entity_kind', title: '类型' }, { key: 'display_name', title: '名称' },
    { key: 'attributes', title: '已知属性' }, { key: 'version', title: '版本' }, { key: 'first_seen_at', title: '首次出现' }, { key: 'last_seen_at', title: '最近出现' },
  ],
  capabilities: [
    { key: 'capability_name', title: '能力' }, { key: 'description', title: '说明' },
    { key: 'available', title: '可用' }, { key: 'requirements', title: '条件' },
  ],
  schedules: [
    { key: 'title', title: '世界事实' }, { key: 'location_id', title: '地点' },
    { key: 'activity', title: '活动' }, { key: 'actors', title: '涉及人物' }, { key: 'reactions', title: '可能反应' }, { key: 'start_at', title: '开始' }, { key: 'end_at', title: '结束' },
  ],
  rules: [
    { key: 'name', title: '规则' }, { key: 'trigger_action_type', title: '触发行动' },
    { key: 'conditions', title: '条件' }, { key: 'reactions', title: '世界反应' }, { key: 'priority', title: '优先级' },
  ],
  expansions: [
    { key: 'entity_id', title: '拟扩展实体' }, { key: 'description', title: '说明' },
    { key: 'requested_by_intent_id', title: '来源意图' }, { key: 'evidence', title: '提出依据' }, { key: 'status', title: '状态' }, { key: 'created_at', title: '创建时间' },
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
    <v-alert type="info" variant="tonal" density="comfortable" class="mb-5">{{ tabDescription }}</v-alert>
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
