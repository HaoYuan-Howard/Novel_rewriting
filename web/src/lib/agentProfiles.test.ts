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
  withProfileDriver,
  withProfileModel,
} from './agentProfiles'

describe('agent profile draft/materialize', () => {
  it('null 默认保持全空，完整动态选择才 materialize', () => {
    const capabilities = agentCapabilities(null)
    const empty = pipelineDraftOf(capabilities.default_profiles)
    expect(empty).toEqual(emptyPipelineAgentProfilesDraft())
    expect(materializePipelineAgentProfiles(capabilities, empty)).toMatchObject({
      profiles: null,
      errors: expect.arrayContaining(['指挥大脑：请选择 driver', '默认执行者：请选择 driver']),
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
    const validExecutor = { ...draft.executor_default }
    draft.executor_default = {
      ...draft.executor_default,
      reasoning_effort: 'not-published',
    }
    draft.executor_overrides['node.dynamic-a'] = { ...draft.commander }
    draft.executor_overrides['node.dynamic-b'] = {
      ...validExecutor,
      model: 'not-this-driver',
    }
    draft.executor_overrides['unknown.node'] = validExecutor
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
        b: { ...EXECUTOR_PROFILE },
        a: { ...EXECUTOR_PROFILE },
      },
    }
    const right = {
      ...READY_PROFILES,
      executor_overrides: {
        a: { ...EXECUTOR_PROFILE },
        b: { ...EXECUTOR_PROFILE },
      },
    }
    expect(pipelineProfilesEqual(left, right)).toBe(true)
    right.executor_overrides.a.reasoning_effort = 'drift'
    expect(pipelineProfilesEqual(left, right)).toBe(false)
  })
})
