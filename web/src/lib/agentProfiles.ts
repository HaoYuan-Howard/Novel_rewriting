import type {
  AgentCapabilitiesResponse,
  AgentDriverCapability,
  AgentProfile,
  AgentRole,
  PipelineAgentProfiles,
} from '@/types/agent'

/**
 * 表单态刻意允许半成品；只有 materialize 成功后的对象才准进入 preview/start。
 * 空串只存在于浏览器草稿，不是后端 profile 的一种取值。
 */
export interface AgentProfileDraft {
  driver: string
  model: string
  reasoning_effort: string
  execution_mode: string
}

export interface PipelineAgentProfilesDraft {
  commander: AgentProfileDraft
  executor_default: AgentProfileDraft
  /** 键存在表示启用覆盖；没有键表示继承 executor_default。 */
  executor_overrides: Record<string, AgentProfileDraft>
}

export interface MaterializedProfiles {
  profiles: PipelineAgentProfiles | null
  errors: string[]
}

export function emptyAgentProfileDraft(): AgentProfileDraft {
  return { driver: '', model: '', reasoning_effort: '', execution_mode: '' }
}

export function emptyPipelineAgentProfilesDraft(): PipelineAgentProfilesDraft {
  return {
    commander: emptyAgentProfileDraft(),
    executor_default: emptyAgentProfileDraft(),
    executor_overrides: {},
  }
}

export function profileDraftOf(profile: AgentProfile): AgentProfileDraft {
  return {
    driver: profile.driver,
    model: profile.model,
    reasoning_effort: profile.reasoning_effort,
    execution_mode: profile.execution_mode ?? '',
  }
}

/** null 明确生成全空草稿，绝不拿 capability 数组第一项伪造默认。 */
export function pipelineDraftOf(
  profiles: PipelineAgentProfiles | null,
): PipelineAgentProfilesDraft {
  if (profiles === null) return emptyPipelineAgentProfilesDraft()
  return {
    commander: profileDraftOf(profiles.commander),
    executor_default: profileDraftOf(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, profile]) => [
        node,
        profileDraftOf(profile),
      ]),
    ),
  }
}

/** driver 变化使 model/effort/mode 全部失去判定域。 */
export function withProfileDriver(
  draft: AgentProfileDraft,
  driver: string,
): AgentProfileDraft {
  if (driver === draft.driver) return draft
  return { driver, model: '', reasoning_effort: '', execution_mode: '' }
}

/** model 变化使 effort/mode 失去判定域；不能沿用前一个模型的同名字面量。 */
export function withProfileModel(
  draft: AgentProfileDraft,
  model: string,
): AgentProfileDraft {
  if (model === draft.model) return draft
  return { ...draft, model, reasoning_effort: '', execution_mode: '' }
}

export function driverForDraft(
  capabilities: AgentCapabilitiesResponse,
  draft: AgentProfileDraft,
): AgentDriverCapability | null {
  return capabilities.drivers.find((driver) => driver.id === draft.driver) ?? null
}

function materializeProfile(
  capabilities: AgentCapabilitiesResponse,
  draft: AgentProfileDraft,
  role: AgentRole,
  label: string,
): { profile: AgentProfile | null; errors: string[] } {
  const errors: string[] = []
  if (!draft.driver) {
    errors.push(`${label}：请选择 driver`)
    return { profile: null, errors }
  }
  const driver = driverForDraft(capabilities, draft)
  if (driver === null) {
    errors.push(`${label}：driver ${draft.driver} 已不在当前 capability 中`)
    return { profile: null, errors }
  }
  if (!driver.roles.includes(role)) {
    errors.push(`${label}：${driver.label} 不支持 ${role} 角色`)
  }
  if (driver.availability !== 'ready') {
    errors.push(
      `${label}：${driver.label} 当前为 ${driver.availability}：${driver.availability_reason}`,
    )
  }
  if (!draft.model) {
    errors.push(`${label}：请选择模型`)
    return { profile: null, errors }
  }
  const model = driver.models.find((candidate) => candidate.id === draft.model) ?? null
  if (model === null) {
    errors.push(`${label}：模型 ${draft.model} 不属于当前 driver`)
    return { profile: null, errors }
  }
  if (!draft.reasoning_effort) {
    errors.push(`${label}：请选择思考强度`)
  } else if (!model.reasoning_efforts.includes(draft.reasoning_effort)) {
    errors.push(`${label}：思考强度 ${draft.reasoning_effort} 不属于当前模型`)
  }

  let executionMode: string | null = null
  if (model.execution_modes.length > 0) {
    if (!draft.execution_mode) {
      errors.push(`${label}：请选择执行模式`)
    } else if (!model.execution_modes.includes(draft.execution_mode)) {
      errors.push(`${label}：执行模式 ${draft.execution_mode} 不属于当前模型`)
    } else {
      executionMode = draft.execution_mode
    }
  } else if (draft.execution_mode) {
    errors.push(`${label}：当前模型没有可显式选择的执行模式`)
  }

  if (errors.length > 0) return { profile: null, errors }
  return {
    profile: {
      driver: driver.id,
      model: model.id,
      reasoning_effort: draft.reasoning_effort,
      execution_mode: executionMode,
    },
    errors: [],
  }
}

/**
 * 对 capability 做关闭式校验并生成后端形状。任何一格不完整/过期/不可用都返回
 * profiles=null；调用方据此关闭 preview 与 start，而不是修补一个近似配置。
 */
export function materializePipelineAgentProfiles(
  capabilities: AgentCapabilitiesResponse,
  draft: PipelineAgentProfilesDraft,
): MaterializedProfiles {
  const commander = materializeProfile(
    capabilities,
    draft.commander,
    'commander',
    '指挥大脑',
  )
  const executorDefault = materializeProfile(
    capabilities,
    draft.executor_default,
    'executor',
    '默认执行者',
  )
  const errors = [...commander.errors, ...executorDefault.errors]
  const overrides: Record<string, AgentProfile> = {}
  const knownNodes = new Set(capabilities.executor_nodes)

  for (const node of Object.keys(draft.executor_overrides)) {
    if (!knownNodes.has(node)) errors.push(`节点覆盖：${node} 已不在当前 capability 中`)
  }
  for (const node of capabilities.executor_nodes) {
    const override = draft.executor_overrides[node]
    if (!override) continue
    const result = materializeProfile(
      capabilities,
      override,
      'executor',
      `节点 ${node}`,
    )
    errors.push(...result.errors)
    if (result.profile) overrides[node] = result.profile
  }

  if (!commander.profile || !executorDefault.profile || errors.length > 0) {
    return { profiles: null, errors }
  }
  return {
    profiles: {
      commander: commander.profile,
      executor_default: executorDefault.profile,
      executor_overrides: overrides,
    },
    errors: [],
  }
}

export function cloneAgentProfile(profile: AgentProfile): AgentProfile {
  return { ...profile }
}

/** 手写深拷贝避免 JSON 序列化把未来显式 null/字段语义压扁。 */
export function clonePipelineAgentProfiles(
  profiles: PipelineAgentProfiles,
): PipelineAgentProfiles {
  return {
    commander: cloneAgentProfile(profiles.commander),
    executor_default: cloneAgentProfile(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, profile]) => [
        node,
        cloneAgentProfile(profile),
      ]),
    ),
  }
}

function profileEqual(left: AgentProfile, right: AgentProfile): boolean {
  return left.driver === right.driver
    && left.model === right.model
    && left.reasoning_effort === right.reasoning_effort
    && left.execution_mode === right.execution_mode
}

/** preview 必须逐格回显同一份配置；键序不同不算漂移，增删/改值都算。 */
export function pipelineProfilesEqual(
  left: PipelineAgentProfiles | null,
  right: PipelineAgentProfiles | null,
): boolean {
  if (left === null || right === null) return left === right
  if (!profileEqual(left.commander, right.commander)) return false
  if (!profileEqual(left.executor_default, right.executor_default)) return false
  const leftNodes = Object.keys(left.executor_overrides).sort()
  const rightNodes = Object.keys(right.executor_overrides).sort()
  if (leftNodes.length !== rightNodes.length) return false
  return leftNodes.every((node, index) => {
    const other = rightNodes[index]
    return node === other
      && profileEqual(left.executor_overrides[node], right.executor_overrides[other])
  })
}
