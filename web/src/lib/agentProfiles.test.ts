import { describe, expect, it } from 'vitest'
import {
  agentCapabilities,
  EXECUTOR_PROFILE,
  READY_PROFILES,
} from '@/test/agent'
import type { ExecutorBackendDraft } from './agentProfiles'
import {
  emptyPipelineAgentProfilesDraft,
  executorBackendDraftForNode,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
  pipelineProfilesEqual,
  clonePipelineAgentProfiles,
  withBackendKind,
  withExecutorDefault,
  withProfileDriver,
  withProfileModel,
} from './agentProfiles'

describe('agent profile draft/materialize', () => {
  it('并发策略随预览与历史快照完整保留，任一限制漂移使预览失效', () => {
    const original = { ...READY_PROFILES, concurrency: {
      node_overrides: { 'node.dynamic-a': 6 }, per_book: 6, per_provider: 8,
    } }
    const draft = pipelineDraftOf(original)
    const result = materializePipelineAgentProfiles(agentCapabilities(), draft)
    expect(result).toEqual({ profiles: original, errors: [] })
    const copied = clonePipelineAgentProfiles(original)
    expect(pipelineProfilesEqual(copied, original)).toBe(true)
    copied.concurrency!.node_overrides['node.dynamic-a'] = 8
    expect(original.concurrency.node_overrides['node.dynamic-a']).toBe(6)
    expect(pipelineProfilesEqual(copied, original)).toBe(false)
    expect(pipelineProfilesEqual(original, READY_PROFILES)).toBe(false)
    draft.concurrency!.per_book = 0
    expect(materializePipelineAgentProfiles(agentCapabilities(), draft).profiles).toBeNull()
    draft.concurrency!.per_book = 6
    draft.concurrency!.node_overrides['node.dynamic-a'] = 7
    expect(materializePipelineAgentProfiles(agentCapabilities(), draft).profiles).toBeNull()
  })

  it('null 默认保持全空，完整动态选择才 materialize', () => {
    const capabilities = agentCapabilities(null)
    const empty = pipelineDraftOf(capabilities.default_profiles)
    expect(empty).toEqual(emptyPipelineAgentProfilesDraft())
    expect(materializePipelineAgentProfiles(capabilities, empty)).toMatchObject({
      profiles: null,
      errors: expect.arrayContaining([
        '默认执行者：请选择 Agent 或 Legacy API',
      ]),
    })

    const complete = pipelineDraftOf(READY_PROFILES)
    expect(materializePipelineAgentProfiles(capabilities, complete)).toEqual({
      profiles: READY_PROFILES,
      errors: [],
    })
  })

  it('driver/model 改变会清空全部下游选择', () => {
    const draft = pipelineDraftOf(READY_PROFILES).executor_default.agent_profile
    expect(withProfileDriver(draft, 'another-driver')).toEqual({
      driver: 'another-driver',
      model: '',
      reasoning_effort: '',
      execution_mode: '',
    })
    expect(withProfileModel(draft, 'executor-model-v8')).toEqual({
      driver: EXECUTOR_PROFILE.driver,
      model: 'executor-model-v8',
      reasoning_effort: '',
      execution_mode: '',
    })
  })

  it('关闭式拒绝非 ready、错误 model/effort 与未知 override', () => {
    const capabilities = agentCapabilities(null)
    capabilities.drivers[0].availability = 'unknown'
    capabilities.drivers[0].availability_reason = '探针没有结论'
    const draft = pipelineDraftOf(READY_PROFILES)
    const validExecutor = {
      ...draft.executor_default,
      agent_profile: { ...draft.executor_default.agent_profile },
      legacy_profile: { ...draft.executor_default.legacy_profile },
    }
    draft.executor_default = {
      ...validExecutor,
      agent_profile: {
        ...validExecutor.agent_profile,
        reasoning_effort: 'not-published',
      },
    }
    draft.executor_overrides['node.dynamic-b'] = {
      ...validExecutor,
      agent_profile: {
        ...validExecutor.agent_profile,
        model: 'not-this-driver',
      },
    }
    draft.executor_overrides['unknown.node'] = {
      ...validExecutor,
      agent_profile: { ...validExecutor.agent_profile },
      legacy_profile: { ...validExecutor.legacy_profile },
    }
    const result = materializePipelineAgentProfiles(capabilities, draft)
    expect(result.profiles).toBeNull()
    expect(result.errors.join('\n')).toMatch(/探针没有结论/)
    expect(result.errors.join('\n')).toMatch(/not-published/)
    expect(result.errors.join('\n')).toMatch(/not-this-driver/)
    expect(result.errors.join('\n')).toMatch(/unknown\.node/)
  })

  it('深比较忽略 override 键序，但不忽略任一 profile 字段', () => {
    const left = {
      ...READY_PROFILES,
      executor_overrides: {
        b: { kind: 'agent' as const, profile: { ...EXECUTOR_PROFILE } },
        a: { kind: 'agent' as const, profile: { ...EXECUTOR_PROFILE } },
      },
    }
    const right = {
      ...READY_PROFILES,
      executor_overrides: {
        a: { kind: 'agent' as const, profile: { ...EXECUTOR_PROFILE } },
        b: { kind: 'agent' as const, profile: { ...EXECUTOR_PROFILE } },
      },
    }
    expect(pipelineProfilesEqual(left, right)).toBe(true)
    right.executor_overrides.a.profile.reasoning_effort = 'drift'
    expect(pipelineProfilesEqual(left, right)).toBe(false)
  })

  it('legacy 默认按节点能力校验，显式 Pro 覆盖后可冻结判别联合', () => {
    const capabilities = agentCapabilities(null)
    const draft = pipelineDraftOf(READY_PROFILES)
    draft.executor_default = withBackendKind(draft.executor_default, 'legacy')
    draft.executor_default.legacy_profile = {
      provider: 'deepseek',
      model: 'deepseek-flash',
      thinking_mode: 'on',
      reasoning_effort: 'high',
    }

    const refused = materializePipelineAgentProfiles(capabilities, draft)
    expect(refused.profiles).toBeNull()
    // 报错必须给出唯一合法动作：钉死模型 + 启用覆盖，而不是把人推向 Agent 模式。
    expect(refused.errors.join('\n')).toMatch(
      /无法承接节点 node\.dynamic-b（产品契约钉死 DeepSeek V4 Pro）；请为该节点启用覆盖并选择 DeepSeek V4 Pro/,
    )

    draft.executor_overrides['node.dynamic-b'] = {
      ...withBackendKind(draft.executor_default, 'legacy'),
      agent_profile: { ...draft.executor_default.agent_profile },
      legacy_profile: {
        provider: 'deepseek',
        model: 'deepseek-v4-pro',
        thinking_mode: 'on',
        reasoning_effort: 'high',
      },
    }
    // agent-only 节点没有 legacy 执行器：唯一合法配置是 agent 覆盖（单向豁免）。
    draft.executor_overrides['node.agent-only'] = {
      kind: 'agent',
      agent_profile: {
        ...EXECUTOR_PROFILE,
        execution_mode: EXECUTOR_PROFILE.execution_mode ?? '',
      },
      legacy_profile: { ...draft.executor_default.legacy_profile },
    }
    expect(materializePipelineAgentProfiles(capabilities, draft)).toEqual({
      profiles: {
        schema_version: 3,
        executor_default: {
          kind: 'legacy',
          profile: draft.executor_default.legacy_profile,
        },
        executor_overrides: {
          'node.dynamic-b': {
            kind: 'legacy',
            profile: draft.executor_overrides['node.dynamic-b'].legacy_profile,
          },
          'node.agent-only': {
            kind: 'agent',
            profile: EXECUTOR_PROFILE,
          },
        },
      },
      errors: [],
    })
  })

  it('节点覆盖不允许与默认执行者混用 Agent / Legacy 模式', () => {
    const capabilities = agentCapabilities(null)
    const draft = pipelineDraftOf(READY_PROFILES)
    draft.executor_default = withBackendKind(draft.executor_default, 'legacy')
    draft.executor_default.legacy_profile = {
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      thinking_mode: 'on',
      reasoning_effort: 'high',
    }

    const mixed = materializePipelineAgentProfiles(capabilities, {
      ...draft,
      executor_overrides: {
        'node.dynamic-a': {
          kind: 'agent',
          agent_profile: {
            ...EXECUTOR_PROFILE,
            execution_mode: EXECUTOR_PROFILE.execution_mode ?? '',
          },
          legacy_profile: { ...draft.executor_default.legacy_profile },
        },
      },
    })
    expect(mixed.profiles).toBeNull()
    expect(mixed.errors.join('\n')).toMatch(/默认 Legacy 时仅 Legacy 不支持的节点可用 Agent 覆盖/)

    // legacy 不承接的节点豁免：agent 覆盖是 agent-only 节点的唯一合法出路。
    const exempted = materializePipelineAgentProfiles(capabilities, {
      ...draft,
      executor_overrides: {
        'node.agent-only': {
          kind: 'agent',
          agent_profile: {
            ...EXECUTOR_PROFILE,
            execution_mode: EXECUTOR_PROFILE.execution_mode ?? '',
          },
          legacy_profile: { ...draft.executor_default.legacy_profile },
        },
      },
    })
    expect(exempted.errors).toEqual([])
    expect(exempted.profiles?.executor_overrides['node.agent-only']).toEqual({
      kind: 'agent',
      profile: EXECUTOR_PROFILE,
    })

    const sameKind = materializePipelineAgentProfiles(capabilities, {
      ...draft,
      executor_overrides: {
        'node.dynamic-b': {
          kind: 'legacy',
          agent_profile: { ...draft.executor_default.agent_profile },
          legacy_profile: { ...draft.executor_default.legacy_profile },
        },
        'node.agent-only': {
          kind: 'agent',
          agent_profile: {
            ...EXECUTOR_PROFILE,
            execution_mode: EXECUTOR_PROFILE.execution_mode ?? '',
          },
          legacy_profile: { ...draft.executor_default.legacy_profile },
        },
      },
    })
    expect(sameKind.errors).toEqual([])
    expect(sameKind.profiles?.executor_overrides['node.dynamic-b']).toEqual({
      kind: 'legacy',
      profile: draft.executor_default.legacy_profile,
    })
  })

  it('钉死节点：勾选覆盖自动预选可承接模型，默认切到 legacy 时自动补覆盖', () => {
    const capabilities = agentCapabilities(null)
    const flashLegacyDraft: ExecutorBackendDraft = {
      kind: 'legacy',
      agent_profile: { ...EXECUTOR_PROFILE, execution_mode: EXECUTOR_PROFILE.execution_mode ?? '' },
      legacy_profile: {
        provider: 'deepseek', model: 'deepseek-flash',
        thinking_mode: 'on', reasoning_effort: 'high',
      },
    }

    // 勾选钉死节点：初始草稿换成 V4 Pro，而不是复制默认 Flash 后继续报错。
    const override = executorBackendDraftForNode(capabilities, flashLegacyDraft, 'node.dynamic-b')
    expect(override.legacy_profile).toEqual({
      provider: 'deepseek', model: 'deepseek-v4-pro',
      thinking_mode: 'on', reasoning_effort: 'high',
    })
    // 默认模型可承接的节点与 agent 后端：原样副本。
    expect(executorBackendDraftForNode(capabilities, flashLegacyDraft, 'node.dynamic-a'))
      .toEqual(flashLegacyDraft)

    // 默认执行者切到 legacy Flash：承接不了的 node.dynamic-b 自动补成 Pro 覆盖，
    // agent-only 的 node.agent-only 自动补成空 Agent 草稿覆盖（不猜 driver），
    // 可承接的 node.dynamic-a 保持继承；materialize 只差待选 driver ⇒ 纯 legacy 可整跑。
    const draft = emptyPipelineAgentProfilesDraft()
    const swept = withExecutorDefault(capabilities, draft, flashLegacyDraft)
    expect(Object.keys(swept.executor_overrides)).toEqual(['node.dynamic-b', 'node.agent-only'])
    expect(swept.executor_overrides['node.agent-only'].kind).toEqual('agent')
    const materialized = materializePipelineAgentProfiles(capabilities, swept)
    expect(materialized.errors).toEqual(['节点 node.agent-only：请选择 driver'])
    expect(materialized.profiles).toBeNull()
    swept.executor_overrides['node.agent-only'].agent_profile = {
      ...EXECUTOR_PROFILE,
      execution_mode: EXECUTOR_PROFILE.execution_mode ?? '',
    }
    const completed = materializePipelineAgentProfiles(capabilities, swept)
    expect(completed.errors).toEqual([])
    expect(completed.profiles?.executor_default).toEqual({
      kind: 'legacy', profile: flashLegacyDraft.legacy_profile,
    })
    expect(completed.profiles?.executor_overrides['node.dynamic-b']).toEqual({
      kind: 'legacy',
      profile: override.legacy_profile,
    })
    expect(completed.profiles?.executor_overrides['node.agent-only']).toEqual({
      kind: 'agent',
      profile: EXECUTOR_PROFILE,
    })

    // 用户显式取消该覆盖后不再抢回去，物化给出唯一合法动作的报错。
    const unchecked = {
      ...swept,
      executor_overrides: Object.fromEntries(
        Object.entries(swept.executor_overrides).filter(([node]) => node !== 'node.dynamic-b'),
      ),
    }
    const refused = materializePipelineAgentProfiles(capabilities, unchecked)
    expect(refused.profiles).toBeNull()
    expect(refused.errors.join('\n')).toMatch(/产品契约钉死 DeepSeek V4 Pro/)
  })
})
