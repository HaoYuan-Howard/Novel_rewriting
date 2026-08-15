/**
 * agent 代跑（POST /api/agent/runs 一族）的形状。snake_case 照后端原样，
 * 与 run.ts / jobs.ts 的 Backend* 同款做法：数据缝只做适配，不改名。
 */
import type { JobState } from '@/constants/vocabulary'

/** 发起参数。两项授权彼此独立，都会原样进入 prompt 与审计记录。 */
export interface AgentRunRequest {
  sid: string
  /** 只控制是否允许产生外部调用费用。 */
  authorized: boolean
  /** 只控制是否允许向外部模型发送正文及派生上下文。 */
  egress_authorized: boolean
}

/** preview 的返回：与真跑同一构造函数，0 成本。前端不拼 argv / prompt。 */
export interface AgentRunPreview {
  argv: string[]
  prompt: string
  authorized: boolean
  egress_authorized: boolean
}

export interface AgentRunStart {
  sid: string
  argv: string[]
  prompt: string
  authorized: boolean
  /** null 只会出现在该字段落地前的历史事件，不能视为已授权。 */
  egress_authorized: boolean | null
}

/**
 * 一次 agent 代跑的记录。
 * state 复用作业四态：服务重启后原本 running 的一律改判 unknown —— 不是 ok。
 */
export interface AgentRunRecord {
  run_id: string
  start: AgentRunStart
  /** unix 秒 */
  started: number
  ended: number | null
  exit_code: number | null
  state: JobState
  note: string | null
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
