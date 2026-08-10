/**
 * 唯一的数据缝。
 *
 * src/api/mock.ts 与 src/api/endpoints.ts 里的真实实现都 `satisfies Api`，
 * 所以两条路径同型是 **tsc 强制**的，不是口头约定。
 * 组件与屏从不 fetch，只经 hooks → api.*。
 */
import type { ApprovalDoc, GateKey, SeenKey } from './approvals'
import type { EnumsResponse } from './enums'
import type { Job, JobLogResponse, JobPreview, JobRequest, JobsResponse } from './jobs'
import type { NovelOverview, NovelsResponse } from './novels'
import type {
  CheckKind,
  CheckReport,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0Extras,
  Stage1Chapters,
  Stage1HookCopy,
  Stage1Report,
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

  getStage0(sid: string): Promise<Stage0Extras>
  getStage1Report(sid: string): Promise<Stage1Report>
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
  /** 确认框里那条命令由服务端下发，前端不拼 */
  previewJob(req: JobRequest): Promise<JobPreview>
  startJob(req: JobRequest): Promise<Job>

  /* 人工验收记录 */
  getApprovals(sid: string): Promise<ApprovalDoc>
  approveGate(sid: string, key: GateKey): Promise<ApprovalDoc>
  markSeen(sid: string, key: SeenKey): Promise<ApprovalDoc>
  setDeep(sid: string, deep: number[]): Promise<ApprovalDoc>
  startWriting(sid: string): Promise<ApprovalDoc>
  resetBook(sid: string): Promise<ApprovalDoc>

  /* 写 */
  saveDraftEdit(sid: string, ordinal: number, text: string): Promise<{ ok: true }>
  approveHook(sid: string, index: number, text: string): Promise<{ ok: true }>
  approveNames(sid: string): Promise<{ ok: true }>
  uploadCorpus(file: { name: string; size: number }): Promise<{ sid: string }>
}
