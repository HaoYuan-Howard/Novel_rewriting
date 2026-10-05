import type {
  AgentCapabilitiesResponse,
  ExecutorBackend,
  LegacyLlmProfile,
  PipelineAgentProfiles,
} from '@/types/agent'

export const EXECUTOR_PROFILE: LegacyLlmProfile = {
  provider: 'deepseek',
  model: 'deepseek-v4-pro',
  reasoning_effort: 'high',
  thinking_mode: 'on',
}

export const OVERRIDE_PROFILE: LegacyLlmProfile = {
  provider: 'deepseek',
  model: 'deepseek-v4-pro',
  reasoning_effort: 'max',
  thinking_mode: 'on',
}

export const EXECUTOR_BACKEND: ExecutorBackend = {
  kind: 'legacy',
  profile: EXECUTOR_PROFILE,
}

export const OVERRIDE_BACKEND: ExecutorBackend = {
  kind: 'legacy',
  profile: OVERRIDE_PROFILE,
}

export const READY_PROFILES: PipelineAgentProfiles = {
  schema_version: 4,
  executor_default: EXECUTOR_BACKEND,
  executor_overrides: {},
}

function cloneProfiles(profiles: PipelineAgentProfiles): PipelineAgentProfiles {
  return {
    schema_version: 4,
    executor_default: { kind: 'legacy', profile: { ...profiles.executor_default.profile } },
    ...(profiles.concurrency
      ? { concurrency: { ...profiles.concurrency, node_overrides: { ...profiles.concurrency.node_overrides } } }
      : {}),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        { kind: 'legacy' as const, profile: { ...backend.profile } },
      ]),
    ),
  }
}

export function agentCapabilities(
  defaultProfiles: PipelineAgentProfiles | null = READY_PROFILES,
): AgentCapabilitiesResponse {
  return {
    legacy_providers: [
      {
        id: 'deepseek',
        label: 'DeepSeek API',
        models: [
          {
            id: 'deepseek-flash',
            label: 'DeepSeek V4.1 Flash',
            thinking_modes: [
              { id: 'on', label: '开启思考', reasoning_efforts: ['low', 'high', 'max'] },
              { id: 'off', label: '关闭思考', reasoning_efforts: ['low'] },
            ],
            executor_nodes: ['node.dynamic-a'],
          },
          {
            id: 'deepseek-v4-pro',
            label: 'DeepSeek V4 Pro',
            thinking_modes: [
              { id: 'on', label: '开启思考', reasoning_efforts: ['low', 'high', 'max'] },
              { id: 'off', label: '关闭思考', reasoning_efforts: ['low'] },
            ],
            executor_nodes: ['node.dynamic-a', 'node.dynamic-b'],
          },
        ],
      },
    ],
    executor_nodes: ['node.dynamic-a', 'node.dynamic-b'],
    // node.dynamic-b 只在 V4 Pro 的 executor_nodes 里 ⇒ 前端按钉死表预选覆盖模型。
    legacy_model_pins: { 'node.dynamic-b': 'deepseek-v4-pro' },
    concurrency: { nodes: ['node.dynamic-a'], node_choices: [], default_node: 4,
      defaults: { node_overrides: {}, per_book: 4, per_provider: 8 },
      total_min: 1, total_max: 200, scope: 'output_root' },
    default_profiles: defaultProfiles === null ? null : cloneProfiles(defaultProfiles),
  }
}

export function previewOf(
  profiles: PipelineAgentProfiles = READY_PROFILES,
  authorized = false,
  egressAuthorized = true,
) {
  return {
    argv: ['agent-dynamic', '--profile-snapshot'],
    prompt: '动态 prompt',
    authorized,
    egress_authorized: egressAuthorized,
    profiles,
  }
}
