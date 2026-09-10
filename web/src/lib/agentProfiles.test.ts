import { describe, expect, it } from 'vitest'
import {
  agentCapabilities,
  COMMANDER_PROFILE,
  EXECUTOR_PROFILE,
  READY_PROFILES,
} from '@/test/agent'
import {
  emptyPipelineAgentProfilesDraft,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
  pipelineProfilesEqual,
  clonePipelineAgentProfiles,
  withBackendKind,
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
        '指挥大脑：请选择 driver',
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
    const draft = pipelineDraftOf(READY_PROFILES).commander
    expect(withProfileDriver(draft, 'another-driver')).toEqual({
      driver: 'another-driver',
      model: '',
      reasoning_effort: '',
      execution_mode: '',
    })
    expect(withProfileModel(draft, 'commander-model-v10')).toEqual({
      driver: COMMANDER_PROFILE.driver,
      model: 'commander-model-v10',
      reasoning_effort: '',
      execution_mode: '',
    })
  })

  it('关闭式拒绝非 ready、错误 role/model/effort 与未知 override', () => {
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
    draft.executor_overrides['node.dynamic-a'] = {
      ...validExecutor,
      agent_profile: { ...draft.commander },
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
    expect(result.errors.join('\n')).toMatch(/不支持 executor 角色/)
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
      model: 'deepseek-v4-flash',
      thinking_mode: 'on',
      reasoning_effort: 'high',
    }

    const refused = materializePipelineAgentProfiles(capabilities, draft)
    expect(refused.profiles).toBeNull()
    expect(refused.errors.join('\n')).toMatch(/不支持节点 node\.dynamic-b/)

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
    expect(materializePipelineAgentProfiles(capabilities, draft)).toEqual({
      profiles: {
        schema_version: 2,
        commander: COMMANDER_PROFILE,
        executor_default: {
          kind: 'legacy',
          profile: draft.executor_default.legacy_profile,
        },
        executor_overrides: {
          'node.dynamic-b': {
            kind: 'legacy',
            profile: draft.executor_overrides['node.dynamic-b'].legacy_profile,
          },
        },
      },
      errors: [],
    })
  })
})
