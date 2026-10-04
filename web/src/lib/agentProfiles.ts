import type {
  AgentCapabilitiesResponse,
  ExecutorBackend,
  LegacyLlmProfile,
  LegacyProviderCapability,
  PipelineAgentProfiles,
  PipelineConcurrency,
} from '@/types/agent'

/** 浏览器草稿允许半成品；只有 materialize 成功后的对象才准进入 preview/start。 */
export interface LegacyLlmProfileDraft {
  provider: string
  model: string
  reasoning_effort: string
  thinking_mode: string
}

/** 单一 LLM executor；kind 固定为 legacy（与 C3 wire 对齐），草稿只编辑 profile。 */
export interface ExecutorBackendDraft {
  kind: 'legacy'
  profile: LegacyLlmProfileDraft
}

export interface PipelineAgentProfilesDraft {
  executor_default: ExecutorBackendDraft
  /** 键存在表示启用覆盖；没有键表示继承 executor_default。 */
  executor_overrides: Record<string, ExecutorBackendDraft>
  concurrency?: PipelineConcurrency
}

export interface MaterializedProfiles {
  profiles: PipelineAgentProfiles | null
  errors: string[]
}

export function emptyLegacyLlmProfileDraft(): LegacyLlmProfileDraft {
  return { provider: '', model: '', reasoning_effort: '', thinking_mode: '' }
}

export function emptyExecutorBackendDraft(): ExecutorBackendDraft {
  return {
    kind: 'legacy',
    profile: emptyLegacyLlmProfileDraft(),
  }
}

export function emptyPipelineAgentProfilesDraft(): PipelineAgentProfilesDraft {
  return {
    executor_default: emptyExecutorBackendDraft(),
    executor_overrides: {},
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
  return {
    kind: 'legacy',
    profile: legacyProfileDraftOf(backend.profile),
  }
}

/** null 明确生成全空草稿，绝不拿 capability 数组第一项伪造默认。 */
export function pipelineDraftOf(
  profiles: PipelineAgentProfiles | null,
): PipelineAgentProfilesDraft {
  if (profiles === null) return emptyPipelineAgentProfilesDraft()
  return {
    ...concurrencySnapshot(profiles.concurrency),
    executor_default: executorBackendDraftOf(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        executorBackendDraftOf(backend),
      ]),
    ),
  }
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
 * 同一 provider 内真正承接该节点的模型：优先产品钉死模型
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
 * 勾选节点覆盖时的初始草稿：默认执行者的副本，但当默认模型承接不了
 * 该节点时，换成同 provider 内可承接的模型（优先产品钉死），并把思考档位
 * 夹回新模型的判定域。
 */
export function executorBackendDraftForNode(
  capabilities: AgentCapabilitiesResponse,
  base: ExecutorBackendDraft,
  node: string,
): ExecutorBackendDraft {
  if (legacyDraftServesNode(capabilities, base.profile, node)) {
    return { ...base, profile: { ...base.profile } }
  }
  const provider = base.profile.provider
  const served = legacyModelIdServingNode(capabilities, provider, node)
  if (!served) return { ...base, profile: { ...base.profile } }
  const clamped = clampLegacyThinking(capabilities, provider, served, base.profile)
  return {
    kind: 'legacy',
    profile: { ...base.profile, ...clamped, model: served },
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
 * 默认执行者变化后的整份草稿：凡「继承默认」的节点若默认模型承接
 * 不了（产品钉死），自动补成显式覆盖并预选可承接模型。已有覆盖不碰。
 */
export function withExecutorDefault(
  capabilities: AgentCapabilitiesResponse,
  value: PipelineAgentProfilesDraft,
  executorDefault: ExecutorBackendDraft,
): PipelineAgentProfilesDraft {
  const next: PipelineAgentProfilesDraft = { ...value, executor_default: executorDefault }
  let overrides = next.executor_overrides
  for (const node of capabilities.executor_nodes) {
    if (overrides[node] !== undefined) continue
    if (legacyDraftServesNode(capabilities, executorDefault.profile, node)) continue
    overrides = {
      ...overrides,
      [node]: executorBackendDraftForNode(capabilities, executorDefault, node),
    }
  }
  return { ...next, executor_overrides: overrides }
}

function materializeLegacyProfile(
  capabilities: AgentCapabilitiesResponse,
  draft: LegacyLlmProfileDraft,
  label: string,
  node?: string,
): { profile: LegacyLlmProfile | null; errors: string[] } {
  const errors: string[] = []
  if (!draft.provider) {
    errors.push(`${label}：请选择 Provider`)
    return { profile: null, errors }
  }
  const provider = legacyProviderForDraft(capabilities, draft)
  if (!provider) {
    errors.push(`${label}：Provider ${draft.provider} 已不在当前 capability 中`)
    return { profile: null, errors }
  }
  if (!draft.model) {
    errors.push(`${label}：请选择 Model`)
    return { profile: null, errors }
  }
  const model = provider.models.find((candidate) => candidate.id === draft.model) ?? null
  if (!model) {
    errors.push(`${label}：Model ${draft.model} 不属于 ${provider.label}`)
    return { profile: null, errors }
  }
  if (node && !model.executor_nodes.includes(node)) {
    const pin = capabilities.legacy_model_pins[node]
    errors.push(
      pin
        ? `${label}：${model.label} 无法承接节点 ${node}（产品契约钉死 ${legacyModelLabel(capabilities, pin)}）；请为该节点启用覆盖并选择 ${legacyModelLabel(capabilities, pin)}`
        : `${label}：${model.label} 不支持节点 ${node}，请为该节点选择支持的模型`,
    )
  }
  if (!draft.thinking_mode) {
    errors.push(`${label}：请选择 Thinking`)
    return { profile: null, errors }
  }
  const thinking = model.thinking_modes.find(
    (candidate) => candidate.id === draft.thinking_mode,
  ) ?? null
  if (!thinking) {
    errors.push(`${label}：Thinking ${draft.thinking_mode} 不属于当前模型`)
    return { profile: null, errors }
  }
  if (!draft.reasoning_effort) errors.push(`${label}：请选择 Effort`)
  else if (!thinking.reasoning_efforts.includes(draft.reasoning_effort)) {
    errors.push(`${label}：Effort ${draft.reasoning_effort} 不属于当前 Thinking`)
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
  const result = materializeLegacyProfile(
    capabilities, draft.profile, label, node,
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
  const executorDefault = materializeBackend(
    capabilities, draft.executor_default, '默认模型执行配置',
  )
  const errors = [...executorDefault.errors]
  const overrides: Record<string, ExecutorBackend> = {}
  const knownNodes = new Set(capabilities.executor_nodes)
  if (draft.concurrency) {
    const policy = draft.concurrency
    const allowed = capabilities.concurrency
    for (const [node, count] of Object.entries(policy.node_overrides)) {
      if (!allowed.nodes.includes(node)
          || !Number.isInteger(count)
          || count < allowed.total_min
          || count > allowed.total_max) {
        errors.push(
          `节点并发 ${node}=${count} 必须是 ${allowed.total_min}～${allowed.total_max} 的整数，且节点在当前 capability 中`,
        )
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
      const result = materializeBackend(
        capabilities, override, `节点 ${node} 模型执行配置`, node,
      )
      errors.push(...result.errors)
      if (result.backend) overrides[node] = result.backend
      continue
    }
    if (executorDefault.backend) {
      const inherited = materializeLegacyProfile(
        capabilities, draft.executor_default.profile, '默认模型执行配置', node,
      )
      errors.push(...inherited.errors)
    }
  }

  const uniqueErrors = [...new Set(errors)]
  if (!executorDefault.backend || uniqueErrors.length > 0) {
    return { profiles: null, errors: uniqueErrors }
  }
  return {
    profiles: {
      schema_version: 4,
      ...concurrencySnapshot(draft.concurrency),
      executor_default: executorDefault.backend,
      executor_overrides: overrides,
    },
    errors: [],
  }
}

export function cloneExecutorBackend(backend: ExecutorBackend): ExecutorBackend {
  return { kind: 'legacy', profile: { ...backend.profile } }
}

/** 手写深拷贝避免 JSON 序列化把未来显式 null/字段语义压扁。 */
export function clonePipelineAgentProfiles(
  profiles: PipelineAgentProfiles,
): PipelineAgentProfiles {
  return {
    schema_version: 4,
    ...concurrencySnapshot(profiles.concurrency),
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

function backendEqual(left: ExecutorBackend, right: ExecutorBackend): boolean {
  return left.profile.provider === right.profile.provider
    && left.profile.model === right.profile.model
    && left.profile.reasoning_effort === right.profile.reasoning_effort
    && left.profile.thinking_mode === right.profile.thinking_mode
}

/** preview 必须逐格回显同一份配置；键序不同不算漂移，增删/改值都算。 */
export function pipelineProfilesEqual(
  left: PipelineAgentProfiles | null,
  right: PipelineAgentProfiles | null,
): boolean {
  if (left === null || right === null) return left === right
  if (left.schema_version !== right.schema_version) return false
  if (!concurrencyEqual(left.concurrency, right.concurrency)) return false
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
