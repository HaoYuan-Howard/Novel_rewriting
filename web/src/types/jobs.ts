import type { JobState } from '@/constants/vocabulary'
import type { ExecutorBackend } from '@/types/agent'

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
  /** 直接节点作业启动时冻结的执行者；旧记录或非 executor 节点为 null/缺失。 */
  executor_backend?: ExecutorBackend | null
  /** 与 backend 正交的正文外发授权；旧记录/非 executor 节点为 null/缺失。 */
  egress_authorized?: boolean | null
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
  /** 本步已解析的 executor；旧记录、未指定或未迁移路径为 null，不按当前默认回填。 */
  executor_backend: ExecutorBackend | null
  /** 独立正文外发授权；旧/无 backend Step 为 null，新 executor Step 冻结 true/false 并与 argv 闭合。 */
  egress_authorized: boolean | null
}

export interface BackendJobStart {
  sid: string | null
  node: string
  flags: string[]
  steps: BackendJobStep[]
  python: string
  code: string
  paid: boolean
  writes: boolean
  /** 与 steps 中同源的直接节点 executor 快照；不能由当前默认倒推历史。 */
  executor_backend: ExecutorBackend | null
  /** 与付费 executor Step 同源；false 是可预览但不可启动，旧/无 backend 记录为 null。 */
  egress_authorized: boolean | null
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
  lines: JobLogLine[]
  /** 下次从这里续取 */
  offset: number
  done: boolean
}

export interface JobLogLine {
  text: string
  /** Present for @observe rows so diagnostics never disappear behind a friendly summary. */
  raw: string | null
  details: Record<string, unknown> | null
  kind: 'plain' | 'observation' | 'malformed'
}

/** POST /api/jobs/preview 的返回：确认框里原样摆出的那条命令 */
export interface JobPreview {
  argv: string
  paid: boolean
  label: string
  kind: string
  executor_backend?: ExecutorBackend | null
  egress_authorized?: boolean | null
  steps: Array<{
    label: string
    paid: boolean
    writes: string
    argv: string
    executor_backend?: ExecutorBackend | null
    egress_authorized?: boolean | null
  }>
}

/**
 * A human-supplied value required by one concrete server action.
 * The list is derived by the backend from NodeSpec; the browser only renders
 * the fields and sends their values back.
 */
export interface RequiredActionInput {
  flag: string
  description: string
}

/**
 * 发起一个作业需要的参数。
 * 注意这里**没有** `paid` —— 花不花钱由服务端在 preview 里回答，
 * 调用方不许自带一个 paid 过来（那就等于前端自己判定了）。
 */
export interface JobRequest {
  kind: string
  label: string
  /**
   * 这个动作的**身份**：服务端动作表里那一条的 extra，一个字都不能多。
   * 加了运行期参数就查不到这一条了，paid 会掉回 kind 级的默认值 ——
   * 于是一条免费的 --recheck 会被画成花钱的。运行期参数走 args。
   */
  extra: string[] | null
  /** 运行期参数：--chapters 3 这类。只进命令行，不参与查表。 */
  args?: string[]
  /**
   * 由 /api/enums 随具体动作下发的人工必填项。它只驱动确认框收值；
   * preview 不带这些值以取得待填模板，真正 start 才把实值并入 args。
   */
  required_inputs?: RequiredActionInput[]
  sid: string
  ordinal?: number | null
}

export interface BackendJobRequest {
  sid: string | null
  node: string
  flags: string[]
  /**
   * 可选直接节点 executor 判别联合。Agent variant 当前放行 8 节点 / 13 种
   * flag-token 组合；Legacy variant 由 provider/model/node capability 与服务端 run
   * registry 共同关闭式校验。自动 check-only Step 不继承 backend。本类型不硬编码
   * capability，选择面由设置页与 Agent 跑书表单动态构造。
   */
  executor_backend?: ExecutorBackend | null
  /** 独立正文外发授权；默认 false。false 可 preview，POST start 会在 driver 前拒绝；不能由付费确认或 backend 推导。 */
  egress_authorized?: boolean
}
