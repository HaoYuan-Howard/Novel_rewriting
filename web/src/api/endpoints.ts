/**
 * 唯一的数据缝。屏与组件只认这个文件导出的 `api`。
 *
 * 接后端时要做的事就是把 VITE_USE_MOCK 关掉。
 * 如果哪个组件自己造了数据，那一屏就会当场空掉 —— 而这正是联调时最不想找的一类 bug。
 */
import type { Api } from '@/types/api'
import type { ApprovalDoc, GateKey, SeenKey } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type {
  BackendJobList,
  BackendJobRequest,
  BackendJobRecord,
  BackendJobStart,
  BackendLogChunk,
  Job,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
} from '@/types/jobs'
import type { NovelOverview, NovelsResponse } from '@/types/novels'
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

function splitLog(text: string): string[] {
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
  getNovels: () => get<NovelsResponse>('/novels'),
  getNovel: (sid) => get<NovelOverview>(n(sid)),
  getEnums: () => get<EnumsResponse>('/enums'),

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
  previewJob: async (req) =>
    adaptPreview(await post<BackendJobStart>('/jobs/preview', backendJobRequest(req))),
  startJob: async (req) =>
    adaptJob(await post<BackendJobRecord>('/jobs', backendJobRequest(req))),

  getApprovals: (sid) => get<ApprovalDoc>(n(sid) + '/approvals'),
  approveGate: (sid, key: GateKey) => post<ApprovalDoc>(n(sid) + '/approvals/gate/' + key),
  markSeen: (sid, key: SeenKey) => post<ApprovalDoc>(n(sid) + '/approvals/seen/' + key),
  setDeep: (sid, deep) => post<ApprovalDoc>(n(sid) + '/approvals/deep', { deep }),
  startWriting: (sid) => post<ApprovalDoc>(n(sid) + '/approvals/writing'),
  resetBook: (sid) => post<ApprovalDoc>(n(sid) + '/approvals/reset'),

  saveDraftEdit: (sid, o, text) => post<{ ok: true }>(n(sid) + '/review/' + o + '/edit', { text }),
  approveHook: (sid, index, text) =>
    post<{ ok: true }>(n(sid) + '/stage6/approve', { index, text }),
  approveNames: (sid) => post<{ ok: true }>(n(sid) + '/stage7/approve'),
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
