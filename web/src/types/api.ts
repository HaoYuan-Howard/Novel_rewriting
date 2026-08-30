/**
 * 唯一的数据缝。
 *
 * src/api/mock.ts 与 src/api/endpoints.ts 里的真实实现都 `satisfies Api`，
 * 所以两条路径同型是 **tsc 强制**的，不是口头约定。
 * 组件与屏从不 fetch，只经 hooks → api.*。
 */
import type {
  ApprovalBackfillResult,
  ApprovalDoc,
  ApprovalWorkpack,
  BatchTrimResolutionRequest,
  DesignReviewRequest,
  DesignReviewTemplate,
  DesignReviewTemplateQuery,
  GateKey,
  SeenKey,
  StructureApprovalRequest,
  Stage5ClosureRequest,
  Stage5ClosureTemplate,
  Stage6ApprovalWorkpack,
  Stage7ApprovalWorkpack,
} from './approvals'
import type {
  AgentCapabilitiesResponse,
  AgentBatchRequest,
  AgentQueueView,
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
import type { TrashNovelResult } from './novels'
import type { RunProgress } from './run'
import type {
  CheckKind,
  CheckReport,
  DraftEditPreview,
  DraftEditPreviewRequest,
  DraftEditRequest,
  InboxResponse,
  GlobalReviewDecisionSet,
  GlobalReviewResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0EditRequest,
  Stage0EditResult,
  Stage0ApprovalRequest,
  Stage0CandidateDecisionRequest,
  Stage0CandidateView,
  Stage0Report,
  Stage0Segments,
  Stage1View,
  Stage2Global,
  Stage4Response,
  Stage5Response,
  Stage6Response,
  Stage7Response,
  TrimResolutionRequest,
} from './stages'

export interface Api {
  /* 读 */
  getNovels(): Promise<NovelsResponse>
  getNovel(sid: string): Promise<NovelOverview>
  getEnums(): Promise<EnumsResponse>
  /** 跑书进度：NODES 信标 + 各层产物完成度。beacon=ok 只念作「跑完」，判定归各层屏的 badge。 */
  getRunProgress(sid: string): Promise<RunProgress>

  getStage0Report(sid: string): Promise<Stage0Report>
  getStage0Segments(sid: string): Promise<Stage0Segments>
  editStage0(sid: string, request: Stage0EditRequest): Promise<Stage0EditResult>
  approveStage0(sid: string, request: Stage0ApprovalRequest): Promise<Job>
  getStage0Candidate(sid: string): Promise<Stage0CandidateView | null>
  adoptStage0Candidate(sid: string, request: Stage0CandidateDecisionRequest): Promise<Job>
  dismissStage0Candidate(sid: string, request: Stage0CandidateDecisionRequest): Promise<Job>
  partitionStage1(sid: string): Promise<Job>
  getStage1(sid: string): Promise<Stage1View>
  getReading(sid: string, ordinal: number): Promise<ReadingResponse>
  getGlobal(sid: string, revealSpoilers?: boolean): Promise<Stage2Global>
  getGlobalReview(sid: string): Promise<GlobalReviewResponse>
  decideGlobalReview(sid: string, request: GlobalReviewDecisionSet): Promise<Job>
  getStage4(sid: string, ordinal: number): Promise<Stage4Response>
  getStage5(sid: string, ordinal: number): Promise<Stage5Response>
  getReview(sid: string, ordinal: number): Promise<ReviewResponse>
  getInbox(sid: string): Promise<InboxResponse>
  getStage6(sid: string): Promise<Stage6Response>
  getStage7(sid: string): Promise<Stage7Response>
  getStage7Chapter(sid: string, ordinal: number): Promise<Stage7Response>
  getStage7Book(sid: string): Promise<string>
  downloadStage7Book(sid: string): Promise<Blob>
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
  /** 本机 CLI/模型能力动态探针；响应与查询缓存都按 no-store 对待。 */
  getAgentCapabilities(): Promise<AgentCapabilitiesResponse>
  /** 0 成本预览：与真跑同一构造函数。两项授权都进 prompt，任一勾选变化都重新 preview。 */
  previewAgentRun(req: AgentRunRequest): Promise<AgentRunPreview>
  startAgentRun(req: AgentRunRequest): Promise<AgentRunRecord>
  /** 新的在前 */
  getAgentRuns(sid: string): Promise<AgentRunsResponse>
  /** 与 jobs 日志同一形状 {text, next_byte} */
  getAgentRunLog(runId: string, offset: number): Promise<AgentRunLogChunk>
  /** 熄火：没在跑 409 */
  stopAgentRun(runId: string): Promise<AgentRunRecord>
  /**
   * 一次交 N 本：闸内的立刻跑，超出的排队。**部分失败不回滚**——
   * 起不来的进 rejected，其余照常受理，逐本落点全在返回值里。
   */
  startAgentBatch(req: AgentBatchRequest): Promise<AgentQueueView>
  /** 谁在跑、谁在排、闸开多大。排队部分只在内存，服务重启即清空。 */
  getAgentQueue(): Promise<AgentQueueView>

  /* 人工验收记录 */
  getApprovals(sid: string): Promise<ApprovalDoc>
  /** Stage4 A 当前候选的异常优先只读工作包；不预填任何审批决定。 */
  getDesignApprovalWorkpack(sid: string): Promise<ApprovalWorkpack>
  /** Stage4 B 当前结构主体的异常优先只读工作包；不预填任何审批决定。 */
  getStructureApprovalWorkpack(sid: string): Promise<ApprovalWorkpack>
  /** Stage6 候选 + A/B 原始证据 + 机械异常的 hash-bound 紧凑终审包。 */
  getStage6ApprovalWorkpack(sid: string): Promise<Stage6ApprovalWorkpack>
  /** Stage7 人物决策 + 实际替换命中 + 残留上下文的 hash-bound 紧凑终审包。 */
  getStage7ApprovalWorkpack(sid: string): Promise<Stage7ApprovalWorkpack>
  /** 当前盘面确定性生成的 Stage5 关闭式回执模板；只读、0 成本。 */
  getStage5ClosureTemplate(sid: string): Promise<Stage5ClosureTemplate>
  /** 原子提交当前 Stage5 Commander 回执；不启动任何付费作业。 */
  approveStage5Closure(sid: string, request: Stage5ClosureRequest): Promise<ApprovalDoc>
  /** Stage4A 当前候选的关闭式审阅模板；全拒默认由服务端生成。 */
  getDesignReviewTemplate(
    sid: string,
    query: DesignReviewTemplateQuery,
  ): Promise<DesignReviewTemplate>
  /** 保存 Stage4A 正式审阅；只写审批记录，不隐式启动 B 或付费返修。 */
  approveDesignReview(sid: string, request: DesignReviewRequest): Promise<ApprovalDoc>
  approveGate(sid: string, key: GateKey): Promise<ApprovalDoc>
  /** Stage4 的专用全书 typed 裁决；只写审批记录，不启动重规划作业。 */
  approveStructure(sid: string, request: StructureApprovalRequest): Promise<ApprovalDoc>
  backfillApprovals(sid: string): Promise<ApprovalBackfillResult>
  markSeen(sid: string, key: SeenKey): Promise<ApprovalDoc>
  startWriting(sid: string): Promise<ApprovalDoc>
  resetBook(sid: string): Promise<ApprovalDoc>

  /* 写（三条人工写回走专用路由，返回真实作业记录——真实模式不写 localStorage 充成功） */
  /** 章节改稿只读预检：与 apply 共用归一、判据与双 SHA 主体，不写盘。 */
  previewDraftEdit(
    sid: string,
    ordinal: number,
    req: DraftEditPreviewRequest,
  ): Promise<DraftEditPreview>
  /** 章节改稿：只接受 preview 归一稿、人工审计、双 SHA 与服务端要求的确认语义。 */
  saveDraftEdit(
    sid: string,
    ordinal: number,
    req: DraftEditRequest,
  ): Promise<Job>
  /** Resolve one current trim verification by retaining pretrim; candidate adoption is impossible here. */
  retainPretrim(sid: string, ordinal: number, req: TrimResolutionRequest): Promise<Job>
  /** 整批预检后按章幂等销账；identity 逐项原样来自 closure workpack。 */
  retainPretrimBatch(sid: string, req: BatchTrimResolutionRequest): Promise<Job>
  /** 文案定稿：序号 + 自报身份/理由 + 候选完整 hash 乐观锁。 */
  approveHook(
    sid: string,
    req: {
      pick: number
      actor: string
      reason: string
      subject_sha256: string
      workpack_sha256: string
    },
  ): Promise<Job>
  /** 人名表定稿：自报身份/理由 + 当前 decision surface 的 hash 乐观锁。 */
  approveNames(
    sid: string,
    req: { actor: string; reason: string; subject_sha256: string; workpack_sha256: string },
  ): Promise<Job>
  /** 显式采纳当前可安全应用的 high residual；后端仍会重新校验冲突。 */
  adoptResidual(sid: string, req: { confidence: 'high' }): Promise<Job>
  uploadCorpus(file: File): Promise<UploadResponse>
  /** 可恢复删除：整本项目移入 `_service/trash/`，确认绑定当前书名。 */
  trashNovel(sid: string, expectedTitle: string): Promise<TrashNovelResult>
}
