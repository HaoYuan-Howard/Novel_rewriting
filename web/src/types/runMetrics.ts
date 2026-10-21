/** 后端统计投影；全部聚合、未知与运行归属由服务端确定。 */
export interface UsageSummary {
  observed_rows: number
  conflicting_rows: number
  wire_calls: number
  cached_calls: number
  unknown_cache_calls: number
  failed_calls: number
  unknown_outcome_calls: number
  missing_usage_calls: number
  missing_latency_calls: number
  known_prompt_tokens: number
  known_completion_tokens: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  reasoning_tokens: number | null
  known_model_time_ms: number
  model_time_ms: number | null
  known_active_time_ms: number
  active_time_ms: number | null
  missing_interval_calls: number
}

export interface AttemptMetrics {
  attempt_id: string
  turn: number
  run_id: string
  parent_run_id: string | null
  session_run_id: string | null
  state: string
  cached: boolean | null
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
  latency_ms: number | null
  first_token_ms: number | null
  finished_at: string | null
  issue: string | null
}

export interface UnitMetrics {
  unit_id: string
  node: string
  role: string
  scope: string
  content_turns: number
  network_retries: number
  mechanical: number
  cached_processing: number
  usage: UsageSummary
  attempts: AttemptMetrics[]
  issues: string[]
}

export interface RunMetrics {
  sid: string
  run_id: string | null
  runs: Array<{
    run_id: string
    kind: string
    state: string
    started_at: number | null
    ended_at: number | null
    elapsed_seconds: number | null
  }>
  usage: UsageSummary
  units: UnitMetrics[]
  unassigned_rows: number
  unassigned_usage: UsageSummary
  issues: string[]
}
