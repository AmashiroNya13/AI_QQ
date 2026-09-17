<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const activeTab = ref<'users' | 'groups' | 'messages'>('users');
const query = ref('');
const loading = ref(false);
const error = ref('');
const items = ref<EcobotRecord[]>([]);

const columns = computed(() => ({
  users: [
    { key: 'qq_id', title: 'QQ号' },
    { key: 'current_nickname', title: '当前昵称' },
    { key: 'group_count', title: '所在群数' },
    { key: 'message_count', title: '消息数' },
    { key: 'first_seen_at', title: '首次发现' },
    { key: 'last_seen_at', title: '最近出现' },
  ],
  groups: [
    { key: 'group_id', title: '群号' },
    { key: 'current_name', title: '群名称' },
    { key: 'current_owner_qq_id', title: '群主' },
    { key: 'member_count', title: '成员数' },
    { key: 'message_count', title: '消息数' },
    { key: 'last_seen_at', title: '最近活动' },
  ],
  messages: [
    { key: 'sender_qq_id', title: '发送者' },
    { key: 'sender_group_card', title: '群名片' },
    { key: 'group_name', title: '群聊' },
    { key: 'content_text', title: '消息内容' },
    { key: 'sent_at', title: '发送时间戳' },
    { key: 'recalled_at', title: '撤回时间' },
  ],
}[activeTab.value]));

async function load() {
  loading.value = true;
  error.value = '';
  try {
    if (activeTab.value === 'users') items.value = await ecobotApi.users(query.value, 200);
    if (activeTab.value === 'groups') items.value = await ecobotApi.groups(query.value, 200);
    if (activeTab.value === 'messages') items.value = await ecobotApi.messages(query.value, 200);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

function switchTab(value: 'users' | 'groups' | 'messages') {
  activeTab.value = value;
  query.value = '';
  load();
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="QQ 数据档案" :loading="loading" @refresh="load" />
    <div class="archive-toolbar">
      <v-btn-toggle :model-value="activeTab" mandatory density="compact" variant="outlined" @update:model-value="switchTab">
        <v-btn value="users">人物</v-btn>
        <v-btn value="groups">群聊</v-btn>
        <v-btn value="messages">消息</v-btn>
      </v-btn-toggle>
      <v-text-field
        v-model="query"
        density="compact"
        variant="outlined"
        hide-details
        prepend-inner-icon="mdi-magnify"
        :placeholder="activeTab === 'messages' ? '搜索消息内容' : '搜索号码或名称'"
        @keyup.enter="load"
      />
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-magnify" @click="load">搜索</v-btn>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <EcobotTable :columns="columns" :items="items" :loading="loading" />
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1440px; }
.archive-toolbar { align-items: center; display: grid; gap: 10px; grid-template-columns: auto minmax(220px, 420px) auto; margin-bottom: 16px; }
@media (max-width: 720px) { .archive-toolbar { align-items: stretch; grid-template-columns: 1fr; } }
</style>
