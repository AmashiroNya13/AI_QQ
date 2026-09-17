<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ecobotApi, type EcobotSettings } from '@/api/ecobot';
import { providerApi, toolApi } from '@/api/v1';
import EcobotPageHeader from '@/components/ecobot/EcobotPageHeader.vue';

const loading = ref(false);
const saving = ref(false);
const error = ref('');
const saved = ref(false);
const activeTab = ref('runtime');
const chatProviders = ref<Array<{ title: string; value: string }>>([]);
const embeddingProviders = ref<Array<{ title: string; value: string }>>([]);
const tools = ref<Array<{ title: string; value: string }>>([]);

const defaults: EcobotSettings = {
  enabled: true,
  persona_enabled: true,
  passive_interval_seconds: 15,
  idle_interval_seconds: 600,
  idle_poll_seconds: 5,
  idle_allow_proactive_expression: true,
  idle_prompt: '',
  max_action_rounds: 4,
  max_actions: 12,
  recent_message_limit: 20,
  memory_limit: 12,
  memory_embedding_enabled: true,
  memory_embedding_provider_id: '',
  memory_semantic_min_similarity: 0.35,
  structured_output_retries: 1,
  model_timeout_seconds: 90,
  request_max_retries: 2,
  generation_temperature: 0.4,
  generation_top_p: 0.9,
  generation_max_tokens: 1200,
  desire_threshold: 50,
  desire_time_growth_enabled: true,
  desire_time_growth_per_hour: 5,
  desire_time_growth_max: 40,
  state_update_enabled: true,
  tools_enabled: true,
  tool_allowlist: [],
  max_tool_risk: 'medium',
  anti_repeat_window_minutes: 360,
  anti_repeat_fuzzy_threshold: 0.92,
  anti_repeat_semantic_threshold: 0.94,
  reply_style_repeat_enabled: true,
  reply_style_window_minutes: 60,
  reply_style_repeat_limit: 1,
  style_learning_enabled: true,
  style_reference_enabled: true,
  style_reference_user_ids: [],
  style_reference_limit: 6,
  style_reference_min_similarity: 0.35,
  identity_imitation_enabled: false,
  identity_imitation_user_id: '',
  high_fidelity_imitation_enabled: false,
  style_rewrite_provider_id: '',
  style_rewrite_prompt: '',
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
  debug_log_include_prompts: false,
  observe_enabled: true,
  observe_provider_id: '',
  observe_prompt: '',
  infer_enabled: true,
  infer_provider_id: '',
  infer_prompt: '',
  desire_enabled: true,
  desire_provider_id: '',
  desire_prompt: '',
  plan_enabled: true,
  plan_provider_id: '',
  plan_prompt: '',
  reflect_enabled: true,
  reflect_provider_id: '',
  reflect_prompt: '',
};
const form = reactive<EcobotSettings>({ ...defaults });
const settingsRecord = form as unknown as Record<string, any>;

const tabs = [
  ['runtime', '运行与触发', 'mdi-timer-play-outline'],
  ['phases', '五阶段思考', 'mdi-head-cog-outline'],
  ['model', '模型参数', 'mdi-tune-variant'],
  ['persona', '人格与状态', 'mdi-account-heart-outline'],
  ['tools', '工具执行', 'mdi-tools'],
  ['memory', '记忆与防重复', 'mdi-brain'],
  ['relations', '关系评估', 'mdi-account-switch-outline'],
  ['trace', '调试追踪', 'mdi-text-box-search-outline'],
];

const phases = [
  { name: '观察', description: '从消息和世界状态中提取可验证事实', enabled: 'observe_enabled', provider: 'observe_provider_id', prompt: 'observe_prompt' },
  { name: '分析推断', description: '推断意图、情绪和关系影响，并保留不确定性', enabled: 'infer_enabled', provider: 'infer_provider_id', prompt: 'infer_prompt' },
  { name: '欲望判断', description: '结合人格与场景决定是否参与；关闭后默认继续', enabled: 'desire_enabled', provider: 'desire_provider_id', prompt: 'desire_prompt' },
  { name: '行为规划', description: '生成工具动作、表达内容和状态变化；关闭后保持沉默', enabled: 'plan_enabled', provider: 'plan_provider_id', prompt: 'plan_prompt' },
  { name: '结果反思', description: '检查工具结果并决定是否重新规划；关闭后直接接受结果', enabled: 'reflect_enabled', provider: 'reflect_provider_id', prompt: 'reflect_prompt' },
];

const providerItems = (items: Array<{ title: string; value: string }>) => [
  { title: '继承当前会话模型', value: '' },
  ...items,
];

function assignSettings(value: EcobotSettings) {
  Object.assign(form, defaults, value);
}

async function loadOptions() {
  const [chatResponse, embeddingResponse, toolResponse] = await Promise.all([
    providerApi.list({ capability: 'chat', enabled: true }),
    providerApi.list({ capability: 'embedding', enabled: true }),
    toolApi.list({ enabled: true }),
  ]);
  const chat = chatResponse.data.data.providers || [];
  const embeddings = embeddingResponse.data.data.providers || [];
  const toolRows = Array.isArray(toolResponse.data.data) ? toolResponse.data.data : [];
  chatProviders.value = chat.map((item: any) => ({ title: item.id, value: item.id }));
  embeddingProviders.value = embeddings.map((item: any) => ({ title: item.id, value: item.id }));
  tools.value = toolRows.map((item: any) => ({ title: item.name || item.id, value: item.name || item.id })).filter((item: { value: string }) => Boolean(item.value));
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [settings] = await Promise.all([ecobotApi.settings(), loadOptions()]);
    assignSettings(settings);
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
    const changes = Object.fromEntries(Object.entries(form).filter(([key]) => key !== 'updated_at')) as Partial<EcobotSettings>;
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
    <EcobotPageHeader title="AI 介入控制台" subtitle="控制每一段思考、执行、记忆和评估链路" :loading="loading" @refresh="load" />
    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <v-alert v-if="saved" type="success" variant="tonal" class="mb-4" closable @click:close="saved = false">设置已保存并将在下一次处理时应用</v-alert>

    <v-tabs v-model="activeTab" color="primary" show-arrows class="settings-tabs">
      <v-tab v-for="tab in tabs" :key="tab[0]" :value="tab[0]" :prepend-icon="tab[2]">{{ tab[1] }}</v-tab>
    </v-tabs>

    <form @submit.prevent="save">
      <v-window v-model="activeTab" class="settings-window">
        <v-window-item value="runtime">
          <section class="settings-section">
            <h2>总控与触发</h2>
            <div class="setting-row"><div><strong>启用行为链</strong><p>关闭后保留基础消息链，Ecobot 不归档也不参与回复</p></div><v-switch v-model="form.enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>允许空闲主动表达</strong><p>空闲思考可以主动发消息；关闭后只更新内部判断，不向群内发送</p></div><v-switch v-model="form.idle_allow_proactive_expression" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.passive_interval_seconds" type="number" min="0" max="86400" label="被动消息合并间隔" suffix="秒" hint="间隔内的新消息合并为一次刷新；0 表示不延迟" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.idle_interval_seconds" type="number" min="1" label="空闲驱动间隔" suffix="秒" hint="频道持续无新消息多久后启动一次空闲思考" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.idle_poll_seconds" type="number" min="1" label="空闲检查周期" suffix="秒" hint="后台检查哪些频道到期的频率" persistent-hint variant="outlined" />
            </div>
            <v-textarea v-model="form.idle_prompt" class="mt-5" label="空闲刺激提示词" hint="作为空闲驱动的当前事件内容送入观察阶段" persistent-hint rows="4" auto-grow variant="outlined" />
          </section>
        </v-window-item>

        <v-window-item value="phases">
          <section class="settings-section phase-list">
            <div v-for="phase in phases" :key="phase.enabled" class="phase-block">
              <div class="setting-row"><div><strong>{{ phase.name }}</strong><p>{{ phase.description }}</p></div><v-switch v-model="settingsRecord[phase.enabled]" color="primary" hide-details /></div>
              <div class="field-grid phase-fields">
                <v-select v-model="settingsRecord[phase.provider]" :items="providerItems(chatProviders)" label="介入模型" hint="留空时继承该消息所属会话的模型" persistent-hint variant="outlined" />
                <v-textarea v-model="settingsRecord[phase.prompt]" label="阶段附加提示词" hint="追加到该阶段的系统约束，不替换结构化输出协议" persistent-hint rows="3" auto-grow variant="outlined" />
              </div>
            </div>
          </section>
        </v-window-item>

        <v-window-item value="model">
          <section class="settings-section">
            <h2>生成与容错</h2>
            <div class="setting-row"><div><strong>欲望随时间增长</strong><p>按频道从上次成功表达开始累计欲望；成功发送后归零，明确安静请求和边界冲突不受加成影响</p></div><v-switch v-model="form.desire_time_growth_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.generation_temperature" type="number" min="0" max="2" step="0.05" label="Temperature" hint="越低越稳定，越高越发散" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.generation_top_p" type="number" min="0" max="1" step="0.05" label="Top P" hint="限制候选词累计概率范围" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.generation_max_tokens" type="number" min="64" max="32768" label="最大输出 Token" hint="每一次阶段调用允许生成的上限" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.model_timeout_seconds" type="number" min="5" max="600" label="单次模型超时" suffix="秒" hint="超过时间会终止该次思考批次" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.request_max_retries" type="number" min="0" max="10" label="请求错误重试" suffix="次" hint="网络或服务错误后的额外重试次数" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.structured_output_retries" type="number" min="0" max="5" label="JSON 修复重试" suffix="次" hint="模型未返回合法结构时追加纠错提示再试" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.max_action_rounds" type="number" min="1" max="20" label="最大动作轮数" suffix="轮" hint="工具反馈后重新规划的轮数预算" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.max_actions" type="number" min="1" max="100" label="最大动作总数" suffix="次" hint="单个思考批次可执行的工具动作总预算" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.desire_threshold" type="number" min="0" max="100" step="1" label="回复欲望阈值" hint="模型分数叠加时间欲望后达到该值才继续规划" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.desire_time_growth_per_hour" type="number" min="0" max="100" step="0.5" label="时间欲望增长" suffix="点/小时" hint="持续没有成功表达时，每小时增加的参与欲望" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.desire_time_growth_max" type="number" min="0" max="100" step="1" label="时间欲望上限" suffix="点" hint="限制时间加成，避免单靠沉默时长覆盖人格和场景判断" persistent-hint variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="persona">
          <section class="settings-section">
            <h2>人格与连续状态</h2>
            <div class="setting-row"><div><strong>继承会话人格</strong><p>把当前会话选中的人格提示词加入每个思考阶段</p></div><v-switch v-model="form.persona_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>允许状态更新</strong><p>规划阶段可以维护活动、场景、情绪、精力、目标和预计结束时间</p></div><v-switch v-model="form.state_update_enabled" color="primary" hide-details /></div>
          </section>
        </v-window-item>

        <v-window-item value="tools">
          <section class="settings-section">
            <h2>工具权限</h2>
            <div class="setting-row"><div><strong>允许工具执行</strong><p>关闭后规划阶段只允许表达与状态更新，所有工具调用会被拒绝</p></div><v-switch v-model="form.tools_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-select v-model="form.max_tool_risk" :items="[{ title: '低风险', value: 'low' }, { title: '中风险', value: 'medium' }, { title: '高风险', value: 'high' }]" label="最高工具风险" hint="超过等级的规划动作不会执行" persistent-hint variant="outlined" />
              <v-select v-model="form.tool_allowlist" :items="tools" multiple chips closable-chips clearable label="工具白名单" hint="留空允许全部已启用工具；选择后只允许名单内工具" persistent-hint variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="memory">
          <section class="settings-section">
            <h2>记忆召回</h2>
            <div class="setting-row"><div><strong>启用语义召回</strong><p>使用 Embedding 按语义相关度补充长期记忆，失败时自动保留关键词召回</p></div><v-switch v-model="form.memory_embedding_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-select v-model="form.memory_embedding_provider_id" :items="providerItems(embeddingProviders)" label="Embedding 模型" hint="留空时使用第一个已启用的 Embedding Provider" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.memory_semantic_min_similarity" type="number" min="0" max="1" step="0.01" label="语义最低相似度" hint="低于阈值的向量记忆被过滤，纯关键词候选仍保留" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.recent_message_limit" type="number" min="1" max="500" label="近期消息上限" suffix="条" hint="直接放入思考上下文的近期消息数量" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.memory_limit" type="number" min="1" max="500" label="长期记忆上限" suffix="条" hint="每批最多召回的长期记忆数量" persistent-hint variant="outlined" />
            </div>
            <h2>表达防重复</h2>
            <div class="field-grid">
              <v-text-field v-model.number="form.anti_repeat_window_minutes" type="number" min="1" max="43200" label="比较时间窗" suffix="分钟" hint="只与该时间范围内成功或待发送的表达比较" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.anti_repeat_fuzzy_threshold" type="number" min="0" max="1" step="0.01" label="文本近似阈值" hint="归一化文本相似度达到阈值时阻止发送" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.anti_repeat_semantic_threshold" type="number" min="0" max="1" step="0.01" label="语义重复阈值" hint="Embedding 相似度达到阈值时阻止发送" persistent-hint variant="outlined" />
            </div>
            <h2>回复范式防重复</h2>
            <div class="setting-row"><div><strong>避免重复尾音与回应方式</strong><p>短时间内禁止重复相同的语气助词、开头、表情习惯与问答形式；和内容防重复一起生效</p></div><v-switch v-model="form.reply_style_repeat_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.reply_style_window_minutes" type="number" min="1" max="43200" label="风格比较时间窗" suffix="分钟" hint="只比较该时段内已发送或待发送的表达风格" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.reply_style_repeat_limit" type="number" min="1" max="20" label="同一风格允许次数" suffix="次" hint="设为 1 时，近期同一明显尾音或开头只允许出现一次" persistent-hint variant="outlined" />
            </div>
            <h2>人物语气学习</h2>
            <div class="setting-row"><div><strong>归档人物表达特征</strong><p>将新消息写入本地风格样本库，提取开头、尾音、句式和表情偏好；Embedding 开启时复用同一向量请求</p></div><v-switch v-model="form.style_learning_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>按需参考指定人物风格</strong><p>只有下面名单中的 QQ 号会在其发言时被召回少量风格样本；机器人不会自称为该人物或复述样本原句</p></div><v-switch v-model="form.style_reference_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-combobox v-model="form.style_reference_user_ids" multiple chips closable-chips clearable label="风格参考 QQ 号" hint="加入后，机器人可按需参考这些人的表达节奏和偏好" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.style_reference_limit" type="number" min="1" max="8" label="单次参考样本数" suffix="条" hint="只向模型提供最相关的少量样本" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.style_reference_min_similarity" type="number" min="0" max="1" step="0.01" label="风格语义最低相似度" hint="有向量时，低于此阈值的历史样本不会被参考" persistent-hint variant="outlined" />
            </div>
            <h2>完整身份模仿模式</h2>
            <div class="setting-row"><div><strong>启用身份模仿</strong><p>启用后停止加载当前人格设定，改用指定 QQ 的表达风格、句式偏好和少量样本进行回复；默认关闭</p></div><v-switch v-model="form.identity_imitation_enabled" color="error" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model="form.identity_imitation_user_id" label="被模仿对象 QQ 号" placeholder="输入一个 QQ 号" hint="启用身份模仿后必须指定目标 QQ 号" persistent-hint variant="outlined" />
            </div>
            <div class="setting-row"><div><strong>启用高保真二次改写</strong><p>在行为规划后增加一次风格改写调用，事实内容保持不变；更像目标人物，但会增加一次模型费用和延迟</p></div><v-switch v-model="form.high_fidelity_imitation_enabled" color="warning" hide-details /></div>
            <div class="field-grid">
              <v-select v-model="form.style_rewrite_provider_id" :items="providerItems(chatProviders)" label="高保真改写模型" hint="留空时使用当前会话模型；可单独选择更擅长角色表达的模型" persistent-hint variant="outlined" />
            </div>
          </section>
        </v-window-item>

        <v-window-item value="relations">
          <section class="settings-section">
            <h2>机器人对人物的好感</h2>
            <div class="setting-row"><div><strong>启用多维好感度</strong><p>按 QQ 号长期维护好感、信任、熟悉度和会随时间衰减的短期烦躁，并影响欲望判断与行为规划</p></div><v-switch v-model="form.affinity_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-text-field v-model.number="form.affinity_positive_step_limit" type="number" min="0.1" max="20" step="0.1" label="单次正向变化上限" hint="防止一次普通互动让关系突然升高" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.affinity_negative_step_limit" type="number" min="0.1" max="30" step="0.1" label="单次负向变化上限" hint="负面事件通常比正面事件影响更快，但仍受上限约束" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.affinity_irritation_half_life_hours" type="number" min="0.1" max="720" step="0.5" label="烦躁衰减半衰期" suffix="小时" hint="经过该时间后，短期烦躁自然下降到一半" persistent-hint variant="outlined" />
            </div>
            <h2>周期关系评估</h2>
            <div class="setting-row"><div><strong>启用 AI 复核</strong><p>统计评估完成后，再由模型复核关系类型、摘要和置信度</p></div><v-switch v-model="form.relationship_ai_enabled" color="primary" hide-details /></div>
            <div class="field-grid">
              <v-select v-model="form.relationship_provider_id" :items="providerItems(chatProviders)" label="关系复核模型" hint="留空时继承触发本次后台评估的会话模型" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.relationship_scan_interval_seconds" type="number" min="60" label="扫描间隔" suffix="秒" hint="检查是否存在到期关系评估的最短间隔" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.relationship_evaluation_interval_hours" type="number" min="1" label="最长评估周期" suffix="小时" hint="即使证据较少，达到周期也会重新评估" persistent-hint variant="outlined" />
              <v-text-field v-model.number="form.relationship_minimum_new_evidence" type="number" min="1" label="最少新增证据" suffix="条" hint="达到数量后可提前触发评估" persistent-hint variant="outlined" />
            </div>
            <v-textarea v-model="form.relationship_prompt" class="mt-5" label="关系复核提示词" hint="约束模型如何解释统计证据，不替换 JSON 输出协议" persistent-hint rows="4" auto-grow variant="outlined" />
          </section>
        </v-window-item>

        <v-window-item value="trace">
          <section class="settings-section">
            <h2>阶段调用追踪</h2>
            <div class="setting-row"><div><strong>记录 AI 阶段调用</strong><p>保存阶段、模型、耗时、输出和错误，便于定位思考链问题</p></div><v-switch v-model="form.trace_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>保存完整提示词</strong><p>开启后追踪包含系统与输入提示词；其中可能含聊天内容和人格设定</p></div><v-switch v-model="form.trace_include_prompts" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>输出中文调试日志</strong><p>在实时日志和日志文件中显示批次、模型调用、思考结果、工具执行与停止原因</p></div><v-switch v-model="form.debug_log_enabled" color="primary" hide-details /></div>
            <div class="setting-row"><div><strong>日志显示完整输入</strong><p>额外把系统提示词和输入提示词写入日志；可能很长并包含聊天、记忆与人格内容</p></div><v-switch v-model="form.debug_log_include_prompts" color="primary" hide-details /></div>
            <v-text-field v-model.number="form.trace_max_records" class="single-field" type="number" min="100" max="100000" label="最大追踪记录数" suffix="条" hint="超过上限时自动删除最旧记录" persistent-hint variant="outlined" />
          </section>
        </v-window-item>
      </v-window>

      <div class="save-bar">
        <span v-if="form.updated_at">最后保存：{{ form.updated_at }}</span>
        <v-spacer />
        <v-btn type="submit" color="primary" prepend-icon="mdi-content-save" :loading="saving">保存全部设置</v-btn>
      </div>
    </form>
  </v-container>
</template>

<style scoped>
.ecobot-page { max-width: 1320px; }
.settings-tabs { border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12); }
.settings-window { min-height: 500px; }
.settings-section { max-width: 1060px; padding: 28px 4px 20px; }
.settings-section h2 { font-size: 1rem; font-weight: 650; letter-spacing: 0; margin: 0 0 18px; }
.settings-section h2:not(:first-child) { border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1); margin-top: 28px; padding-top: 24px; }
.setting-row { align-items: center; border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08); display: grid; gap: 20px; grid-template-columns: minmax(0, 1fr) auto; min-height: 76px; padding: 10px 4px; }
.setting-row strong { display: block; font-size: 0.94rem; }
.setting-row p { color: rgba(var(--v-theme-on-surface), 0.62); font-size: 0.82rem; line-height: 1.45; margin: 4px 0 0; }
.field-grid { display: grid; gap: 20px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 24px; }
.phase-block { border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.14); padding-bottom: 28px; }
.phase-block + .phase-block { padding-top: 16px; }
.phase-fields { align-items: start; }
.single-field { margin-top: 24px; max-width: 520px; }
.save-bar { align-items: center; background: rgb(var(--v-theme-surface)); border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12); bottom: 0; display: flex; min-height: 72px; padding: 12px 4px; position: sticky; z-index: 3; }
.save-bar span { color: rgba(var(--v-theme-on-surface), 0.56); font-size: 0.78rem; }
@media (max-width: 760px) {
  .field-grid { grid-template-columns: 1fr; }
  .settings-section { padding-top: 20px; }
  .save-bar span { display: none; }
}
</style>
