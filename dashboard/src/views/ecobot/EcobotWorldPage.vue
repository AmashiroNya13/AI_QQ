<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ecobotApi, type EcobotRecord } from '@/api/ecobot';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';
import EcobotTable from '@/components/ecobot/EcobotTable.vue';

const loading = ref(false);
const error = ref('');
const items = ref<EcobotRecord[]>([]);
const columns = [
  { key: 'channel_id', title: '场景频道' },
  { key: 'revision', title: '修订' },
  { key: 'kind', title: '事件类型' },
  { key: 'payload_json', title: '事件内容' },
  { key: 'created_at', title: '记录时间' },
];

async function load() {
  loading.value = true;
  error.value = '';
  try { items.value = await ecobotApi.worldEvents(200); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="世界与场景" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-5">{{ error }}</v-alert>
    <EcobotTable :columns="columns" :items="items" :loading="loading" />
  </v-container>
</template>

<style scoped>.ecobot-page { max-width: 1440px; }</style>
