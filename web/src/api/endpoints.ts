/**
 * 唯一的数据缝。屏与组件只认这个文件导出的 `api`。
 *
 * 接后端时要做的事就是把 VITE_USE_MOCK 关掉。
 * 如果哪个组件自己造了数据，那一屏就会当场空掉 —— 而这正是联调时最不想找的一类 bug。
 */
import type { Api } from '@/types/api'
import type { ApprovalDoc, GateKey, SeenKey } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type { Job, JobLogResponse, JobPreview, JobsResponse } from '@/types/jobs'
import type { NovelOverview, NovelsResponse } from '@/types/novels'
import type {
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
} from '@/types/stages'
import { get, post } from './client'
import { mockApi } from './mock'

const n = (sid: string) => '/novels/' + encodeURIComponent(sid)

/**
 * 真实 HTTP 实现。路由表来自 brief/04_delivery.md；
 * stage0 与 approvals 两组是设计稿新增的屏，后端还没有对应 endpoint
 * —— 已在 DELIVERY.md 里写明形状建议，没有自己改后端。
 */
export const realApi: Api = {
  getNovels: () => get<NovelsResponse>('/novels'),
  getNovel: (sid) => get<NovelOverview>(n(sid)),
  getEnums: () => get<EnumsResponse>('/enums'),

  getStage0: (sid) => get<Stage0Extras>(n(sid) + '/stage0/extras'),
  getStage1Report: (sid) => get<Stage1Report>(n(sid) + '/stage1/report'),
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
  getCheck: (sid, kind) => get<CheckReport | null>(n(sid) + '/check/' + kind),

  // 真后端上每一章都有数据，没有「样本边界」这回事
  getSampleCoverage: () => Promise.resolve(null),

  getJobs: (sid) => get<JobsResponse>('/jobs?sid=' + encodeURIComponent(sid)),
  getJobLog: (id, offset) => get<JobLogResponse>('/jobs/' + id + '/log?offset=' + offset),
  previewJob: (req) => post<JobPreview>('/jobs/preview', req),
  startJob: (req) => post<Job>('/jobs', req),

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
  uploadCorpus: (file) => post<{ sid: string }>('/upload', file),
}

/** 编译期常量：VITE_USE_MOCK 会被 Vite 替换成字面量，于是这个三元能被静态折叠 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = USE_MOCK ? mockApi : realApi
