import type {
  AgentCapabilitiesResponse,
  AgentProfile,
  ExecutorBackend,
  PipelineAgentProfiles,
} from '@/types/agent'

export const EXECUTOR_PROFILE: AgentProfile = {
  driver: 'executor-dynamic',
  model: 'executor-model-v7',
  reasoning_effort: 'deep-dynamic',
  execution_mode: 'worker-dynamic',
}

export const OVERRIDE_PROFILE: AgentProfile = {
  driver: 'executor-dynamic',
  model: 'executor-model-v8',
  reasoning_effort: 'fast-dynamic',
  execution_mode: 'worker-dynamic',
}

export const EXECUTOR_BACKEND: ExecutorBackend = {
  kind: 'agent',
  profile: EXECUTOR_PROFILE,
}

export const OVERRIDE_BACKEND: ExecutorBackend = {
  kind: 'agent',
  profile: OVERRIDE_PROFILE,
}

export const READY_PROFILES: PipelineAgentProfiles = {
  schema_version: 3,
  executor_default: EXECUTOR_BACKEND,
  executor_overrides: {},
}

function cloneProfiles(profiles: PipelineAgentProfiles): PipelineAgentProfiles {
  return {
    schema_version: 3,
    executor_default: profiles.executor_default.kind === 'agent'
      ? { kind: 'agent', profile: { ...profiles.executor_default.profile } }
      : { kind: 'legacy', profile: { ...profiles.executor_default.profile } },
    ...(profiles.concurrency
      ? { concurrency: { ...profiles.concurrency, node_overrides: { ...profiles.concurrency.node_overrides } } }
      : {}),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        backend.kind === 'agent'
          ? { kind: 'agent', profile: { ...backend.profile } }
          : { kind: 'legacy', profile: { ...backend.profile } },
      ]),
    ),
  }
}

export function agentCapabilities(
  defaultProfiles: PipelineAgentProfiles | null = READY_PROFILES,
): AgentCapabilitiesResponse {
  return {
    drivers: [
      {
        id: 'executor-dynamic',
        provider: 'provider-b',
        label: '动态执行 CLI',
        availability: 'ready',
        availability_reason: '动态执行探针通过',
        version: 'v-executor',
        models: [
          {
            id: 'executor-model-v7',
            label: '动态执行模型七',
            reasoning_efforts: ['deep-dynamic'],
            execution_modes: ['worker-dynamic'],
          },
          {
            id: 'executor-model-v8',
            label: '动态执行模型八',
            reasoning_efforts: ['fast-dynamic'],
            execution_modes: ['worker-dynamic'],
          },
        ],
      },
    ],
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
    executor_nodes: ['node.dynamic-a', 'node.dynamic-b', 'node.agent-only'],
    // node.dynamic-b 只在 V4 Pro 的 executor_nodes 里 ⇒ 前端按钉死表预选覆盖模型。
    // node.agent-only 不在任何 legacy 模型里 ⇒ agent-only：默认 legacy 时
    // 唯一合法的 agent 覆盖豁免节点。
    legacy_model_pins: { 'node.dynamic-b': 'deepseek-v4-pro' },
    concurrency: { nodes: ['node.dynamic-a'], node_choices: [4, 6, 8], default_node: 4,
      defaults: { node_overrides: {}, per_book: 4, per_provider: 8 },
      total_min: 1, total_max: 32, scope: 'output_root' },
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
