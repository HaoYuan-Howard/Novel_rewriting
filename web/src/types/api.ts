import type { RunMetrics } from './runMetrics'
/**
 * 唯一的数据缝。
 *
 * src/api/mock.ts 与 src/api/endpoints.ts 里的真实实现都 `satisfies Api`，
 * 所以两条路径同型是 **tsc 强制**的，不是口头约定。
 * 组件与屏从不 fetch，只经 hooks → api.*。
 */
import type {
  ApprovalDoc,
  Stage6ApprovalWorkpack,
  Stage7ApprovalWorkpack,
} from './approvals'
import type {
  AgentCapabilitiesResponse,
  AgentBatchRequest,
  AgentProfileSettingsUpdate,
  AgentProfileSettingsView,
  AgentQueueView,
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunContinuationView,
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
  Stage6RebuildStatus,
  UploadCorpusOptions,
  UploadResponse,
} from './jobs'
import type { NovelOverview, NovelsResponse } from './novels'
import type { TrashNovelResult } from './novels'
import type { RunProgress, WorkUnitGrantPreview, WorkUnitGrantRequest, WorkUnitGrantResult } from './run'
import type {
  RetentionApplyResult,
  RetentionPlan,
  RetentionPolicy,
  TrashRestorePlan,
  TrashRestoreResult,
} from './maintenance'
import type {
  CheckKind,
  CheckReport,
  BlueprintReadingWorkpack,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0Report,
  Stage0Segments,
  Stage1View,
  Stage2Global,
  Stage4Response,
  Stage5Response,
  Stage6Response,
  Stage7Response,
} from './stages'

import type { CharacterRegistryView } from './characters'
import type { AnalyzeDraftView, AnalyzeDraftOwner } from './analyzeDraft'
import type {
  PrepareView, PrepareWorkpack,
} from './prepare'

export interface Api {
  /* 读 */
  getNovels(): Promise<NovelsResponse>
  getNovel(sid: string): Promise<NovelOverview>
  getEnums(): Promise<EnumsResponse>
  /** 跑书进度：NODES 信标 + 各层产物完成度。beacon=ok 只念作「跑完」，判定归各层屏的 badge。 */
  getRunProgress(sid: string): Promise<RunProgress>
  getRunMetrics(sid: string, runId?: string): Promise<RunMetrics>
  previewWorkUnitGrant(sid: string, unitId: string, additional: number): Promise<WorkUnitGrantPreview>
  grantWorkUnit(sid: string, unitId: string, request: WorkUnitGrantRequest): Promise<WorkUnitGrantResult>

  getStage0Report(sid: string): Promise<Stage0Report>
  getStage0Segments(sid: string): Promise<Stage0Segments>
  // stage0 的人工编辑/批准/候选三选一与手动 partition 已随全自动裁决退役；
  // Stage0/1 面只剩只读展示，上传后由后端自动开跑。
  getStage1(sid: string): Promise<Stage1View>
  getReading(sid: string, ordinal: number): Promise<ReadingResponse>
  /** Stage2 语义审阅退役后的原文↔蓝图只读阅读工作包；不携带审阅或接受语义。 */
  getBlueprintReadingWorkpack(sid: string, scope?: string): Promise<BlueprintReadingWorkpack>
  getGlobal(sid: string, revealSpoilers?: boolean): Promise<Stage2Global>
  getCharacters(sid: string): Promise<CharacterRegistryView>
  getAnalyzeDraft(sid: string, owner: AnalyzeDraftOwner): Promise<AnalyzeDraftView>
  getStage4(sid: string, ordinal: number): Promise<Stage4Response>
  getStage5(sid: string, ordinal: number): Promise<Stage5Response>
  getReview(sid: string, ordinal: number): Promise<ReviewResponse>
  getInbox(sid: string): Promise<InboxResponse>
  getStage6(sid: string): Promise<Stage6Response>
  getStage6Prepare(sid: string): Promise<PrepareView>
  getStage6PrepareWorkpack(sid: string): Promise<PrepareWorkpack>
  /** Stage6 改选后的重建排队 / 成书对齐状态。 */
  getStage6Rebuild(sid: string): Promise<Stage6RebuildStatus>
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
  /** 当前 output base 的用户常用配置；不含凭据与两项逐次授权。 */
  getAgentProfileSettings(): Promise<AgentProfileSettingsView>
  /** CAS 保存/清空常用配置；不改变任何历史或排队中的 run 快照。 */
  saveAgentProfileSettings(req: AgentProfileSettingsUpdate): Promise<AgentProfileSettingsView>
  /** 0 成本预览：与真跑同一构造函数。两项授权都进 prompt，任一勾选变化都重新 preview。 */
  getAgentRunContinuation(runId: string): Promise<AgentRunContinuationView>
  previewAgentRun(req: AgentRunRequest): Promise<AgentRunPreview>
  startAgentRun(req: AgentRunRequest): Promise<AgentRunRecord>
  /** 新的在前 */
  getAgentRuns(sid: string): Promise<AgentRunsResponse>
  /** 与 jobs 日志同一形状 {text, next_byte} */
  getAgentRunLog(runId: string, offset: number): Promise<AgentRunLogChunk>
  /** 熄火：没在跑 409 */
  stopAgentRun(runId: string): Promise<AgentRunRecord>
  /** 协作式暂停：关闭新 claim，当前节点自然闭合后进入 paused。 */
  pauseAgentRun(runId: string): Promise<AgentRunRecord>
  /** 只恢复服务端仍持有 owner/书锁且明确开放 resume 的 run。 */
  resumeAgentRun(runId: string): Promise<AgentRunRecord>
  /**
   * 一次交 N 本：闸内的立刻跑，超出的排队。**部分失败不回滚**——
   * 起不来的进 rejected，其余照常受理，逐本落点全在返回值里。
   */
  startAgentBatch(req: AgentBatchRequest): Promise<AgentQueueView>
  /** 谁在跑、谁在排、闸开多大。排队部分只在内存，服务重启即清空。 */
  getAgentQueue(): Promise<AgentQueueView>

  /* 只读审批投影 */
  getApprovals(sid: string): Promise<ApprovalDoc>
  // stage4/5 人工闸与通用 gate 写面已退役；Stage 页只读监控。
  /** Stage6 候选 + A/B 原始证据 + 机械异常的 hash-bound 紧凑终审包。 */
  getStage6ApprovalWorkpack(sid: string): Promise<Stage6ApprovalWorkpack>
  /** Stage7 人物决策 + 实际替换命中 + 残留上下文的 hash-bound 紧凑终审包。 */
  getStage7ApprovalWorkpack(sid: string): Promise<Stage7ApprovalWorkpack>
  resetBook(sid: string): Promise<ApprovalDoc>

  /**
   * 文案定稿 / 三候选改选：序号 + 自报身份/理由 + immutable workpack token。
   * Stage 页唯一保留的人工写动作。
   */
  approveHook(
    sid: string,
    req: {
      pick: number
      actor: string
      reason: string
      review_token: string
    },
  ): Promise<Job>
  /**
   * 上传并自动开跑。Header 带幂等键；授权与 profiles 走 query；body 仍是文件字节。
   */
  uploadCorpus(file: File, options: UploadCorpusOptions): Promise<UploadResponse>
  /** 可恢复删除：整本项目移入 `_service/trash/`，确认绑定当前书名。 */
  trashNovel(sid: string, expectedTitle: string): Promise<TrashNovelResult>

  /* service 生命周期维护：preview 与 apply 必须传递同一份完整 token/CAS。 */
  previewRetention(policy: RetentionPolicy): Promise<RetentionPlan>
  applyRetention(plan: RetentionPlan, confirmed: boolean): Promise<RetentionApplyResult>
  previewTrashRestore(trashId: string, expectedTitle: string): Promise<TrashRestorePlan>
  applyTrashRestore(plan: TrashRestorePlan, confirmed: boolean): Promise<TrashRestoreResult>
}
