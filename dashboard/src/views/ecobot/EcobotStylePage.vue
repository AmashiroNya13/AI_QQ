<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const backfilling = ref(false);
const error = ref('');
const notice = ref('');
const backfillUserId = ref('');
const profiles = ref<EcobotRecord[]>([]);
const examples = ref<EcobotRecord[]>([]);
const selected = ref<EcobotRecord | null>(null);

const profileColumns = [
  { key: 'user_id', title: 'QQ 号' },
  { key: 'current_nickname', title: '昵称' },
  { key: 'sample_count', title: '样本数' },
  { key: 'updated_at', title: '最近学习' },
];
const exampleColumns = [
  { key: 'content', title: '样本内容' },
  { key: 'features_json', title: '表达特征' },
  { key: 'channel_id', title: '来源群聊' },
  { key: 'created_at', title: '记录时间' },
];

async function load() {
  loading.value = true;
  error.value = '';
  try {
    profiles.value = await ecobotApi.styleProfiles(300);
    if (selected.value) {
      examples.value = await ecobotApi.styleExamples(String(selected.value.user_id), 200);
    }
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

async function selectProfile(item: EcobotRecord) {
  selected.value = item;
  loading.value = true;
  error.value = '';
  try { examples.value = await ecobotApi.styleExamples(String(item.user_id), 200); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

async function backfill() {
  const userId = String(selected.value?.user_id || backfillUserId.value).trim();
  if (!userId) {
    error.value = '请先选择一个人物，或输入需要回填的 QQ 号';
    return;
  }
  backfilling.value = true;
  error.value = '';
  try {
    const result = await ecobotApi.backfillStyle(userId);
    notice.value = `QQ ${userId} 已从历史消息补充 ${result.inserted} 条风格样本`;
    await load();
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { backfilling.value = false; }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="人物语气学习" subtitle="本地保存表达样本与特征；仅在 AI 介入控制台指定 QQ 后按需参考" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" closable class="mb-5" @click:close="notice = ''">{{ notice }}</v-alert>
    <v-alert type="info" variant="tonal" class="mb-5">样本是本地 SQLite 数据。回填只导入历史文本和表达特征；不会自动为历史记录批量调用 Embedding，因此不会突然产生大量费用</v-alert>
    <div class="backfill-row mb-5">
      <v-text-field v-model="backfillUserId" label="从历史回填的 QQ 号" placeholder="输入 QQ 号后导入其已有聊天记录" hide-details variant="outlined" />
      <v-btn color="primary" prepend-icon="mdi-database-import-outline" :loading="backfilling" @click="backfill">回填历史消息</v-btn>
    </div>
    <EcobotTable :columns="profileColumns" :items="profiles" :loading="loading" @row-click="selectProfile" />
    <section v-if="selected" class="mt-8">
      <div class="section-head">
        <div><h2>{{ selected.current_nickname || selected.user_id }} 的风格样本</h2><p>QQ {{ selected.user_id }} · 可在“AI 介入控制台”加入风格参考名单</p></div>
        <v-btn color="primary" prepend-icon="mdi-database-import-outline" :loading="backfilling" @click="backfill">回填历史消息</v-btn>
      </div>
      <EcobotTable :columns="exampleColumns" :items="examples" :loading="loading" empty-text="尚无可用文本样本" />
    </section>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.backfill-row { align-items: center; display: flex; gap: 12px; max-width: 620px; }
.section-head { align-items: center; display: flex; gap: 20px; justify-content: space-between; margin-bottom: 16px; }
.section-head h2 { font-size: 1.05rem; margin: 0; }
.section-head p { color: rgba(var(--v-theme-on-surface), 0.62); font-size: 0.82rem; margin: 4px 0 0; }
</style>
