import type { RunMetrics } from './runMetrics'
/**
 * 唯一的数据缝。
 *
 * src/api/mock.ts 与 src/api/endpoints.ts 里的真实实现都 `satisfies Api`，
 * 所以两条路径同型是 **tsc 强制**的，不是口头约定。
 * 组件与屏从不 fetch，只经 hooks → api.*。
 *
 * ⚠ STR-06 切换（F8c）：旧链读屏面（reading / blueprint-reading-workpack /
 * global / characters / analyze-draft / stage4 / stage5 / review / inbox /
 * stage7 / book 下载）与人工闸面（approvals / workpack / reset）整族退役，
 * 对应方法已从本接口删除——不为已退役的端点保留第二个落点。新链只读监控面
 * 走 `getStr06*`（`/api/str06/candidates/{candidate}/*`）。
 */
import type {
  AgentCapabilitiesResponse,
  AgentBatchRequest,
  AgentProfileSettingsUpdate,
  AgentProfileSettingsView,
  NetworkSettingsUpdate,
  NetworkSettingsView,
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
  Stage0Report,
  Stage0Segments,
  Stage1View,
} from './stages'
import type {
  Str06CandidateList,
  Str06ChapterView,
  Str06DeliveryView,
  Str06HookView,
  Str06SourceStoryView,
  Str06TargetStoryView,
  Str06RunView,
} from './str06'

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
  /**
   * 层级复检报告。`null` = 服务端说这一层还没有报告（≠ 通过）。
   * ⚠ F8c 起取值面只剩 stage0/stage1：新链 Stage2/4/5/7 的复检是节点级
   * `--check-only`，层名不在取值面里后端答 400。
   */
  getCheck(sid: string, kind: CheckKind): Promise<CheckReport | null>

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
  /** 当前 output base 的网络偏好（付费外发直连/代理）；默认直连。 */
  getNetworkSettings(): Promise<NetworkSettingsView>
  /** CAS 保存/清空网络偏好；run 起跑时注入付费子进程，不影响已启动的 run。 */
  saveNetworkSettings(req: NetworkSettingsUpdate): Promise<NetworkSettingsView>
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

  /* STR-06 候选链只读监控：隔离候选根的十二条 GET，与现役 `/novels` 面物理分开 */
  getStr06Candidates(): Promise<Str06CandidateList>
  getStr06Run(candidate: string): Promise<Str06RunView>
  getStr06SourceStory(candidate: string): Promise<Str06SourceStoryView>
  getStr06TargetStory(candidate: string): Promise<Str06TargetStoryView>
  getStr06Chapter(candidate: string, ordinal: number): Promise<Str06ChapterView>
  getStr06Hook(candidate: string): Promise<Str06HookView>
  getStr06Delivery(candidate: string): Promise<Str06DeliveryView>
  getStr06Book(candidate: string): Promise<{ text: string; filename: string }>
}
