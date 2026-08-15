/**
 * 唯一的数据缝。屏与组件只认这个文件导出的 `api`。
 *
 * 接后端时要做的事就是把 VITE_USE_MOCK 关掉。
 * 如果哪个组件自己造了数据，那一屏就会当场空掉 —— 而这正是联调时最不想找的一类 bug。
 */
import type { Api } from '@/types/api'
import type {
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunRecord,
  AgentRunsResponse,
} from '@/types/agent'
import type { ApprovalDoc, GateKey, SeenKey } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type {
  BackendJobList,
  BackendJobRequest,
  BackendJobRecord,
  BackendJobStart,
  BackendLogChunk,
  BackendLogStreamEvent,
  Job,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
} from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { RunProgress } from '@/types/run'
import type {
  CheckReport,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
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
} from '@/types/stages'
import { isApiError } from './errors'
import { get, post, postRaw } from './client'
import { mockApi } from './mock'

const n = (sid: string) => '/novels/' + encodeURIComponent(sid)

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
  readable: boolean
}

interface BackendStageState {
  stage: string
  artifacts: BackendArtifactState[]
  done: boolean | null
  chapters_done: Record<string, number[]>
  reviewable: boolean
}

interface BackendNovelOverview {
  sid: string
  title: string
  dirname: string
  stages: BackendStageState[]
  stage5_modes: string[]
  summary: NovelOverview['summary']
}

interface BackendNovelList {
  base: string
  novels: BackendNovelOverview[]
}

function inferChapters(stages: BackendStageState[]): number | null {
  const stage5 = stages.find((stage) => stage.stage === 'stage5')
  // 半本书已经写了几章，不等于这本书总共有几章；只在整层完成时才用它报总数。
  if (stage5?.done !== true) return null
  const ordinals = new Set<number>()
  for (const values of Object.values(stage5.chapters_done)) {
    for (const ordinal of values) ordinals.add(ordinal)
  }
  return ordinals.size || null
}

function adaptNovel(raw: BackendNovelOverview): NovelOverview {
  const stages: Record<string, Artifact[]> = {}
  const stageDone: Record<string, boolean | null> = {}
  const chaptersDone: Record<string, Record<string, number[]>> = {}
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
      readable: artifact.readable,
    }))
  }

  return {
    sid: raw.sid,
    title: raw.title,
    dir: raw.dirname,
    dirname: raw.dirname,
    chapters: inferChapters(raw.stages),
    stages,
    stage5_modes: raw.stage5_modes,
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
  }
}

function adaptPreview(start: BackendJobStart): JobPreview {
  return {
    argv: commandOf(start),
    paid: start.paid,
    label: labelOf(start),
    kind: start.node,
  }
}

function backendJobRequest(req: JobRequest): BackendJobRequest {
  return {
    sid: req.sid,
    node: req.kind,
    mode: req.mode ?? null,
    flags: [...(req.extra ?? []), ...(req.args ?? [])],
  }
}

export function splitLog(text: string): string[] {
  if (!text) return []
  const lines = text.replaceAll('\r\n', '\n').split('\n')
  if (lines.at(-1) === '') lines.pop()
  return lines
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
  getNovel: async (sid) => {
    const novel = adaptNovel(await get<BackendNovelOverview>(n(sid)))
    if (novel.chapters !== null || novel.stage_done?.stage1 !== true) return novel

    // 书详情只补一次章数；语料库不做 N+1。章表失败不该带走整本书的顶部信息。
    try {
      const index = await get<Stage1Chapters>(n(sid) + '/stage1/chapters')
      return { ...novel, chapters: index.chapters.length }
    } catch {
      return novel
    }
  },
  getEnums: () => get<EnumsResponse>('/enums'),
  // run 的响应已是视图形状（snake_case 照后端原样），数据缝不做适配
  getRunProgress: (sid) => get<RunProgress>(n(sid) + '/run'),

  getStage0Report: (sid) => get<Stage0Report>(n(sid) + '/stage0/report'),
  getStage0Segments: (sid) => get<Stage0Segments>(n(sid) + '/stage0/segments'),
  editStage0: async (sid, request) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage0/edit', request)),
  approveStage0: async (sid) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage0/approve')),
  partitionStage1: async (sid) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage1/partition')),
  getStage1Segments: (sid) => get<Stage1Segments>(n(sid) + '/stage1/segments'),
  getStage1Chapters: (sid) => get<Stage1Chapters>(n(sid) + '/stage1/chapters'),
  getStage1HookCopy: (sid) => get<Stage1HookCopy>(n(sid) + '/stage1/hook_copy'),
  getReading: (sid, o) => get<ReadingResponse>(n(sid) + '/reading/' + o),
  getGlobal: (sid) => get<Stage2Global>(n(sid) + '/stage2/global'),
  getStyle: (sid) => get<Stage3Style>(n(sid) + '/stage3/style'),
  getStage4: (sid, o) => get<Stage4Response>(n(sid) + '/stage4/' + o),
  getStage5: (sid, o) => get<Stage5Response>(n(sid) + '/stage5/' + o),
  getReview: (sid, o) => get<ReviewResponse>(n(sid) + '/review/' + o),
  getInbox: (sid) => get<InboxResponse>(n(sid) + '/inbox'),
  getStage6: (sid) => get<Stage6Response>(n(sid) + '/stage6'),
  getStage7: (sid) => get<Stage7Response>(n(sid) + '/stage7'),
  getStage7Chapter: (sid, o) => get<Stage7Response>(n(sid) + '/stage7/' + o),
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
  previewAgentRun: (req) => post<AgentRunPreview>('/agent/runs/preview', req),
  startAgentRun: (req) => post<AgentRunRecord>('/agent/runs', req),
  getAgentRuns: (sid) => get<AgentRunsResponse>('/agent/runs?sid=' + encodeURIComponent(sid)),
  getAgentRunLog: (runId, offset) =>
    get<AgentRunLogChunk>('/agent/runs/' + encodeURIComponent(runId) + '/log?offset=' + offset),
  stopAgentRun: (runId) =>
    post<AgentRunRecord>('/agent/runs/' + encodeURIComponent(runId) + '/stop'),

  getApprovals: (sid) => get<ApprovalDoc>(n(sid) + '/approvals'),
  approveGate: (sid, key: GateKey) => post<ApprovalDoc>(n(sid) + '/approvals/gate/' + key),
  markSeen: (sid, key: SeenKey) => post<ApprovalDoc>(n(sid) + '/approvals/seen/' + key),
  setDeep: (sid, deep) => post<ApprovalDoc>(n(sid) + '/approvals/deep', { deep }),
  startWriting: (sid) => post<ApprovalDoc>(n(sid) + '/approvals/writing'),
  resetBook: (sid) => post<ApprovalDoc>(n(sid) + '/approvals/reset'),

  saveDraftEdit: async (sid, o, req) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/review/' + o + '/edit', req)),
  approveHook: async (sid, pick) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage6/approve', { pick })),
  approveNames: async (sid) =>
    adaptJob(await post<BackendJobRecord>(n(sid) + '/stage7/approve', {})),
  uploadCorpus: async (file) => {
    const raw = await postRaw<{ sid: string; job: BackendJobRecord }>(
      '/upload?filename=' + encodeURIComponent(file.name),
      file,
    )
    return { sid: raw.sid, job: adaptJob(raw.job, raw.sid) }
  },
}

/** 编译期常量：VITE_USE_MOCK 会被 Vite 替换成字面量，于是这个三元能被静态折叠 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = USE_MOCK ? mockApi : realApi
