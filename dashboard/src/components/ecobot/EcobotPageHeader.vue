<script setup lang="ts">
defineProps<{
  title: string;
  subtitle?: string;
  loading?: boolean;
}>();

defineEmits<{
  refresh: [];
}>();
</script>

<template>
  <header class="ecobot-page-header">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="subtitle">{{ subtitle }}</p>
    </div>
    <v-tooltip text="刷新" location="left">
      <template #activator="{ props }">
        <v-btn
          v-bind="props"
          icon="mdi-refresh"
          variant="text"
          :loading="loading"
          aria-label="刷新"
          @click="$emit('refresh')"
        />
      </template>
    </v-tooltip>
  </header>
</template>

<style scoped>
.ecobot-page-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
  min-height: 64px;
  padding: 8px 4px 20px;
}

h1 {
  font-size: 1.5rem;
  font-weight: 650;
  letter-spacing: 0;
  line-height: 1.25;
  margin: 0;
}

p {
  color: rgba(var(--v-theme-on-surface), 0.62);
  margin: 5px 0 0;
}
</style>
