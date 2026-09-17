import { apiV1Client } from './http';

export interface EcobotEnvelope<T> {
  status: 'ok' | 'error';
  message?: string | null;
  data: T;
}

export type EcobotRecord = Record<string, unknown>;

export interface EcobotStatus {
  schema_version: number | null;
  counts: Record<string, number>;
  latest_batch: EcobotRecord | null;
  settings: EcobotSettings;
}

export interface EcobotSettings {
  enabled: boolean;
  persona_enabled: boolean;
  passive_interval_seconds: number;
  idle_interval_seconds: number;
  idle_poll_seconds: number;
  idle_allow_proactive_expression: boolean;
  idle_prompt: string;
  max_action_rounds: number;
  max_actions: number;
  recent_message_limit: number;
  memory_limit: number;
  memory_embedding_enabled: boolean;
  memory_embedding_provider_id: string;
  memory_semantic_min_similarity: number;
  structured_output_retries: number;
  model_timeout_seconds: number;
  request_max_retries: number;
  generation_temperature: number;
  generation_top_p: number;
  generation_max_tokens: number;
  desire_threshold: number;
  desire_time_growth_enabled: boolean;
  desire_time_growth_per_hour: number;
  desire_time_growth_max: number;
  state_update_enabled: boolean;
  tools_enabled: boolean;
  tool_allowlist: string[];
  max_tool_risk: 'low' | 'medium' | 'high';
  anti_repeat_window_minutes: number;
  anti_repeat_fuzzy_threshold: number;
  anti_repeat_semantic_threshold: number;
  reply_style_repeat_enabled: boolean;
  reply_style_window_minutes: number;
  reply_style_repeat_limit: number;
  style_learning_enabled: boolean;
  style_reference_enabled: boolean;
  style_reference_user_ids: string[];
  style_reference_limit: number;
  style_reference_min_similarity: number;
  identity_imitation_enabled: boolean;
  identity_imitation_user_id: string;
  high_fidelity_imitation_enabled: boolean;
  style_rewrite_provider_id: string;
  style_rewrite_prompt: string;
  relationship_scan_interval_seconds: number;
  relationship_evaluation_interval_hours: number;
  relationship_minimum_new_evidence: number;
  relationship_ai_enabled: boolean;
  relationship_provider_id: string;
  relationship_prompt: string;
  affinity_enabled: boolean;
  affinity_positive_step_limit: number;
  affinity_negative_step_limit: number;
  affinity_irritation_half_life_hours: number;
  trace_enabled: boolean;
  trace_include_prompts: boolean;
  trace_max_records: number;
  debug_log_enabled: boolean;
  debug_log_include_prompts: boolean;
  observe_enabled: boolean;
  observe_provider_id: string;
  observe_prompt: string;
  infer_enabled: boolean;
  infer_provider_id: string;
  infer_prompt: string;
  desire_enabled: boolean;
  desire_provider_id: string;
  desire_prompt: string;
  plan_enabled: boolean;
  plan_provider_id: string;
  plan_prompt: string;
  reflect_enabled: boolean;
  reflect_provider_id: string;
  reflect_prompt: string;
  updated_at?: string;
}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const response = await apiV1Client.get<EcobotEnvelope<T>>(path, { params });
  return response.data.data;
}

export const ecobotApi = {
  status: () => get<EcobotStatus>('/ecobot/status'),
  state: () => get<EcobotRecord | null>('/ecobot/state'),
  stateHistory: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/state/history', { limit }),
  worldEvents: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/world/events', { limit }),
  memories: (limit = 100) => get<EcobotRecord[]>('/ecobot/memories', { limit }),
  relationships: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/relationships', { limit }),
  affinities: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/affinities', { limit }),
  affinityEvents: (userId = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/affinity/events', {
      user_id: userId || undefined,
      limit,
    }),
  styleProfiles: (limit = 100) => get<EcobotRecord[]>('/ecobot/styles', { limit }),
  styleExamples: (userId: string, limit = 100) =>
    get<EcobotRecord[]>(`/ecobot/styles/${encodeURIComponent(userId)}/examples`, { limit }),
  backfillStyle: async (userId: string, limit = 5000) => {
    const response = await apiV1Client.post<EcobotEnvelope<{ inserted: number }>>(
      `/ecobot/styles/${encodeURIComponent(userId)}/backfill`,
      undefined,
      { params: { limit } },
    );
    return response.data.data;
  },
  autonomyEvents: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/events', { limit }),
  autonomyActions: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/actions', { limit }),
  autonomyConsequences: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/consequences', { limit }),
  autonomyAppraisals: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/appraisals', { limit }),
  autonomyLocations: (limit = 1000) => get<EcobotRecord[]>('/ecobot/autonomy/locations', { limit }),
  autonomyScenes: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/scenes', { limit }),
  autonomyIntentions: (status = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/intentions', { status: status || undefined, limit }),
  autonomyAttention: (limit = 100) => get<EcobotRecord[]>('/ecobot/autonomy/attention', { limit }),
  autonomyRevisions: (status = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/revisions', { status: status || undefined, limit }),
  autonomyThreads: (channelId = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/threads', { channel_id: channelId || undefined, limit }),
  autonomyDialogueActs: (eventId = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/dialogue-acts', { event_id: eventId || undefined, limit }),
  autonomyObligations: (status = 'pending', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/obligations', { status: status || undefined, limit }),
  autonomyBeliefs: (subject = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/beliefs', { subject: subject || undefined, limit }),
  autonomyCapabilities: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/capabilities', { limit }),
  autonomyWorldEntities: (kind = '', limit = 200) =>
    get<EcobotRecord[]>('/ecobot/autonomy/world-entities', { entity_kind: kind || undefined, limit }),
  autonomyScheduleFacts: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/schedule-facts', { limit }),
  autonomyWorldRules: (actionType = '') =>
    get<EcobotRecord[]>('/ecobot/autonomy/world-rules', { action_type: actionType || undefined }),
  autonomySceneExpansions: (limit = 100) =>
    get<EcobotRecord[]>('/ecobot/autonomy/scene-expansions', { limit }),
  createAutonomyIntent: async (command: {
    action_type: string;
    target_id?: string | null;
    arguments?: Record<string, unknown>;
    priority?: number;
    reason?: string;
    expires_at?: string | null;
  }) => {
    const response = await apiV1Client.post<EcobotEnvelope<EcobotRecord>>(
      '/ecobot/autonomy/intentions', command,
    );
    return response.data.data;
  },
  advanceAutonomyTick: async () => {
    const response = await apiV1Client.post<EcobotEnvelope<EcobotRecord>>('/ecobot/autonomy/tick');
    return response.data.data;
  },
  updateAffinity: async (
    userId: string,
    changes: {
      affinity_score: number;
      trust_score: number;
      familiarity: number;
      special_level?: 'none' | 'unforgivable' | 'supreme';
      reason?: string;
    },
  ) => {
    const response = await apiV1Client.patch<EcobotEnvelope<EcobotRecord>>(
      `/ecobot/affinities/${encodeURIComponent(userId)}`,
      changes,
    );
    return response.data.data;
  },
  batches: (limit = 100) => get<EcobotRecord[]>('/ecobot/batches', { limit }),
  actions: (limit = 100) => get<EcobotRecord[]>('/ecobot/actions', { limit }),
  traces: (limit = 100) => get<EcobotRecord[]>('/ecobot/traces', { limit }),
  users: (q = '', limit = 100) => get<EcobotRecord[]>('/ecobot/users', { q, limit }),
  groups: (q = '', limit = 100) => get<EcobotRecord[]>('/ecobot/groups', { q, limit }),
  messages: (q = '', limit = 100) =>
    get<EcobotRecord[]>('/ecobot/messages', { q, limit }),
  settings: () => get<EcobotSettings>('/ecobot/config'),
  updateSettings: async (changes: Partial<EcobotSettings>) => {
    const response = await apiV1Client.patch<EcobotEnvelope<EcobotSettings>>(
      '/ecobot/config',
      changes,
    );
    return response.data.data;
  },
};
