/**
 * agent 代跑（POST /api/agent/runs 一族）的形状。snake_case 照后端原样，
 * 与 run.ts / jobs.ts 的 Backend* 同款做法：数据缝只做适配，不改名。
 */
/**
 * Agent 组合由后端 capability registry 发布。driver/model/effort/mode 都保留为
 * 字符串，前端不能用联合类型复制一份供应商能力表。
 */
export interface AgentProfile {
  driver: string
  model: string
  reasoning_effort: string
  execution_mode: string | null
}

export interface PipelineAgentProfiles {
  commander: AgentProfile
  executor_default: AgentProfile
  /** 键同样由 capability 响应的 executor_nodes 给出，前端不保存节点副本。 */
  executor_overrides: Record<string, AgentProfile>
}

export type AgentRole = 'commander' | 'executor'

export type AgentAvailability = 'ready' | 'missing' | 'unknown' | 'unauthenticated'

export interface AgentModelCapability {
  /** 进入 argv 与审计账的精确模型 id；与展示文案分开。 */
  id: string
  label: string
  reasoning_efforts: string[]
  execution_modes: string[]
}

export interface AgentDriverCapability {
  id: string
  /** 供应商元数据，不等于可执行 driver id。 */
  provider: string
  label: string
  roles: AgentRole[]
  availability: AgentAvailability
  availability_reason: string
  version: string | null
  models: AgentModelCapability[]
}

export interface AgentCapabilitiesResponse {
  drivers: AgentDriverCapability[]
  executor_nodes: string[]
  /** null = 服务端尚无 provider-neutral 默认，调用方不得拿 drivers[0] 猜。 */
  default_profiles: PipelineAgentProfiles | null
}

export type AgentProfileSettingsState = 'unset' | 'configured' | 'unreadable'

/** 当前 output base 的用户常用配置；与 capability 的产品默认分开。 */
export interface AgentProfileSettingsView {
  state: AgentProfileSettingsState
  profiles: PipelineAgentProfiles | null
  /** 设置文件当前字节身份；保存时用于防多标签页静默覆盖。 */
  revision: string
  /** unix 秒；从未保存或文件不可读时为 null。 */
  updated_at: number | null
  issue: string | null
}

export interface AgentProfileSettingsUpdate {
  /** null = 显式清空用户常用配置，不影响已启动/排队的 run。 */
  profiles: PipelineAgentProfiles | null
  expected_revision: string
}

/** 发起参数。两项授权彼此独立，都会原样进入 prompt 与审计记录。 */
export interface AgentRunRequest {
  sid: string
  /** 只控制是否允许产生外部调用费用。 */
  authorized: boolean
  /** 只控制是否允许向外部模型发送正文及派生上下文。 */
  egress_authorized: boolean
  /** 本次预览与启动共用的完整快照；前端新入口绝不省略或发送 null。 */
  profiles: PipelineAgentProfiles
}

/** preview 的返回：与真跑同一构造函数，0 成本。前端不拼 argv / prompt。 */
export interface AgentRunPreview {
  argv: string[]
  prompt: string
  authorized: boolean
  egress_authorized: boolean
  /** 服务端解析后的双角色配置；旧路由尚未接线或旧记录为 null。 */
  profiles: PipelineAgentProfiles | null
}

export interface AgentRunStart {
  sid: string
  /** 规范化绝对 base；旧事件可能没有。 */
  base?: string | null
  argv: string[]
  prompt: string
  authorized: boolean
  egress_authorized: boolean
  /** 启动时仓库代码指纹；历史记录可能没有。 */
  code_version: string | null
  /** 启动时 operator skill 整树身份；历史记录可能没有。 */
  skill_sha256?: string | null
  /** 历史缺失只能表示 unknown，不能按今天的默认倒推。 */
  profiles: PipelineAgentProfiles | null
}

export interface RunContractV2 {
  wire_version: 2
  sid: string
  base: string
  authorized: boolean
  egress_authorized: boolean
  profiles: PipelineAgentProfiles
}

export interface ExecutionEpoch {
  epoch_id: string
  ordinal: number
  reason: string
  argv: string[]
  prompt_sha256: string
  protocol_version: string
  cli_version: string | null
  code_version: string
  skill_sha256: string
  session_run_id: string
  resume_provider_session_id: string | null
}

export type AgentRunState =
  | 'pending'
  | 'running'
  | 'paused'
  | 'ok'
  | 'landed_red'
  | 'failed'
  | 'unknown'

export type AgentRunControl = 'pause' | 'resume' | 'stop'

export type AgentDeliveryState =
  | 'ready'
  | 'ready_with_disclosures'
  | 'stopped'
  | 'failed'
  | 'unknown'

export interface AgentDeliveryOutcome {
  state: AgentDeliveryState
  reason: string
  /** unix 秒 */
  checked_at: number
}

/**
 * 一次 agent 代跑的记录。
 * state 是整书 Supervisor 生命周期，不复用单节点 JobState。
 */
export interface AgentRunRecord {
  run_id: string
  start: AgentRunStart
  /** v1 历史为 null；v2 的稳定 parent contract。 */
  contract: RunContractV2 | null
  epochs: ExecutionEpoch[]
  active_epoch_id: string | null
  /** unix 秒 */
  started: number
  ended: number | null
  exit_code: number | null
  state: AgentRunState
  note: string | null
  /** 与进程 state 正交；null 只会出现在仍运行/被 reconcile 的记录。 */
  outcome: AgentDeliveryOutcome | null
  /** UI 只显示服务端明确开放的动作，不按 state 猜。 */
  allowed_controls: AgentRunControl[]
}

/** GET /api/agent/runs?sid= 的返回。新的在前。 */
export interface AgentRunsResponse {
  runs: AgentRunRecord[]
}

/** 与 jobs 日志同一形状：按需续取，下次从 next_byte 接着要。 */
export interface AgentRunLogChunk {
  text: string
  next_byte: number
}

/**
 * 批量代跑的请求：一次交 N 本。两项授权对**整批**生效。
 * 逐本勾会让「这一批到底授权了什么」没有一个能写进终审包的答案。
 */
export interface AgentBatchRequest {
  sids: string[]
  authorized: boolean
  egress_authorized: boolean
  /** 整批共享并冻结的完整快照；排队中的书也不能随随后界面选择漂移。 */
  profiles: PipelineAgentProfiles
}

/** 排在队里、还没有进程的一本。它不是 AgentRunRecord 的一种状态：盘上没有它。 */
export interface AgentPending {
  sid: string
  /** 排在第几，1 起算。 */
  position: number
}

/** 批量受理时没被收下的一本，连同原因。部分失败不回滚，所以要逐本说明。 */
export interface AgentRejected {
  sid: string
  reason: string
}

/**
 * GET /api/agent/runs/queue 与 POST /api/agent/runs/batch 的返回。
 * max_concurrent 由服务端下发 —— 前端不写死这个数（同「贵贱只有一处算」的纪律）。
 */
export interface AgentQueueView {
  running: AgentRunRecord[]
  pending: AgentPending[]
  rejected: AgentRejected[]
  max_concurrent: number
}
