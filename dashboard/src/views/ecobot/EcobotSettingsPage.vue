<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ecobotApi, type EcobotSettings } from '@/api/ecobot';
import { providerApi } from '@/api/v1';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';

const loading = ref(false);
const saving = ref(false);
const error = ref('');
const saved = ref(false);
const activeTab = ref('runtime');
const chatProviders = ref<Array<{ title: string; value: string }>>([]);

const defaults: EcobotSettings = {
  enabled: true,
  persona_enabled: true,
  persona_base_prompt: '',
  persona_growth_enabled: true,
  persona_growth_auto_activate: true,
  persona_growth_min_confidence: 0.75,
  persona_growth_min_evidence: 3,
  persona_growth_limit: 12,
  passive_interval_seconds: 15,
  idle_interval_seconds: 600,
  idle_poll_seconds: 5,
  idle_allow_proactive_expression: true,
  idle_prompt: '',
  memory_reconstruction_enabled: true,
  memory_reconstruction_limit: 8,
  memory_reconstruction_hops: 2,
  structured_output_retries: 1,
  model_timeout_seconds: 90,
  request_max_retries: 2,
  generation_temperature: 0.4,
  generation_top_p: 0.9,
  generation_max_tokens: 1200,
  anti_repeat_window_minutes: 360,
  anti_repeat_fuzzy_threshold: 0.92,
  anti_repeat_semantic_threshold: 0.94,
  reply_style_repeat_enabled: true,
  reply_style_window_minutes: 60,
  reply_style_repeat_limit: 1,
  relationship_scan_interval_seconds: 1800,
  relationship_evaluation_interval_hours: 168,
  relationship_minimum_new_evidence: 10,
  relationship_ai_enabled: false,
  relationship_provider_id: '',
  relationship_prompt: '',
  affinity_enabled: true,
  affinity_positive_step_limit: 20,
  affinity_negative_step_limit: 30,
  affinity_irritation_half_life_hours: 12,
  trace_enabled: true,
  trace_include_prompts: true,
  trace_max_records: 5000,
  debug_log_enabled: true,
};
const form = reactive<EcobotSettings>({ ...defaults });

const tabs = [
  ['runtime', '运行与触发', 'mdi-timer-play-outline'],
  ['model', '主体模型', 'mdi-head-cog-outline'],
  ['persona', '人格与成长', 'mdi-account-heart-outline'],
  ['memory', '记忆与防重复', 'mdi-brain'],
  ['relations', '关系评估', 'mdi-account-switch-outline'],
  ['trace', '调试追踪', 'mdi-text-box-search-outline'],
];

function providerItems() {
  return [{ title: '继承当前会话模型', value: '' }, ...chatProviders.value];
}

function assignSettings(value: EcobotSettings) {
  Object.assign(form, defaults, value);
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [settings, providers] = await Promise.all([
      ecobotApi.settings(),
      providerApi.list({ capability: 'chat', enabled: true }),
    ]);
    assignSettings(settings);
    chatProviders.value = (providers.data.data.providers || []).map((item: any) => ({ title: item.id, value: item.id }));
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  saved.value = false;
  error.value = '';
  try {
    const { updated_at: _updatedAt, ...changes } = form;
    assignSettings(await ecobotApi.updateSettings(changes));
    saved.value = true;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <v-container fluid class="ecobot-page pa-4 pa-md-6">
    <EcobotPageHeader title="主体控制台" subtitle="这里仅配置主体世界链：刺激、人格、记忆、关系和回执，不再暴露旧五段式或身份模仿链" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <v-alert v-if="saved" type="success" variant="tonal" closable class="mb-4" @click:close="saved = false">设置已保存，并将在下一次刺激或心跳中应用</v-alert>

    <v-tabs v-model="activeTab" color="primary" show-arrows class="settings-tabs">
      <v-tab v-for="tab in tabs" :key="tab[0]" :value="tab[0]" :prepend-icon="tab[2]">{{ tab[1] }}</v-tab>
    </v-tabs>
    <form @submit.prevent="save">
      <v-window v-model="activeTab" class="settings-window">
        <v-window-item value="runtime">
          <section class="settings-section">
            <h2>主体与心跳</h2>
            <div class="setting-row"><div><strong>启用主体世界链</strong><p>关闭后只保留平台归档，不运行主体决策、意图和反馈</p></div><v-switch v-model="form.enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>允许空闲主动表达</strong><p>空闲时间也作为时钟刺激；关闭时仍推进世界和主体状态，但不发送主动消息</p></div><v-switch v-model="form.idle_allow_proactive_expression" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.passive_interval_seconds" type="number" min="0" label="消息合并间隔" suffix="秒" variant="outlined" />
              <v-text-field v-model.number="form.idle_interval_seconds" type="number" min="1" label="空闲刺激间隔" suffix="秒" variant="outlined" />
              <v-text-field v-model.number="form.idle_poll_seconds" type="number" min="1" label="心跳检查周期" suffix="秒" variant="outlined" />
            </div>
            <v-textarea v-model="form.idle_prompt" class="mt-5" label="空闲刺激内容" rows="4" auto-grow variant="outlined" />
          </section>
        </v-window-item>

        <v-window-item value="model">
          <section class="settings-section">
            <h2>主体决策模型</h2>
            <p class="section-note">当前每次刺激只进行一次“主体决策”调用；模型可以回复、提出意图、等待或忽略，不再拆成五段</p>
            <div class="field-grid">
              <v-text-field v-model.number="form.generation_temperature" type="number" min="0" max="2" step="0.05" label="Temperature" variant="outlined" />
              <v-text-field v-model.number="form.generation_top_p" type="number" min="0" max="1" step="0.05" label="Top P" variant="outlined" />
              <v-text-field v-model.number="form.generation_max_tokens" type="number" min="64" max="32768" label="最大输出 Token" variant="outlined" />
              <v-text-field v-model.number="form.model_timeout_seconds" type="number" min="5" max="600" label="模型超时" suffix="秒" variant="outlined" />
              <v-text-field v-model.number="form.request_max_retries" type="number" min="0" max="10" label="请求重试" suffix="次" variant="outlined" />
              <v-text-field v-model.number="form.structured_output_retries" type="number" min="0" max="5" label="结构修复重试" suffix="次" hint="关系评估等辅助调用使用；主体决策本身保持单次调用" persistent-hint variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="persona">
          <section class="settings-section">
            <h2>基础人格与人格空地</h2>
            <v-textarea v-model="form.persona_base_prompt" label="基础人格" hint="稳定身份、性格、价值观和表达底色；不会被自动覆盖" persistent-hint rows="8" auto-grow variant="outlined" />
            <div class="setting-row"><div><strong>启用人格空地</strong><p>主体从经历中形成候选倾向，重复证据达到条件后才进入当前人格</p></div><v-switch v-model="form.persona_growth_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>自动确认稳定倾向</strong><p>单次经历不会直接改写稳定人格</p></div><v-switch v-model="form.persona_growth_auto_activate" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.persona_growth_min_confidence" type="number" min="0" max="1" step="0.05" label="最低置信度" variant="outlined" />
              <v-text-field v-model.number="form.persona_growth_min_evidence" type="number" min="1" max="100" label="所需证据数" variant="outlined" />
              <v-text-field v-model.number="form.persona_growth_limit" type="number" min="1" max="100" label="注入增量上限" suffix="条" variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="memory">
          <section class="settings-section">
            <h2>主动记忆重建</h2>
            <div class="setting-row"><div><strong>启用 CTC 记忆重建</strong><p>主体先选线索，再沿本地记忆图按证据展开，不把整库塞进提示词</p></div><v-switch v-model="form.memory_reconstruction_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.memory_reconstruction_limit" type="number" min="1" max="100" label="单次情节数" variant="outlined" />
              <v-text-field v-model.number="form.memory_reconstruction_hops" type="number" min="0" max="4" label="最大展开层数" variant="outlined" />
            </div>
            <h2>表达防重复</h2>
            <div class="setting-row"><div><strong>启用表达防重复</strong><p>在真正发送前检查文本、尾音、回应形式和近期表达范式</p></div><v-switch v-model="form.reply_style_repeat_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.anti_repeat_window_minutes" type="number" min="1" max="43200" label="内容时间窗" suffix="分钟" variant="outlined" />
              <v-text-field v-model.number="form.anti_repeat_fuzzy_threshold" type="number" min="0" max="1" step="0.01" label="文本相似阈值" variant="outlined" />
              <v-text-field v-model.number="form.anti_repeat_semantic_threshold" type="number" min="0" max="1" step="0.01" label="向量相似阈值" variant="outlined" />
              <v-text-field v-model.number="form.reply_style_window_minutes" type="number" min="1" max="43200" label="范式时间窗" suffix="分钟" variant="outlined" />
              <v-text-field v-model.number="form.reply_style_repeat_limit" type="number" min="1" max="20" label="范式允许次数" variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="relations">
          <section class="settings-section">
            <h2>主体关系</h2>
            <div class="setting-row"><div><strong>启用多维关系</strong><p>维护好感、信任、熟悉度和短期烦躁；人工锁定的特殊等级不允许主体自行改变</p></div><v-switch v-model="form.affinity_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.affinity_positive_step_limit" type="number" min="0.1" max="20" step="0.1" label="正向变化上限" variant="outlined" />
              <v-text-field v-model.number="form.affinity_negative_step_limit" type="number" min="0.1" max="30" step="0.1" label="负向变化上限" variant="outlined" />
              <v-text-field v-model.number="form.affinity_irritation_half_life_hours" type="number" min="0.1" max="720" step="0.5" label="烦躁半衰期" suffix="小时" variant="outlined" />
              <v-select v-model="form.relationship_provider_id" :items="providerItems()" label="关系评估模型" variant="outlined" />
              <v-text-field v-model.number="form.relationship_scan_interval_seconds" type="number" min="60" label="关系扫描间隔" suffix="秒" variant="outlined" />
              <v-text-field v-model.number="form.relationship_evaluation_interval_hours" type="number" min="1" label="关系最长评估周期" suffix="小时" variant="outlined" />
              <v-text-field v-model.number="form.relationship_minimum_new_evidence" type="number" min="1" label="最少新增证据" suffix="条" variant="outlined" />
            </div>
            <div class="setting-row"><div><strong>允许关系辅助评估</strong><p>只评估人物之间的长期关系，不参与主体当前回复决策</p></div><v-switch v-model="form.relationship_ai_enabled" color="primary" hide-details /></div>
            <v-textarea v-model="form.relationship_prompt" class="mt-5" label="关系评估说明" rows="4" auto-grow variant="outlined" />
          </section>
        </v-window-item>

        <v-window-item value="trace">
          <section class="settings-section">
            <h2>主体决策追踪</h2>
            <div class="setting-row"><div><strong>记录模型调用</strong><p>记录“主体决策”和“主体意图”调用、耗时、输出和错误</p></div><v-switch v-model="form.trace_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>保存提示词</strong><p>提示词可能包含聊天、人格和世界数据</p></div><v-switch v-model="form.trace_include_prompts" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>输出中文调试日志</strong><p>记录主体决定、意图、回执、后果和停止原因</p></div><v-switch v-model="form.debug_log_enabled" color="primary" hide-details /></div>
            <v-text-field v-model.number="form.trace_max_records" class="single-field" type="number" min="100" max="100000" label="最大追踪记录数" variant="outlined" />
          </section>
        </v-window-item>
      </v-window>
      <div class="save-bar"><span v-if="form.updated_at">最后保存：{{ form.updated_at }}</span><v-spacer /><v-btn type="submit" color="primary" prepend-icon="mdi-content-save" :loading="saving">保存主体设置</v-btn></div>
    </form>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1320px; }
.settings-tabs { border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12); }
.settings-window { min-height: 500px; }
.settings-section { max-width: 1060px; padding: 28px 4px 20px; }
.settings-section h2 { font-size: 1rem; font-weight: 650; margin: 0 0 18px; }
.settings-section h2:not(:first-child) { border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1); margin-top: 28px; padding-top: 24px; }
.section-note { color: rgba(var(--v-theme-on-surface), 0.62); font-size: .84rem; line-height: 1.5; margin: -6px 0 20px; }
.setting-row { align-items: center; border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08); display: grid; gap: 20px; grid-template-columns: minmax(0, 1fr) auto; min-height: 76px; padding: 10px 4px; }
.setting-row strong { display: block; font-size: .94rem; }
.setting-row p { color: rgba(var(--v-theme-on-surface), .62); font-size: .82rem; line-height: 1.45; margin: 4px 0 0; }
.field-grid { display: grid; gap: 20px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 24px; }
.single-field { margin-top: 24px; max-width: 520px; }
.save-bar { align-items: center; background: rgb(var(--v-theme-surface)); border-top: 1px solid rgba(var(--v-theme-on-surface), .12); bottom: 0; display: flex; min-height: 72px; padding: 12px 4px; position: sticky; z-index: 3; }
.save-bar span { color: rgba(var(--v-theme-on-surface), .56); font-size: .78rem; }
@media (max-width: 760px) { .field-grid { grid-template-columns: 1fr; } .save-bar span { display: none; } }
</style>
