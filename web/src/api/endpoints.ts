import type { RunMetrics } from '@/types/runMetrics'
import type { PrepareView, PrepareWorkpack } from '@/types/prepare'
/**
 * 唯一的数据缝。屏与组件只认这个文件导出的 `api`。
 *
 * 接后端时要做的事就是把 VITE_USE_MOCK 关掉。
 * 如果哪个组件自己造了数据，那一屏就会当场空掉 —— 而这正是联调时最不想找的一类 bug。
 */
import type { Api } from '@/types/api'
import type { CharacterRegistryView } from '@/types/characters'
import type { AnalyzeDraftView } from '@/types/analyzeDraft'
import type {
  AgentCapabilitiesResponse,
  AgentProfileSettingsView,
  AgentQueueView,
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunContinuationView,
  AgentRunRecord,
  AgentRunsResponse,
} from '@/types/agent'
import type {
  ApprovalDoc,
  Stage6ApprovalWorkpack,
  Stage7ApprovalWorkpack,
} from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type {
  BackendJobList,
  BackendJobRequest,
  BackendJobRecord,
  BackendJobStart,
  BackendLogChunk,
  BackendLogStreamEvent,
  Job,
  JobLogLine,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
  Stage6RebuildStatus,
  UploadCorpusOptions,
  UploadResponse,
} from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { TrashNovelResult } from '@/types/novels'
import type { RunProgress, WorkUnitGrantPreview, WorkUnitGrantResult } from '@/types/run'
import type {
  Str06BlueprintView,
  Str06CandidateList,
  Str06CharactersView,
  Str06ChapterView,
  Str06DeliveryView,
  Str06DesignView,
  Str06HookView,
  Str06IrNavView,
  Str06NamingView,
  Str06RunView,
} from '@/types/str06'
import type {
  RetentionApplyResult,
  RetentionPlan,
  RetentionPolicy,
  TrashRestorePlan,
  TrashRestoreResult,
} from '@/types/maintenance'
import type {
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
} from '@/types/stages'
import { isApiError } from './errors'
import { get, getBlob, getText, post, postRaw } from './client'
import { mockApi } from './mock'
import { projectGlobalSpoilers } from './spoilers'

const n = (sid: string) => '/novels/' + encodeURIComponent(sid)

/** STR-06 候选根路径（F7d）：候选名同样过编码，非法名由后端 404，不拼路径。 */
const str06 = (candidate: string, suffix: string) =>
  '/str06/candidates/' + encodeURIComponent(candidate) + '/' + suffix

/**
 * 当前后端的 NovelOverview 已收敛成「八个 StageState 的列表」，而冻结 mock 仍是
 * 早期的「stage -> artifacts」对象。这个适配只放在真实数据缝里，屏幕不认识两套形状。
 */
interface BackendArtifactState {
  rel: string
  exists: boolean
  bytes: number | null
  mtime: number | null
  freshness: Artifact['freshness']
  display_freshness: Artifact['display_freshness']
  condition: Artifact['condition']
  reason: string
  ghosts: string[]
  invalid: boolean
  invalid_freshness: Artifact['invalid_freshness']
  invalid_reason: Artifact['invalid_reason']
  readable: boolean
}

interface BackendStageState {
  stage: string
  artifacts: BackendArtifactState[]
  done: boolean | null
  chapters_done: number[]
  reviewable: boolean
}

interface BackendNovelOverview {
  sid: string
  title: string
  dirname: string
  chapters: number | null
  stages: BackendStageState[]
  summary: NovelOverview['summary']
}

interface BackendNovelList {
  base: string
  novels: BackendNovelOverview[]
}

export function adaptNovel(raw: BackendNovelOverview): NovelOverview {
  const stages: Record<string, Artifact[]> = {}
  const stageDone: Record<string, boolean | null> = {}
  const chaptersDone: Record<string, number[]> = {}
  const stageReviewable: Record<string, boolean> = {}

  for (const state of raw.stages) {
    stageDone[state.stage] = state.done
    chaptersDone[state.stage] = state.chapters_done
    stageReviewable[state.stage] = state.reviewable
    stages[state.stage] = state.artifacts.map((artifact) => ({
      stage: state.stage,
      name: artifact.rel.split('/').at(-1) ?? artifact.rel,
      exists: artifact.exists,
      bytes: artifact.bytes,
      mtime: artifact.mtime,
      provenance: {},
      freshness: artifact.freshness,
      display_freshness: artifact.display_freshness,
      condition: artifact.condition,
      stale: [],
      reason: artifact.reason,
      ghosts: artifact.ghosts,
      invalid: artifact.invalid,
      invalid_freshness: artifact.invalid_freshness,
      invalid_reason: artifact.invalid_reason,
      readable: artifact.readable,
    }))
  }

  return {
    sid: raw.sid,
    title: raw.title,
    dir: raw.dirname,
    dirname: raw.dirname,
    chapters: raw.chapters,
    stages,
    stage_done: stageDone,
    stage_reviewable: stageReviewable,
    chapters_done: chaptersDone,
    summary: raw.summary,
  }
}

function quoteArg(value: string): string {
  if (/^[\w./:=@,+-]+$/u.test(value)) return value
  const apostrophe = String.fromCharCode(39)
  return apostrophe + value.replaceAll(apostrophe, apostrophe + apostrophe) + apostrophe
}

function commandOf(start: BackendJobStart): string {
  return start.steps.map((step) => step.argv.map(quoteArg).join(' ')).join('\n')
}

function labelOf(start: BackendJobStart): string {
  return start.steps.map((step) => step.label).join(' → ')
}

function adaptJob(record: BackendJobRecord, sidOverride?: string): Job {
  return {
    id: record.job_id,
    kind: record.start.node,
    label: labelOf(record.start),
    argv: commandOf(record.start),
    state: record.state,
    paid: record.start.paid,
    started: record.started,
    finished: record.ended,
    sid: sidOverride ?? record.start.sid,
    ordinal: null,
    executor_backend: record.start.executor_backend,
    egress_authorized: record.start.egress_authorized,
  }
}

function adaptPreview(start: BackendJobStart): JobPreview {
  return {
    argv: commandOf(start),
    paid: start.paid,
    label: labelOf(start),
    kind: start.node,
    executor_backend: start.executor_backend,
    egress_authorized: start.egress_authorized,
    steps: start.steps.map((step) => ({
      label: step.label,
      paid: step.paid,
      writes: step.writes,
      argv: step.argv.map(quoteArg).join(' '),
      executor_backend: step.executor_backend,
      egress_authorized: step.egress_authorized,
    })),
  }
}

function backendJobRequest(req: JobRequest): BackendJobRequest {
  return {
    sid: req.sid,
    node: req.kind,
    flags: [...(req.extra ?? []), ...(req.args ?? [])],
  }
}

const OBSERVE_PREFIX = '@observe '

function observationOf(
  line: string,
): { line: JobLogLine; key: string | null } | null {
  if (!line.startsWith(OBSERVE_PREFIX)) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(line.slice(OBSERVE_PREFIX.length))
  } catch {
    return {
      line: {
        text: '[遥测] 有一条事件无法解析',
        raw: line,
        details: { raw_payload: line.slice(OBSERVE_PREFIX.length) },
        kind: 'malformed',
      },
      key: null,
    }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {
      line: {
        text: '[遥测] 事件不是 JSON 对象',
        raw: line,
        details: { value: parsed },
        kind: 'malformed',
      },
      key: null,
    }
  }
  const row = parsed as Record<string, unknown>
  const event = typeof row.event === 'string' ? row.event : 'unknown'
  const chapter = typeof row.chapter === 'number' ? 'ch' + row.chapter + ' · ' : ''
  const elapsed =
    typeof row.elapsed_ms === 'number' ? Math.max(0, Math.round(row.elapsed_ms / 1000)) + ' s' : null
  if (event === 'llm_attempt_heartbeat' || event === 'llm_attempt_finished') {
    const attempt = typeof row.attempt === 'number' ? row.attempt : '?'
    const max = typeof row.max_attempts === 'number' ? row.max_attempts : '?'
    const purpose = typeof row.purpose === 'string' ? row.purpose : 'LLM 调用'
    const state =
      row.state === 'started'
        ? '已发起'
        : row.state === 'in_flight'
          ? '进行中'
          : row.state === 'ok'
            ? '完成'
            : row.state === 'error'
              ? '失败'
              : String(row.state ?? '状态未知')
    const detail = [chapter + purpose, '第 ' + attempt + '/' + max + ' 次尝试', elapsed, state]
      .filter(Boolean)
      .join(' · ')
    const diagnostics = [
      typeof row.error_kind === 'string' ? 'error_kind=' + row.error_kind : null,
      typeof row.detail === 'string' ? row.detail : null,
    ].filter(Boolean)
    return {
      line: {
        text: '[LLM] ' + detail + (diagnostics.length ? ' · ' + diagnostics.join(' · ') : ''),
        raw: line,
        details: row,
        kind: 'observation',
      },
      key: ['llm', purpose, chapter, attempt].join(':'),
    }
  }
  if (event === 'chapter_progress') {
    const completed = typeof row.completed === 'number' ? row.completed : '?'
    const total = typeof row.chapter_total === 'number' ? row.chapter_total : '?'
    return {
      line: {
        text:
        '[章节] ' +
        chapter +
        completed +
        '/' +
        total +
        ' 已结算 · ' +
        String(row.outcome ?? '状态未知'),
        raw: line,
        details: row,
        kind: 'observation',
      },
      key: null,
    }
  }
  return {
    line: {
      text: '[遥测] ' + event + '（展开查看完整字段）',
      raw: line,
      details: row,
      kind: 'observation',
    },
    key: null,
  }
}

export function splitLog(text: string): JobLogLine[] {
  if (!text) return []
  const lines = text.replaceAll('\r\n', '\n').split('\n')
  if (lines.at(-1) === '') lines.pop()
  const out: JobLogLine[] = []
  let lastObservationKey: string | null = null
  for (const line of lines) {
    const observation = observationOf(line)
    if (!observation) {
      out.push({ text: line, raw: null, details: null, kind: 'plain' })
      lastObservationKey = null
      continue
    }
    if (observation.key !== null && observation.key === lastObservationKey) {
      out[out.length - 1] = observation.line
    } else {
      out.push(observation.line)
    }
    lastObservationKey = observation.key
  }
  return out
}

/**
 * 真实 HTTP 实现。Stage 0–1 的正文、编辑和确定性作业都直接走 novel 服务。
 * 组件只消费前端视图；jobs.jsonl 的嵌套记录在这条数据缝统一适配。
 */
export const realApi: Api = {
  getNovels: async () => {
    const raw = await get<BackendNovelList>('/novels')
    return { base: raw.base, novels: raw.novels.map(adaptNovel) } satisfies NovelsResponse
  },
  getNovel: async (sid) => adaptNovel(await get<BackendNovelOverview>(n(sid))),
  getEnums: () => get<EnumsResponse>('/enums'),
  // run 的响应已是视图形状（snake_case 照后端原样），数据缝不做适配
  getRunProgress: (sid) => get<RunProgress>(n(sid) + '/run'),
  getRunMetrics: (sid, runId) => get<RunMetrics>(n(sid) + '/run/metrics' + (runId ? '?run_id=' + encodeURIComponent(runId) : '')),
  previewWorkUnitGrant: (sid, unitId, additional) => get<WorkUnitGrantPreview>(
    n(sid) + '/work-units/' + encodeURIComponent(unitId) + '/grant?additional=' + additional),
  grantWorkUnit: (sid, unitId, request) => post<WorkUnitGrantResult>(
    n(sid) + '/work-units/' + encodeURIComponent(unitId) + '/grant', request),

  getStage0Report: (sid) => get<Stage0Report>(n(sid) + '/stage0/report'),
  getStage0Segments: (sid) => get<Stage0Segments>(n(sid) + '/stage0/segments'),
  // stage0 人工写面与手动 partition 已退役；Stage 页只读，上传后自动开跑。
  getStage1: (sid) => get<Stage1View>(n(sid) + '/stage1'),
  getReading: (sid, o) => get<ReadingResponse>(n(sid) + '/reading/' + o),
  getBlueprintReadingWorkpack: (sid, scope) =>
    get<BlueprintReadingWorkpack>(n(sid) + '/stage2/blueprint-reading-workpack'
      + (scope ? '?scope=' + encodeURIComponent(scope) : '')),
  getGlobal: async (sid, revealSpoilers = false) =>
    projectGlobalSpoilers(await get<Stage2Global>(n(sid) + '/stage2/global'), revealSpoilers),
  getCharacters: (sid) => get<CharacterRegistryView>(n(sid) + '/stage2/characters'),
  getAnalyzeDraft: (sid, owner) => get<AnalyzeDraftView>(n(sid) + '/stage2/analyze-draft?owner=' + encodeURIComponent(owner)),
  getStage4: (sid, o) => get<Stage4Response>(n(sid) + '/stage4/' + o),
  getStage5: (sid, o) => get<Stage5Response>(n(sid) + '/stage5/' + o),
  getReview: (sid, o) => get<ReviewResponse>(n(sid) + '/review/' + o),
  getInbox: (sid) => get<InboxResponse>(n(sid) + '/inbox'),
  getStage6: (sid) => get<Stage6Response>(n(sid) + '/stage6'),
  getStage6Prepare: (sid) => get<PrepareView>(n(sid) + '/stage6/prepare'),
  getStage6PrepareWorkpack: (sid) => get<PrepareWorkpack>(n(sid) + '/stage6/prepare/workpack'),
  getStage6Rebuild: (sid) => get<Stage6RebuildStatus>(n(sid) + '/stage6/rebuild'),
  getStage7: (sid) => get<Stage7Response>(n(sid) + '/stage7'),
  getStage7Chapter: (sid, o) => get<Stage7Response>(n(sid) + '/stage7/' + o),
  getStage7Book: (sid) => getText(n(sid) + '/stage7/book.txt'),
  downloadStage7Book: (sid) => getBlob(n(sid) + '/stage7/book.txt?download=true'),
  // 后端对「这一层还没跑」答 404（资源不存在）；Api 契约里 null = 「还没有报告」。
  // 在这里翻译（唯一数据缝的职责），CheckPanel 不用自己捕 404。
  getCheck: async (sid, kind) => {
    try {
      return await get<CheckReport>(n(sid) + '/check/' + kind)
    } catch (e) {
      if (isApiError(e) && e.kind === 'notfound') return null
      throw e
    }
  },

  // 真后端上每一章都有数据，没有「样本边界」这回事
  getSampleCoverage: () => Promise.resolve(null),

  getJobs: async (sid) => {
    const raw = await get<BackendJobList>('/jobs?sid=' + encodeURIComponent(sid))
    const jobs = raw.jobs.map((record) => adaptJob(record)).reverse()
    return {
      jobs,
      running: jobs.find((job) => job.state === 'running') ?? null,
    } satisfies JobsResponse
  },
  getJobLog: async (id, offset) => {
    const [chunk, record] = await Promise.all([
      get<BackendLogChunk>('/jobs/' + encodeURIComponent(id) + '/log?offset=' + offset),
      get<BackendJobRecord>('/jobs/' + encodeURIComponent(id)),
    ])
    return {
      lines: splitLog(chunk.text),
      offset: chunk.next_byte,
      done: record.state !== 'running',
    } satisfies JobLogResponse
  },
  streamJobLog: (id, offset, sink) => {
    const path = '/jobs/' + encodeURIComponent(id) + '/stream?offset=' + offset
    const source = new EventSource('/api' + path)
    let closed = false

    const receive = (raw: Event) => {
      try {
        const event = raw as MessageEvent<string>
        const parsed = JSON.parse(event.data) as BackendLogStreamEvent
        if (
          typeof parsed.text !== 'string' ||
          !Number.isInteger(parsed.next_byte) ||
          parsed.next_byte < 0 ||
          typeof parsed.done !== 'boolean'
        ) {
          throw new Error('invalid job log SSE event')
        }
        sink.onEvent(parsed)
        if (parsed.done) {
          closed = true
          source.close()
        }
      } catch {
        closed = true
        source.close()
        sink.onError(new Error('malformed job log SSE event'))
      }
    }
    source.addEventListener('log', receive)
    source.addEventListener('end', receive)
    source.onerror = () => {
      // CONNECTING 会由 EventSource 携 Last-Event-ID 自动续连；只有明确 CLOSED 才交给界面。
      if (!closed && source.readyState === EventSource.CLOSED) {
        sink.onError(new Error('job log SSE closed before terminal event'))
      }
    }
    return () => {
      closed = true
      source.close()
    }
  },
  previewJob: async (req) =>
    adaptPreview(await post<BackendJobStart>('/jobs/preview', backendJobRequest(req))),
  startJob: async (req) =>
    adaptJob(await post<BackendJobRecord>('/jobs', backendJobRequest(req))),

  /* agent 代跑：响应已是视图形状（snake_case 原样），数据缝不做适配 */
  getAgentCapabilities: () => get<AgentCapabilitiesResponse>('/agent/capabilities'),
  getAgentProfileSettings: () =>
    get<AgentProfileSettingsView>('/settings/agent-profiles'),
  saveAgentProfileSettings: (req) =>
    post<AgentProfileSettingsView>('/settings/agent-profiles', req),
  getAgentRunContinuation: (runId) => get<AgentRunContinuationView>(`/agent/runs/${encodeURIComponent(runId)}/continuation`),
  previewAgentRun: (req) => post<AgentRunPreview>('/agent/runs/preview', req),
  startAgentRun: (req) => post<AgentRunRecord>('/agent/runs', req),
  getAgentRuns: (sid) => get<AgentRunsResponse>('/agent/runs?sid=' + encodeURIComponent(sid)),
  getAgentRunLog: (runId, offset) =>
    get<AgentRunLogChunk>('/agent/runs/' + encodeURIComponent(runId) + '/log?offset=' + offset),
  stopAgentRun: (runId) =>
    post<AgentRunRecord>('/agent/runs/' + encodeURIComponent(runId) + '/stop'),
  pauseAgentRun: (runId) =>
    post<AgentRunRecord>('/agent/runs/' + encodeURIComponent(runId) + '/pause'),
  resumeAgentRun: (runId) =>
    post<AgentRunRecord>('/agent/runs/' + encodeURIComponent(runId) + '/resume'),
  startAgentBatch: (req) => post<AgentQueueView>('/agent/runs/batch', req),
  getAgentQueue: () => get<AgentQueueView>('/agent/runs/queue'),

  getApprovals: (sid) => get<ApprovalDoc>(n(sid) + '/approvals'),
  // stage4 design/structure workpack、模板与 POST 写面已随 2026-09-15 全自动裁决退役；
  // Stage4 人工闸只剩只读审批投影，机械准入是唯一打开条件。
  getStage6ApprovalWorkpack: (sid) =>
    get<Stage6ApprovalWorkpack>(n(sid) + '/approvals/stage6/workpack'),
  getStage7ApprovalWorkpack: (sid) =>
    get<Stage7ApprovalWorkpack>(n(sid) + '/approvals/stage7/workpack'),
  resetBook: (sid) => post<ApprovalDoc>(n(sid) + '/approvals/reset'),

  approveHook: async (sid, req) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage6/approve', req)),
  uploadCorpus: async (file, options: UploadCorpusOptions) => {
    const params = new URLSearchParams({
      filename: file.name,
      authorized: options.authorized ? 'true' : 'false',
      egress_authorized: options.egressAuthorized ? 'true' : 'false',
      profiles: JSON.stringify(options.profiles),
    })
    const raw = await postRaw<{
      sid: string
      job: BackendJobRecord
      run?: UploadResponse['run']
      autostart?: UploadResponse['autostart']
      idempotency_key?: string
    }>(
      '/upload?' + params.toString(),
      file,
      { 'X-Novel-Idempotency-Key': options.idempotencyKey },
    )
    return {
      sid: raw.sid,
      job: adaptJob(raw.job, raw.sid),
      run: raw.run ?? null,
      autostart: raw.autostart ?? null,
      idempotency_key: raw.idempotency_key ?? options.idempotencyKey,
    }
  },
  trashNovel: (sid, expectedTitle) =>
    post<TrashNovelResult>(n(sid) + '/trash', { expected_title: expectedTitle }),
  previewRetention: (policy: RetentionPolicy) =>
    post<RetentionPlan>('/maintenance/retention/preview', policy),
  applyRetention: (plan, confirmed) =>
    post<RetentionApplyResult>('/maintenance/retention/apply', { plan, confirmed }),
  previewTrashRestore: (trashId, expectedTitle) =>
    post<TrashRestorePlan>('/maintenance/trash/restore/preview', {
      trash_id: trashId,
      expected_title: expectedTitle,
    }),
  applyTrashRestore: (plan, confirmed) =>
    post<TrashRestoreResult>('/maintenance/trash/restore/apply', { plan, confirmed }),

  // STR-06 候选链只读监控：与现役 `/novels` 面物理分开的另一棵路由树（F7d）。
  getStr06Candidates: () => get<Str06CandidateList>('/str06/candidates'),
  getStr06Run: (candidate) => get<Str06RunView>(str06(candidate, 'run')),
  getStr06Characters: (candidate) => get<Str06CharactersView>(str06(candidate, 'characters')),
  getStr06Blueprint: (candidate) => get<Str06BlueprintView>(str06(candidate, 'blueprint')),
  getStr06Design: (candidate) => get<Str06DesignView>(str06(candidate, 'design')),
  getStr06Ir: (candidate) => get<Str06IrNavView>(str06(candidate, 'ir')),
  getStr06Chapter: (candidate, o) => get<Str06ChapterView>(str06(candidate, 'chapters/' + o)),
  getStr06Hook: (candidate) => get<Str06HookView>(str06(candidate, 'hook')),
  getStr06Naming: (candidate) => get<Str06NamingView>(str06(candidate, 'naming')),
  getStr06Delivery: (candidate) => get<Str06DeliveryView>(str06(candidate, 'delivery')),
  getStr06Book: (candidate) => getText(str06(candidate, 'book')),
}

/** 编译期常量：VITE_USE_MOCK 会被 Vite 替换成字面量，于是这个三元能被静态折叠 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = USE_MOCK ? mockApi : realApi
