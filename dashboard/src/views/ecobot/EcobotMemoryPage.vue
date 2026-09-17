<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const items = ref<EcobotRecord[]>([]);
const columns = [
  { key: 'memory_type', title: '类型' },
  { key: 'channel_id', title: '频道' },
  { key: 'user_id', title: '对象' },
  { key: 'content', title: '记忆内容' },
  { key: 'importance', title: '重要度' },
  { key: 'access_count', title: '调用次数' },
  { key: 'created_at', title: '形成时间' },
];

async function load() {
  loading.value = true;
  error.value = '';
  try { items.value = await ecobotApi.memories(200); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="记忆系统" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <EcobotTable :columns="columns" :items="items" :loading="loading" />
  </v-container>
</template>

<style scoped>.ecobot-page { max-width: 1440px; }</style>
