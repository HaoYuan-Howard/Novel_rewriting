import type {
  AgentCapabilitiesResponse,
  AgentProfile,
  PipelineAgentProfiles,
} from '@/types/agent'

export const COMMANDER_PROFILE: AgentProfile = {
  driver: 'commander-dynamic',
  model: 'commander-model-v9',
  reasoning_effort: 'careful-dynamic',
  execution_mode: 'interactive-dynamic',
}

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

export const READY_PROFILES: PipelineAgentProfiles = {
  commander: COMMANDER_PROFILE,
  executor_default: EXECUTOR_PROFILE,
  executor_overrides: {},
}

function cloneProfiles(profiles: PipelineAgentProfiles): PipelineAgentProfiles {
  return {
    commander: { ...profiles.commander },
    executor_default: { ...profiles.executor_default },
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, profile]) => [
        node,
        { ...profile },
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
        id: 'commander-dynamic',
        provider: 'provider-a',
        label: '动态指挥 CLI',
        roles: ['commander'],
        availability: 'ready',
        availability_reason: '动态指挥探针通过',
        version: 'v-command',
        models: [
          {
            id: 'commander-model-v9',
            label: '动态指挥模型九',
            reasoning_efforts: ['careful-dynamic', 'slow-dynamic'],
            execution_modes: ['interactive-dynamic'],
          },
          {
            id: 'commander-model-v10',
            label: '动态指挥模型十',
            reasoning_efforts: ['new-effort-dynamic'],
            execution_modes: ['new-mode-dynamic'],
          },
        ],
      },
      {
        id: 'executor-dynamic',
        provider: 'provider-b',
        label: '动态执行 CLI',
        roles: ['executor'],
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
    executor_nodes: ['node.dynamic-a', 'node.dynamic-b'],
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
