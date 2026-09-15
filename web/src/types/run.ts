/**
 * GET /api/novels/{sid}/run 的响应形状。
 * snake_case 照后端原样（与 endpoints.ts 里 BackendNovelOverview 的做法同款：
 * 数据缝只做适配，不改名、不重算）。
 */

/**
 * 信标五态：这条命令跑没跑完、退出码是什么。
 * 它是**观察轨迹不是判据结论** —— ok 只念作「跑完（exit 0）」，
 * 合不合格永远看各层屏的 badge，这一屏不做判定。
 */
export type RunBeacon = 'pending' | 'running' | 'ok' | 'landed_red' | 'stopped' | 'failed'
export type RunResumeFrontierState = 'ready' | 'blocked' | 'pipeline_complete' | 'unknown'
/** Commander 裁决者退役（2026-09-15）：审批闸一律投影为 human，blocked 时只剩 executor/human。 */
export type RunNextHandler = 'code' | 'executor' | 'human' | 'none' | 'unknown'
export type RunResumeVerdict = 'run' | 'skip' | 'redo' | 'unknown' | 'legacy_incompatible'

export interface RunStep {
  seq: number
  node: string
  label: string
  /** 花不花钱由后端用 NODES 派生好下发，前端一行判定都不写 */
  paid: boolean
  beacon: RunBeacon
  /** 最近一次跑完的退出码；从没跑完过是 null */
  last_exit: number | null
  /** 最近一次信标的 unix 秒 */
  last_ts: number | null
  /** running 时 = 已跑秒数（后端算的 now - start）；其余为 null */
  elapsed: number | null
}

/** 信标事件：agent 每执行一条命令前后各打一点（start 不带 exit_code，end 必带）。 */
export interface RunEvent {
  ts: number
  sid: string
  /** 新信标恒有；旧 agent_progress JSONL 行经后端读取时为 null。 */
  run_id: string | null
  /** 同一 parent run 内的精确进程 attempt；旧行可空。 */
  execution_id: string | null
  node: string
  phase: 'start' | 'end'
  exit_code: number | null
  note: string | null
  /** 服务端从原始信标折叠的观察态；前端不得从 exit_code 另算。 */
  beacon: RunBeacon
}

/** 后端 operator 在同一快照上给出的续跑点；前端不从信标或 paid 字段反推。 */
export interface RunResumeFrontier {
  state: RunResumeFrontierState
  node: string | null
  seq: number | null
  label: string | null
  resume_verdict: RunResumeVerdict | null
  why: string
  waiting_on: string | null
  paid: boolean | null
  writes: string | null
  next_handler: RunNextHandler
  review_states?: Array<{ node: string; scope: string; state: string }> | null
  verified_skipped_nodes: string[]
  verified_skipped_count: number
}

export interface RunWorkUnit {
  unit_id: string
  node: string
  role: string
  scope: string
  content_used: number
  content_limit: number
  content_base_limit?: number
  content_granted?: number
  grant_count?: number
  content_remaining: number
  state: string
  latest_request_sha256: string | null
  latest_response_sha256: string | null
  recovery_action: string | null
  registered?: boolean
  processing?: {
    run_id: string
    input_sha256: string
    total: number
    pending: number
    generated: number
    mechanical: number
    history_adopted: number
    cached: number
    failed: number
    unknown: number
    reason: string | null
  } | null
  review?: {
    subject_sha256: string
    total: number
    pending: number
    decision_inherited: number
  } | null
  review_unavailable_reason?: string | null
}

export interface BudgetGrant {
  intent_id: string
  preview_token: string
  additional: number
  actor: string
  reason: string
  created_at: number
  before_ledger_sha256: string
  surface_sha256: string
  authorized_after_ordinal: number
  owner_run_ids: string[]
}

export interface WorkUnitGrantRequest {
  preview_token: string
  intent_id: string
  additional: number
  reason: string
}

export interface WorkUnitGrantPreview {
  sid: string
  unit_id: string
  available: boolean
  reason: string
  additional: number
  allowed_additions: number[]
  preview_token: string | null
  unit: RunWorkUnit | null
  costs: {
    calls: number
    failed_calls: number
    cached_calls: number
    prompt_tokens: number | null
    completion_tokens: number | null
    total_tokens: number | null
    missing_usage_calls: number
  } | null
  history: BudgetGrant[] | null
}

export interface WorkUnitGrantResult {
  grant: BudgetGrant
  current: WorkUnitGrantPreview
}

export interface RunProgress {
  sid: string
  /** 当前真正续跑点、已越过前缀与下一处理者，全部由后端 operator 判定。 */
  resume_frontier: RunResumeFrontier
  /** 与后端 NODES 逐项一致，按处理链顺序。 */
  steps: RunStep[]
  /** 来自产物盘的真实完成度；书目录还没建时全是 null（不 404，进度屏照样能开） */
  stage_done: Record<string, boolean | null>
  /** 最近约 20 条信标事件，人读时间线 */
  events_tail: RunEvent[]
  work_units?: RunWorkUnit[] | null
  work_units_unavailable_reason?: string | null
  current_recovery_actions?: string[] | null
  blueprint_progress?: import('./plot.generated').BlueprintRunProgress | null
}
