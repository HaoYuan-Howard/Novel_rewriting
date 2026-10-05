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
  withExecutorDefault,
  withLegacyModel,
  withLegacyProvider,
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
    expect(materializePipelineAgentProfiles(agentCapabilities(), draft).profiles?.concurrency?.node_overrides['node.dynamic-a']).toBe(7)
    draft.concurrency!.node_overrides['node.dynamic-a'] = 201
    expect(materializePipelineAgentProfiles(agentCapabilities(), draft).profiles).toBeNull()
  })

  it('null 默认保持全空，完整动态选择才 materialize', () => {
    const capabilities = agentCapabilities(null)
    const empty = pipelineDraftOf(capabilities.default_profiles)
    expect(empty).toEqual(emptyPipelineAgentProfilesDraft())
    expect(materializePipelineAgentProfiles(capabilities, empty)).toMatchObject({
      profiles: null,
      errors: expect.arrayContaining([
        '默认模型执行配置：请选择 Provider',
      ]),
    })

    const complete = pipelineDraftOf(READY_PROFILES)
    expect(materializePipelineAgentProfiles(capabilities, complete)).toEqual({
      profiles: READY_PROFILES,
      errors: [],
    })
  })

  it('Provider/Model 改变会清空全部下游选择', () => {
    const draft = pipelineDraftOf(READY_PROFILES).executor_default.profile
    expect(withLegacyProvider(draft, 'other-provider')).toEqual({
      provider: 'other-provider',
      model: '',
      reasoning_effort: '',
      thinking_mode: '',
    })
    expect(withLegacyModel(draft, 'deepseek-flash')).toEqual({
      provider: EXECUTOR_PROFILE.provider,
      model: 'deepseek-flash',
      reasoning_effort: '',
      thinking_mode: '',
    })
  })

  it('关闭式拒绝错误 model/effort 与未知 override', () => {
    const capabilities = agentCapabilities(null)
    const draft = pipelineDraftOf(READY_PROFILES)
    const validExecutor = {
      ...draft.executor_default,
      profile: { ...draft.executor_default.profile },
    }
    draft.executor_default = {
      ...validExecutor,
      profile: {
        ...validExecutor.profile,
        reasoning_effort: 'not-published',
      },
    }
    draft.executor_overrides['node.dynamic-b'] = {
      ...validExecutor,
      profile: {
        ...validExecutor.profile,
        model: 'not-this-provider',
      },
    }
    draft.executor_overrides['unknown.node'] = {
      ...validExecutor,
      profile: { ...validExecutor.profile },
    }
    const result = materializePipelineAgentProfiles(capabilities, draft)
    expect(result.profiles).toBeNull()
    expect(result.errors.join('\n')).toMatch(/not-published/)
    expect(result.errors.join('\n')).toMatch(/not-this-provider/)
    expect(result.errors.join('\n')).toMatch(/unknown\.node/)
  })

  it('深比较忽略 override 键序，但不忽略任一 profile 字段', () => {
    const left = {
      ...READY_PROFILES,
      executor_overrides: {
        b: { kind: 'legacy' as const, profile: { ...EXECUTOR_PROFILE } },
        a: { kind: 'legacy' as const, profile: { ...EXECUTOR_PROFILE } },
      },
    }
    const right = {
      ...READY_PROFILES,
      executor_overrides: {
        a: { kind: 'legacy' as const, profile: { ...EXECUTOR_PROFILE } },
        b: { kind: 'legacy' as const, profile: { ...EXECUTOR_PROFILE } },
      },
    }
    expect(pipelineProfilesEqual(left, right)).toBe(true)
    right.executor_overrides.a.profile.reasoning_effort = 'drift'
    expect(pipelineProfilesEqual(left, right)).toBe(false)
  })

  it('默认按节点能力校验，显式 Pro 覆盖后可冻结配置', () => {
    const capabilities = agentCapabilities(null)
    const draft = pipelineDraftOf(READY_PROFILES)
    draft.executor_default = {
      kind: 'legacy',
      profile: {
        provider: 'deepseek',
        model: 'deepseek-flash',
        thinking_mode: 'on',
        reasoning_effort: 'high',
      },
    }

    const refused = materializePipelineAgentProfiles(capabilities, draft)
    expect(refused.profiles).toBeNull()
    expect(refused.errors.join('\n')).toMatch(
      /无法承接节点 node\.dynamic-b（产品契约钉死 DeepSeek V4 Pro）；请为该节点启用覆盖并选择 DeepSeek V4 Pro/,
    )

    draft.executor_overrides['node.dynamic-b'] = {
      kind: 'legacy',
      profile: {
        provider: 'deepseek',
        model: 'deepseek-v4-pro',
        thinking_mode: 'on',
        reasoning_effort: 'high',
      },
    }
    expect(materializePipelineAgentProfiles(capabilities, draft)).toEqual({
      profiles: {
        schema_version: 4,
        executor_default: {
          kind: 'legacy',
          profile: draft.executor_default.profile,
        },
        executor_overrides: {
          'node.dynamic-b': {
            kind: 'legacy',
            profile: draft.executor_overrides['node.dynamic-b'].profile,
          },
        },
      },
      errors: [],
    })
  })

  it('钉死节点：勾选覆盖自动预选可承接模型，默认模型承接不了时自动补覆盖', () => {
    const capabilities = agentCapabilities(null)
    const flashDraft: ExecutorBackendDraft = {
      kind: 'legacy',
      profile: {
        provider: 'deepseek', model: 'deepseek-flash',
        thinking_mode: 'on', reasoning_effort: 'high',
      },
    }

    const override = executorBackendDraftForNode(capabilities, flashDraft, 'node.dynamic-b')
    expect(override.profile).toEqual({
      provider: 'deepseek', model: 'deepseek-v4-pro',
      thinking_mode: 'on', reasoning_effort: 'high',
    })
    expect(executorBackendDraftForNode(capabilities, flashDraft, 'node.dynamic-a'))
      .toEqual({ ...flashDraft, profile: { ...flashDraft.profile } })

    const draft = emptyPipelineAgentProfilesDraft()
    const swept = withExecutorDefault(capabilities, draft, flashDraft)
    expect(Object.keys(swept.executor_overrides)).toEqual(['node.dynamic-b'])
    const completed = materializePipelineAgentProfiles(capabilities, swept)
    expect(completed.errors).toEqual([])
    expect(completed.profiles?.executor_default).toEqual({
      kind: 'legacy', profile: flashDraft.profile,
    })
    expect(completed.profiles?.executor_overrides['node.dynamic-b']).toEqual({
      kind: 'legacy',
      profile: override.profile,
    })

    const unchecked = {
      ...swept,
      executor_overrides: {},
    }
    const refused = materializePipelineAgentProfiles(capabilities, unchecked)
    expect(refused.profiles).toBeNull()
    expect(refused.errors.join('\n')).toMatch(/产品契约钉死 DeepSeek V4 Pro/)
  })
})
