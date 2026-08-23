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
export type RunBeacon = 'pending' | 'running' | 'ok' | 'landed_red' | 'failed'

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
  node: string
  phase: 'start' | 'end'
  exit_code: number | null
  note: string | null
}

export interface RunProgress {
  sid: string
  /** 与后端 NODES 逐项一致，按处理链顺序。 */
  steps: RunStep[]
  /** 来自产物盘的真实完成度；书目录还没建时全是 null（不 404，进度屏照样能开） */
  stage_done: Record<string, boolean | null>
  /** 最近约 20 条信标事件，人读时间线 */
  events_tail: RunEvent[]
}
