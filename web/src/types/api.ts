/**
 * 唯一的数据缝。
 *
 * src/api/mock.ts 与 src/api/endpoints.ts 里的真实实现都 `satisfies Api`，
 * 所以两条路径同型是 **tsc 强制**的，不是口头约定。
 * 组件与屏从不 fetch，只经 hooks → api.*。
 */
import type { ApprovalDoc, GateKey, SeenKey } from './approvals'
import type {
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunRecord,
  AgentRunRequest,
  AgentRunsResponse,
} from './agent'
import type { JobLogStreamSink } from './jobs'
import type { EnumsResponse } from './enums'
import type {
  Job,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
  UploadResponse,
} from './jobs'
import type { NovelOverview, NovelsResponse } from './novels'
import type { RunProgress } from './run'
import type { Mode } from '@/constants/vocabulary'
import type {
  CheckKind,
  CheckReport,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0EditRequest,
  Stage0Report,
  Stage0Segments,
  Stage1Chapters,
  Stage1HookCopy,
  Stage1Segments,
  Stage2Global,
  Stage3Style,
  Stage4Response,
  Stage5Response,
  Stage6Response,
  Stage7Response,
} from './stages'

export interface Api {
  /* 读 */
  getNovels(): Promise<NovelsResponse>
  getNovel(sid: string): Promise<NovelOverview>
  getEnums(): Promise<EnumsResponse>
  /** 跑书进度：14 步信标 + 各层产物完成度。beacon=ok 只念作「跑完」，判定归各层屏的 badge。 */
  getRunProgress(sid: string): Promise<RunProgress>

  getStage0Report(sid: string): Promise<Stage0Report>
  getStage0Segments(sid: string): Promise<Stage0Segments>
  editStage0(sid: string, request: Stage0EditRequest): Promise<Job>
  approveStage0(sid: string): Promise<Job>
  partitionStage1(sid: string): Promise<Job>
  getStage1Segments(sid: string): Promise<Stage1Segments>
  getStage1Chapters(sid: string): Promise<Stage1Chapters>
  getStage1HookCopy(sid: string): Promise<Stage1HookCopy>
  getReading(sid: string, ordinal: number): Promise<ReadingResponse>
  getGlobal(sid: string): Promise<Stage2Global>
  getStyle(sid: string): Promise<Stage3Style>
  getStage4(sid: string, ordinal: number): Promise<Stage4Response>
  getStage5(sid: string, ordinal: number): Promise<Stage5Response>
  getReview(sid: string, ordinal: number): Promise<ReviewResponse>
  getInbox(sid: string): Promise<InboxResponse>
  getStage6(sid: string): Promise<Stage6Response>
  getStage7(sid: string): Promise<Stage7Response>
  getStage7Chapter(sid: string, ordinal: number): Promise<Stage7Response>
  /** null = 服务端说这一层还没有报告（≠ 通过）。抛 nosample = 这一份没抓在样本里。 */
  getCheck(sid: string, kind: CheckKind): Promise<CheckReport | null>

  /**
   * 样本覆盖了哪一本、哪几章 —— **只有 mock 会给**，真实模式返回 null。
   * 有了它，「第 3 章才有数据」这条知识就留在数据层，
   * 渲染层不会出现 `n === 3` 这种把样本边界写死进界面的代码。
   */
  getSampleCoverage(): Promise<{ sid: string; ordinals: number[] } | null>

  /* 作业 */
  getJobs(sid: string): Promise<JobsResponse>
  getJobLog(id: string, offset: number): Promise<JobLogResponse>
  /** SSE：返回关闭函数；event.next_byte 是 UTF-8 字节 offset。 */
  streamJobLog(id: string, offset: number, sink: JobLogStreamSink): () => void
  /** 确认框里那条命令由服务端下发，前端不拼 */
  previewJob(req: JobRequest): Promise<JobPreview>
  startJob(req: JobRequest): Promise<Job>

  /* agent 代跑（互斥归服务端：冲突 409 / ErrorBody.kind = JobConflict） */
  /** 0 成本预览：与真跑同一构造函数。两项授权都进 prompt，任一勾选变化都重新 preview。 */
  previewAgentRun(req: AgentRunRequest): Promise<AgentRunPreview>
  startAgentRun(req: AgentRunRequest): Promise<AgentRunRecord>
  /** 新的在前 */
  getAgentRuns(sid: string): Promise<AgentRunsResponse>
  /** 与 jobs 日志同一形状 {text, next_byte} */
  getAgentRunLog(runId: string, offset: number): Promise<AgentRunLogChunk>
  /** 熄火：没在跑 409 */
  stopAgentRun(runId: string): Promise<AgentRunRecord>

  /* 人工验收记录 */
  getApprovals(sid: string): Promise<ApprovalDoc>
  approveGate(sid: string, key: GateKey): Promise<ApprovalDoc>
  markSeen(sid: string, key: SeenKey): Promise<ApprovalDoc>
  setDeep(sid: string, deep: number[]): Promise<ApprovalDoc>
  startWriting(sid: string): Promise<ApprovalDoc>
  resetBook(sid: string): Promise<ApprovalDoc>

  /* 写（三条人工写回走专用路由，返回真实作业记录——真实模式不写 localStorage 充成功） */
  /** 章节改稿：与生成稿同一判定落点（stage5.write --apply-edit，origin=human 记账） */
  saveDraftEdit(sid: string, ordinal: number, req: { mode: Mode; text: string }): Promise<Job>
  /** 文案定稿：只收序号（--pick，1 起）。改字走 CLI（stage6.hook --apply-edit），不在浏览器。 */
  approveHook(sid: string, pick: number): Promise<Job>
  /** 人名表定稿（无参终态） */
  approveNames(sid: string): Promise<Job>
  uploadCorpus(file: File): Promise<UploadResponse>
}
