import type { JobState } from '@/constants/vocabulary'

/**
 * 作业。jobs.json 是空数组，样本里没有元素形状 ——
 * 这份定义来自 04_delivery.md 的 JobState 契约 + enums.job_kinds，
 * 已在 DELIVERY.md 里标为「需要后端确认的形状」。
 */
export interface Job {
  id: string
  kind: string
  label: string
  /** 服务端下发的完整命令行。前端不拼命令行。 */
  argv: string
  state: JobState
  paid: boolean
  /** unix 秒 */
  started: number
  finished: number | null
  sid: string | null
  ordinal: number | null
}

export interface JobsResponse {
  jobs: Job[]
  /** 一本书同时只跑一个作业 —— 这个判断归服务端，前端照画。 */
  running: Job | null
}

/** 后端 jobs.jsonl 折叠出来的真实响应；endpoints.ts 在数据缝处适配为上面的视图。 */
export interface BackendJobStep {
  argv: string[]
  cwd: string
  label: string
  paid: boolean
  writes: string
  note: string | null
}

export interface BackendJobStart {
  sid: string | null
  node: string
  mode: string | null
  flags: string[]
  steps: BackendJobStep[]
  python: string
  code: string
  paid: boolean
  writes: boolean
}

export interface BackendJobRecord {
  job_id: string
  start: BackendJobStart
  started: number
  ended: number | null
  exit_code: number | null
  state: JobState
  steps_started: number
  note: string | null
}

export interface BackendJobList {
  jobs: BackendJobRecord[]
}

export interface BackendLogChunk {
  text: string
  next_byte: number
}

export interface BackendLogStreamEvent {
  text: string
  next_byte: number
  done: boolean
  state: JobState | null
}

export interface JobLogStreamSink {
  onEvent(event: BackendLogStreamEvent): void
  onError(error: unknown): void
}

export interface UploadResponse {
  sid: string
  job: Job
}

export interface JobLogResponse {
  lines: string[]
  /** 下次从这里续取 */
  offset: number
  done: boolean
}

/** POST /api/jobs/preview 的返回：确认框里原样摆出的那条命令 */
export interface JobPreview {
  argv: string
  paid: boolean
  label: string
  kind: string
}

/**
 * 发起一个作业需要的参数。
 * 注意这里**没有** `paid` —— 花不花钱由服务端在 preview 里回答，
 * 调用方不许自带一个 paid 过来（那就等于前端自己判定了）。
 */
export interface JobRequest {
  kind: string
  label: string
  mode?: string | null
  /**
   * 这个动作的**身份**：服务端动作表里那一条的 extra，一个字都不能多。
   * 加了运行期参数就查不到这一条了，paid 会掉回 kind 级的默认值 ——
   * 于是一条免费的 --recheck 会被画成花钱的。运行期参数走 args。
   */
  extra: string[] | null
  /** 运行期参数：--chapters 3 / --deep 1,2,3 这类。只进命令行，不参与查表。 */
  args?: string[]
  sid: string
  ordinal?: number | null
}

export interface BackendJobRequest {
  sid: string | null
  node: string
  mode: string | null
  flags: string[]
}
