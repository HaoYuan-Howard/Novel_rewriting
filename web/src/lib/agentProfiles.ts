import type {
  AgentCapabilitiesResponse,
  AgentDriverCapability,
  AgentProfile,
  AgentRole,
  ExecutorBackend,
  LegacyLlmProfile,
  LegacyProviderCapability,
  PipelineAgentProfiles,
  PipelineConcurrency,
} from '@/types/agent'

/** 浏览器草稿允许半成品；只有 materialize 成功后的对象才准进入 preview/start。 */
export interface AgentProfileDraft {
  driver: string
  model: string
  reasoning_effort: string
  execution_mode: string
}

export interface LegacyLlmProfileDraft {
  provider: string
  model: string
  reasoning_effort: string
  thinking_mode: string
}

export interface ExecutorBackendDraft {
  kind: '' | 'agent' | 'legacy'
  agent_profile: AgentProfileDraft
  legacy_profile: LegacyLlmProfileDraft
}

export interface PipelineAgentProfilesDraft {
  commander: AgentProfileDraft
  executor_default: ExecutorBackendDraft
  /** 键存在表示启用覆盖；没有键表示继承 executor_default。 */
  executor_overrides: Record<string, ExecutorBackendDraft>
  concurrency?: PipelineConcurrency
}

export interface MaterializedProfiles {
  profiles: PipelineAgentProfiles | null
  errors: string[]
}

export function emptyAgentProfileDraft(): AgentProfileDraft {
  return { driver: '', model: '', reasoning_effort: '', execution_mode: '' }
}

export function emptyLegacyLlmProfileDraft(): LegacyLlmProfileDraft {
  return { provider: '', model: '', reasoning_effort: '', thinking_mode: '' }
}

export function emptyExecutorBackendDraft(): ExecutorBackendDraft {
  return {
    kind: '',
    agent_profile: emptyAgentProfileDraft(),
    legacy_profile: emptyLegacyLlmProfileDraft(),
  }
}

export function emptyPipelineAgentProfilesDraft(): PipelineAgentProfilesDraft {
  return {
    commander: emptyAgentProfileDraft(),
    executor_default: emptyExecutorBackendDraft(),
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

export function legacyProfileDraftOf(profile: LegacyLlmProfile): LegacyLlmProfileDraft {
  return {
    provider: profile.provider,
    model: profile.model,
    reasoning_effort: profile.reasoning_effort,
    thinking_mode: profile.thinking_mode,
  }
}

export function executorBackendDraftOf(backend: ExecutorBackend): ExecutorBackendDraft {
  return backend.kind === 'agent'
    ? {
        kind: 'agent',
        agent_profile: profileDraftOf(backend.profile),
        legacy_profile: emptyLegacyLlmProfileDraft(),
      }
    : {
        kind: 'legacy',
        agent_profile: emptyAgentProfileDraft(),
        legacy_profile: legacyProfileDraftOf(backend.profile),
      }
}

/** null 明确生成全空草稿，绝不拿 capability 数组第一项伪造默认。 */
export function pipelineDraftOf(
  profiles: PipelineAgentProfiles | null,
): PipelineAgentProfilesDraft {
  if (profiles === null) return emptyPipelineAgentProfilesDraft()
  return {
    ...concurrencySnapshot(profiles.concurrency),
    commander: profileDraftOf(profiles.commander),
    executor_default: executorBackendDraftOf(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        executorBackendDraftOf(backend),
      ]),
    ),
  }
}

/** 切换 backend 只切 discriminator；两边草稿独立保留，避免来回试配丢输入。 */
export function withBackendKind(
  draft: ExecutorBackendDraft,
  kind: ExecutorBackendDraft['kind'],
): ExecutorBackendDraft {
  return { ...draft, kind }
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

export function withLegacyProvider(
  draft: LegacyLlmProfileDraft,
  provider: string,
): LegacyLlmProfileDraft {
  if (provider === draft.provider) return draft
  return { provider, model: '', reasoning_effort: '', thinking_mode: '' }
}

export function withLegacyModel(
  draft: LegacyLlmProfileDraft,
  model: string,
): LegacyLlmProfileDraft {
  if (model === draft.model) return draft
  return { ...draft, model, reasoning_effort: '', thinking_mode: '' }
}

export function withLegacyThinking(
  draft: LegacyLlmProfileDraft,
  thinkingMode: string,
): LegacyLlmProfileDraft {
  if (thinkingMode === draft.thinking_mode) return draft
  return { ...draft, thinking_mode: thinkingMode, reasoning_effort: '' }
}

export function driverForDraft(
  capabilities: AgentCapabilitiesResponse,
  draft: AgentProfileDraft,
): AgentDriverCapability | null {
  return capabilities.drivers.find((driver) => driver.id === draft.driver) ?? null
}

export function legacyProviderForDraft(
  capabilities: AgentCapabilitiesResponse,
  draft: LegacyLlmProfileDraft,
): LegacyProviderCapability | null {
  return capabilities.legacy_providers.find(
    (provider) => provider.id === draft.provider,
  ) ?? null
}

function legacyModelLabel(
  capabilities: AgentCapabilitiesResponse,
  modelId: string,
): string {
  for (const provider of capabilities.legacy_providers) {
    const model = provider.models.find((candidate) => candidate.id === modelId)
    if (model) return model.label
  }
  return modelId
}

/** 钉死/覆盖预选文案用的模型展示名；未知 id 原样展示，不猜。 */
export function legacyModelLabelById(
  capabilities: AgentCapabilitiesResponse,
  modelId: string,
): string {
  return legacyModelLabel(capabilities, modelId)
}

/**
 * 同一 provider 内真正承接该节点的 legacy 模型：优先产品钉死模型
 * （`legacy_model_pins`，质量裁决），没有钉死时取第一个可承接的。
 */
function legacyModelIdServingNode(
  capabilities: AgentCapabilitiesResponse,
  providerId: string,
  node: string,
): string | null {
  const pin = capabilities.legacy_model_pins[node]
  const provider = capabilities.legacy_providers.find(
    (candidate) => candidate.id === providerId,
  )
  if (!provider) return null
  const serving = provider.models.filter((model) =>
    model.executor_nodes.includes(node),
  )
  if (serving.length === 0) return null
  return serving.some((model) => model.id === pin)
    ? pin
    : serving[0].id
}

function clampLegacyThinking(
  capabilities: AgentCapabilitiesResponse,
  providerId: string,
  modelId: string,
  draft: LegacyLlmProfileDraft,
): { thinking_mode: string; reasoning_effort: string } {
  const provider = capabilities.legacy_providers.find(
    (candidate) => candidate.id === providerId,
  )
  const model = provider?.models.find((candidate) => candidate.id === modelId)
  const thinking = model?.thinking_modes.find(
    (candidate) => candidate.id === draft.thinking_mode,
  ) ?? model?.thinking_modes[0]
  if (!thinking) return { thinking_mode: draft.thinking_mode, reasoning_effort: draft.reasoning_effort }
  const effort = thinking.reasoning_efforts.includes(draft.reasoning_effort)
    ? draft.reasoning_effort
    : thinking.reasoning_efforts[0]
  return { thinking_mode: thinking.id, reasoning_effort: effort }
}

/**
 * 勾选节点覆盖时的初始草稿：默认执行者的副本，但当默认 legacy 模型承接不了
 * 该节点时，换成同 provider 内可承接的模型（优先产品钉死），并把思考档位
 * 夹回新模型的判定域。否则「覆盖=复制默认」会让钉死节点在勾选后继续报错，
 * 用户只能误以为必须切换 Agent 模式。
 */
export function executorBackendDraftForNode(
  capabilities: AgentCapabilitiesResponse,
  base: ExecutorBackendDraft,
  node: string,
): ExecutorBackendDraft {
  if (base.kind !== 'legacy') return { ...base }
  const provider = base.legacy_profile.provider
  const served = legacyModelIdServingNode(capabilities, provider, node)
  if (!served || served === base.legacy_profile.model) return { ...base }
  const clamped = clampLegacyThinking(capabilities, provider, served, base.legacy_profile)
  return {
    ...base,
    legacy_profile: { ...base.legacy_profile, ...clamped, model: served },
  }
}

function legacyDraftServesNode(
  capabilities: AgentCapabilitiesResponse,
  draft: LegacyLlmProfileDraft,
  node: string,
): boolean {
  const provider = capabilities.legacy_providers.find(
    (candidate) => candidate.id === draft.provider,
  )
  const model = provider?.models.find((candidate) => candidate.id === draft.model)
  // 还没选到具体模型时不抢跑：让物化按「请选择模型」报错，不预填任何选择。
  if (!model) return true
  return model.executor_nodes.includes(node)
}

/**
 * 默认执行者变化后的整份草稿：凡「继承默认」的节点若默认 legacy 模型承接
 * 不了（产品钉死），自动补成显式覆盖并预选可承接模型。继承在本就不可行，
 * 与其让保存报错把人推向 Agent 模式，不如把唯一合法配置直接摆上盘面
 * （列表里看得到、可改，取消勾选会回到明确的报错）。已有覆盖不碰。
 */
export function withExecutorDefault(
  capabilities: AgentCapabilitiesResponse,
  value: PipelineAgentProfilesDraft,
  executorDefault: ExecutorBackendDraft,
): PipelineAgentProfilesDraft {
  const next: PipelineAgentProfilesDraft = { ...value, executor_default: executorDefault }
  if (executorDefault.kind !== 'legacy') return next
  let overrides = next.executor_overrides
  for (const node of capabilities.executor_nodes) {
    if (overrides[node] !== undefined) continue
    if (legacyDraftServesNode(capabilities, executorDefault.legacy_profile, node)) continue
    overrides = {
      ...overrides,
      // 任何 legacy 模型都不承接（agent-only）的节点唯一合法配置是 Agent
      // 覆盖；预置可见可改的空 Agent 草稿（不替用户猜 driver/model），
      // 不塞一份注定失败的 legacy 副本。
      [node]: legacyServesNodeAnywhere(capabilities, node)
        ? executorBackendDraftForNode(capabilities, executorDefault, node)
        : {
          kind: 'agent',
          agent_profile: emptyAgentProfileDraft(),
          legacy_profile: { ...executorDefault.legacy_profile },
        },
    }
  }
  return { ...next, executor_overrides: overrides }
}

/**
 * 任何 provider 的任何 legacy 模型都不承接该节点 ⇒ agent-only：
 * Legacy 默认下它的唯一合法配置是 Agent 覆盖（与后端单向豁免同口径）。
 */
function legacyServesNodeAnywhere(
  capabilities: AgentCapabilitiesResponse,
  node: string,
): boolean {
  return capabilities.legacy_providers.some((provider) =>
    provider.models.some((model) => model.executor_nodes.includes(node)))
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
  if (!driver.roles.includes(role)) errors.push(`${label}：${driver.label} 不支持 ${role} 角色`)
  if (driver.availability !== 'ready') {
    errors.push(`${label}：${driver.label} 当前为 ${driver.availability}：${driver.availability_reason}`)
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
  if (!draft.reasoning_effort) errors.push(`${label}：请选择思考强度`)
  else if (!model.reasoning_efforts.includes(draft.reasoning_effort)) {
    errors.push(`${label}：思考强度 ${draft.reasoning_effort} 不属于当前模型`)
  }

  let executionMode: string | null = null
  if (model.execution_modes.length > 0) {
    if (!draft.execution_mode) errors.push(`${label}：请选择执行模式`)
    else if (!model.execution_modes.includes(draft.execution_mode)) {
      errors.push(`${label}：执行模式 ${draft.execution_mode} 不属于当前模型`)
    } else executionMode = draft.execution_mode
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

function materializeLegacyProfile(
  capabilities: AgentCapabilitiesResponse,
  draft: LegacyLlmProfileDraft,
  label: string,
  node?: string,
): { profile: LegacyLlmProfile | null; errors: string[] } {
  const errors: string[] = []
  if (!draft.provider) {
    errors.push(`${label}：请选择 API provider`)
    return { profile: null, errors }
  }
  const provider = legacyProviderForDraft(capabilities, draft)
  if (!provider) {
    errors.push(`${label}：provider ${draft.provider} 已不在当前 capability 中`)
    return { profile: null, errors }
  }
  if (!draft.model) {
    errors.push(`${label}：请选择模型`)
    return { profile: null, errors }
  }
  const model = provider.models.find((candidate) => candidate.id === draft.model) ?? null
  if (!model) {
    errors.push(`${label}：模型 ${draft.model} 不属于 ${provider.label}`)
    return { profile: null, errors }
  }
  if (node && !model.executor_nodes.includes(node)) {
    const pin = capabilities.legacy_model_pins[node]
    errors.push(
      !legacyServesNodeAnywhere(capabilities, node)
        ? `${label}：节点 ${node} 需要 Agent 执行者（Legacy 不支持该节点），请为该节点配置 Agent 覆盖`
        : pin
          ? `${label}：${model.label} 无法承接节点 ${node}（产品契约钉死 ${legacyModelLabel(capabilities, pin)}）；请为该节点启用覆盖并选择 ${legacyModelLabel(capabilities, pin)}`
          : `${label}：${model.label} 不支持节点 ${node}，请为该节点选择支持的模型`,
    )
  }
  if (!draft.thinking_mode) {
    errors.push(`${label}：请选择思考模式`)
    return { profile: null, errors }
  }
  const thinking = model.thinking_modes.find(
    (candidate) => candidate.id === draft.thinking_mode,
  ) ?? null
  if (!thinking) {
    errors.push(`${label}：思考模式 ${draft.thinking_mode} 不属于当前模型`)
    return { profile: null, errors }
  }
  if (!draft.reasoning_effort) errors.push(`${label}：请选择思考强度`)
  else if (!thinking.reasoning_efforts.includes(draft.reasoning_effort)) {
    errors.push(`${label}：思考强度 ${draft.reasoning_effort} 不属于当前思考模式`)
  }
  if (errors.length > 0) return { profile: null, errors }
  return {
    profile: {
      provider: provider.id,
      model: model.id,
      reasoning_effort: draft.reasoning_effort,
      thinking_mode: thinking.id,
    },
    errors: [],
  }
}

function materializeBackend(
  capabilities: AgentCapabilitiesResponse,
  draft: ExecutorBackendDraft,
  label: string,
  node?: string,
): { backend: ExecutorBackend | null; errors: string[] } {
  if (!draft.kind) return { backend: null, errors: [`${label}：请选择 Agent 或 Legacy API`] }
  if (draft.kind === 'agent') {
    const result = materializeProfile(
      capabilities, draft.agent_profile, 'executor', label,
    )
    return {
      backend: result.profile ? { kind: 'agent', profile: result.profile } : null,
      errors: result.errors,
    }
  }
  const result = materializeLegacyProfile(
    capabilities, draft.legacy_profile, label, node,
  )
  return {
    backend: result.profile ? { kind: 'legacy', profile: result.profile } : null,
    errors: result.errors,
  }
}

/** capability 关闭式校验；任何一格不完整、过期或不可用都不发 preview/start。 */
export function materializePipelineAgentProfiles(
  capabilities: AgentCapabilitiesResponse,
  draft: PipelineAgentProfilesDraft,
): MaterializedProfiles {
  const commander = materializeProfile(
    capabilities, draft.commander, 'commander', '指挥大脑',
  )
  const executorDefault = materializeBackend(
    capabilities, draft.executor_default, '默认执行者',
  )
  const errors = [...commander.errors, ...executorDefault.errors]
  const overrides: Record<string, ExecutorBackend> = {}
  const knownNodes = new Set(capabilities.executor_nodes)
  // 任何 legacy 模型都不承接的节点 = agent-only：默认 legacy 时这些节点的
  // agent 覆盖是唯一合法出路（与后端 AGENT_ONLY_EXECUTOR_NODES 单向豁免同口径，
  // 从 capability 派生，不另抄节点清单）。
  const legacyServedNodes = new Set(
    capabilities.legacy_providers.flatMap((provider) =>
      provider.models.flatMap((model) => model.executor_nodes),
    ),
  )
  if (draft.concurrency) {
    const policy = draft.concurrency
    const allowed = capabilities.concurrency
    for (const [node, count] of Object.entries(policy.node_overrides)) {
      if (!allowed.nodes.includes(node) || !allowed.node_choices.includes(count)) {
        errors.push(`并发覆盖 ${node}=${count} 不在当前 capability 中`)
      }
    }
    for (const count of [policy.per_book, policy.per_provider]) {
      if (!Number.isInteger(count) || count < allowed.total_min || count > allowed.total_max) {
        errors.push(`总并发必须是 ${allowed.total_min}～${allowed.total_max} 的整数`)
      }
    }
  }

  for (const node of Object.keys(draft.executor_overrides)) {
    if (!knownNodes.has(node)) errors.push(`节点覆盖：${node} 已不在当前 capability 中`)
  }
  for (const node of capabilities.executor_nodes) {
    const override = draft.executor_overrides[node]
    if (override) {
      const result = materializeBackend(capabilities, override, `节点 ${node}`, node)
      errors.push(...result.errors)
      if (result.backend) overrides[node] = result.backend
      if (
        executorDefault.backend !== null
        && result.backend !== null
        && result.backend.kind !== executorDefault.backend.kind
      ) {
        const agentOnlyOverride = executorDefault.backend.kind === 'legacy'
          && result.backend.kind === 'agent'
          && !legacyServedNodes.has(node)
        if (!agentOnlyOverride) {
          errors.push(
            `节点 ${node}：执行者必须与默认执行者同一模式；默认 Legacy 时仅 Legacy 不支持的节点可用 Agent 覆盖，不允许其余混用`,
          )
        }
      }
      continue
    }
    if (executorDefault.backend?.kind === 'legacy') {
      const inherited = materializeLegacyProfile(
        capabilities, draft.executor_default.legacy_profile, '默认执行者', node,
      )
      errors.push(...inherited.errors)
    }
  }

  const uniqueErrors = [...new Set(errors)]
  if (!commander.profile || !executorDefault.backend || uniqueErrors.length > 0) {
    return { profiles: null, errors: uniqueErrors }
  }
  return {
    profiles: {
      schema_version: 2,
      ...concurrencySnapshot(draft.concurrency),
      commander: commander.profile,
      executor_default: executorDefault.backend,
      executor_overrides: overrides,
    },
    errors: [],
  }
}

export function cloneAgentProfile(profile: AgentProfile): AgentProfile {
  return { ...profile }
}

export function cloneExecutorBackend(backend: ExecutorBackend): ExecutorBackend {
  return backend.kind === 'agent'
    ? { kind: 'agent', profile: cloneAgentProfile(backend.profile) }
    : { kind: 'legacy', profile: { ...backend.profile } }
}

/** 手写深拷贝避免 JSON 序列化把未来显式 null/字段语义压扁。 */
export function clonePipelineAgentProfiles(
  profiles: PipelineAgentProfiles,
): PipelineAgentProfiles {
  return {
    schema_version: 2,
    ...concurrencySnapshot(profiles.concurrency),
    commander: cloneAgentProfile(profiles.commander),
    executor_default: cloneExecutorBackend(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        cloneExecutorBackend(backend),
      ]),
    ),
  }
}

function concurrencySnapshot(policy: PipelineConcurrency | undefined): { concurrency?: PipelineConcurrency } {
  return policy ? { concurrency: { ...policy, node_overrides: { ...policy.node_overrides } } } : {}
}

function concurrencyEqual(left: PipelineConcurrency | undefined, right: PipelineConcurrency | undefined): boolean {
  if (!left || !right) return left === right
  if (left.per_book !== right.per_book || left.per_provider !== right.per_provider) return false
  const nodes = Object.keys(left.node_overrides).sort()
  const others = Object.keys(right.node_overrides).sort()
  return nodes.length === others.length && nodes.every((node, index) =>
    node === others[index] && left.node_overrides[node] === right.node_overrides[node])
}

function profileEqual(left: AgentProfile, right: AgentProfile): boolean {
  return left.driver === right.driver
    && left.model === right.model
    && left.reasoning_effort === right.reasoning_effort
    && left.execution_mode === right.execution_mode
}

function backendEqual(left: ExecutorBackend, right: ExecutorBackend): boolean {
  if (left.kind !== right.kind) return false
  if (left.kind === 'agent' && right.kind === 'agent') {
    return profileEqual(left.profile, right.profile)
  }
  if (left.kind === 'legacy' && right.kind === 'legacy') {
    return left.profile.provider === right.profile.provider
      && left.profile.model === right.profile.model
      && left.profile.reasoning_effort === right.profile.reasoning_effort
      && left.profile.thinking_mode === right.profile.thinking_mode
  }
  return false
}

/** preview 必须逐格回显同一份配置；键序不同不算漂移，增删/改值都算。 */
export function pipelineProfilesEqual(
  left: PipelineAgentProfiles | null,
  right: PipelineAgentProfiles | null,
): boolean {
  if (left === null || right === null) return left === right
  if (left.schema_version !== right.schema_version) return false
  if (!concurrencyEqual(left.concurrency, right.concurrency)) return false
  if (!profileEqual(left.commander, right.commander)) return false
  if (!backendEqual(left.executor_default, right.executor_default)) return false
  const leftNodes = Object.keys(left.executor_overrides).sort()
  const rightNodes = Object.keys(right.executor_overrides).sort()
  if (leftNodes.length !== rightNodes.length) return false
  return leftNodes.every((node, index) => {
    const other = rightNodes[index]
    return node === other
      && backendEqual(left.executor_overrides[node], right.executor_overrides[other])
  })
}
