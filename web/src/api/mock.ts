/**
 * 假后端。
 *
 * 这是**整个前端里唯一 import src/mocks/*.json 与 src/fixtures/**\/*.json 的文件**，
 * 也是唯一碰 localStorage 的文件。它不是「fixture 加载器」，是一个真的会写盘
 * （写 localStorage）、会跑作业、会拒绝「这一份不在样本里」的服务端替身 ——
 * 所以屏与组件在 mock 模式下走的代码路径和接了真后端之后**完全一样**。
 *
 * 三件事故意做在这里而不是渲染层：
 *   1. 历史样例与合成候选根的范围 —— 由假后端分别接入，不拼接身份
 *   2. 命令行的拼装 —— U-09 要求确认框里那条命令由服务端下发
 *   3. 花不花钱、会不会写盘 —— 前端一行判定都不许有
 *
 * ⚠ STR-06 切换（F8c）：旧链读屏与人工闸的 mock 面整族退役（plot_v1 剧情链
 * 夹具、stage5/review/inbox/stage7 抓包、审批 localStorage 账全部删除）；
 * 现役样本只剩语料库总览、Stage0/1 读面、作业与 agent 代跑，以及 STR-06
 * 候选链监控（fixtures/backend_v1/str06_candidate.json）。
 */
import type { Api } from '@/types/api'
import type {
  AgentCapabilitiesResponse,
  AgentProfileSettingsView,
  NetworkSettingsView,
  AgentQueueView,
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunRecord,
  AgentRunRequest,
  AgentRunsResponse,
} from '@/types/agent'
import type { EnumsResponse } from '@/types/enums'
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
  Job,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
  UploadCorpusOptions,
  UploadResponse,
} from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { RunProgress } from '@/types/run'
import type {
  CheckKind,
  Stage0Extras,
  Stage0Report,
  Stage0Segments,
  Stage1View,
} from '@/types/stages'
import { lookupAction } from '@/constants/actions'
import { ApiError } from './errors'

/* ------------------------------------------------------------ 样本的边界 */

/** 样本抓自这一本 8 章的书 */
export const SAMPLE_SID = '7615069877019300633'

/* ------------------------------------------ 冻结原样（只加载仍消费的三份历史输入） */

const RAW = import.meta.glob(
  [
    '../mocks/novels.json',
    '../mocks/novel_overview.json',
    '../mocks/stage0_extras.json',
  ],
  { eager: true, import: 'default' },
) as Record<string, unknown>

function raw<T>(file: string): T {
  const hit = RAW['../mocks/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/mock/' + file)
  // 一次性穿过手写类型。Vite 会把 JSON 推成巨型字面量类型，
  // 让它进类型系统会把 tsc 拖垮，也会让 types/ 失去「契约」的意义。
  return hit as T
}

type LegacyArtifact = Omit<Artifact, 'display_freshness' | 'condition'>
type LegacyNovel = Omit<
  NovelOverview,
  'stages' | 'stage_done' | 'stage_reviewable' | 'chapters_done' | 'summary'
> & {
  stages: Record<string, LegacyArtifact[]>
  stage_done?: Record<string, boolean | null>
  chapters_done?: Record<string, number[] | Record<string, number[]>>
  /** 只用于解码冻结 mock；绝不出现在当前 NovelOverview。 */
  stage5_modes?: string[]
}

const PIPELINE_STAGES = [
  'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage7',
]

/** 冻结 mock 的旧业务推导只准留在这个假后端；屏幕与真实数据缝不兼容旧形状。 */
function currentMockNovel(legacy: LegacyNovel): NovelOverview {
  const stages: Record<string, Artifact[]> = {}
  const stageDone: Record<string, boolean | null> = {}
  const stageReviewable: Record<string, boolean> = {}
  const legacyModes = (legacy.stage5_modes ?? []).filter((mode) => !mode.startsWith('_'))

  for (const stage of PIPELINE_STAGES) {
    const artifacts = (legacy.stages[stage] ?? []).map<Artifact>((artifact) => ({
      ...artifact,
      display_freshness: artifact.exists ? artifact.freshness : 'missing',
      condition: artifact.invalid
        ? 'invalid'
        : !artifact.exists
          ? 'missing'
          : artifact.readable === false
            ? 'unreadable'
            : 'ready',
    }))
    stages[stage] = artifacts
    const declared = legacy.stage_done?.[stage]
    const done =
      declared !== undefined
        ? declared
        : stage === 'stage5'
          ? false
          : artifacts.length
            ? artifacts.every(
                (artifact) =>
                  artifact.condition === 'ready' && artifact.display_freshness !== 'stale',
              )
            : false
    stageDone[stage] = done
    stageReviewable[stage] =
      done === true ||
      (stage === 'stage5'
        ? legacyModes.length > 0
        : artifacts.some((artifact) => artifact.exists && artifact.readable !== false))
  }

  const all = Object.values(stages).flat()
  const broken = all.filter(
    (artifact) => artifact.condition === 'invalid' || artifact.condition === 'unreadable',
  )
  const stale = all.filter((artifact) => artifact.display_freshness === 'stale')
  const unknown = all.filter(
    (artifact) => artifact.exists && artifact.display_freshness === 'unknown',
  )
  const missing = all.filter((artifact) => artifact.display_freshness === 'missing')
  const pending = PIPELINE_STAGES.find((stage) => stageDone[stage] === false)
  const unknownStages = PIPELINE_STAGES.filter((stage) => stageDone[stage] === null)
  const existing = all.filter((artifact) => artifact.exists)

  let verdict: NovelOverview['summary']['verdict']
  let text: string
  let detail: string
  if (broken.length) {
    ;[verdict, text, detail] = [
      'broken',
      broken.length + ' 份产物不可用',
      '盘上存在，但当前版本读不回来或已被隔离',
    ]
  } else if (legacyModes.length) {
    ;[verdict, text, detail] = [
      'legacy_incompatible',
      '发现旧 Stage5 契约产物',
      'deep/shallow 只读保留，不会迁移或自动付费重跑',
    ]
  } else if (stale.length) {
    ;[verdict, text, detail] = ['stale', stale.length + ' 份过期', '上游变了，下游要重跑']
  } else if (pending) {
    ;[verdict, text, detail] = ['pending', '待继续', '下一步：' + pending]
  } else if (unknown.length) {
    ;[verdict, text, detail] = [
      'unknown',
      unknown.length + ' 份无从判断',
      missing.length ? missing.length + ' 份尚未落盘' : '其余产物新鲜',
    ]
  } else if (unknownStages.length) {
    ;[verdict, text, detail] = [
      'unknown',
      unknownStages.length + ' 层完成度判不了',
      '不是没跑，需要打开书逐层确认',
    ]
  } else {
    ;[verdict, text, detail] = [
      'complete',
      '全链已有产物',
      existing.length + ' 份产物可读',
    ]
  }

  const chaptersDone = Object.fromEntries(
    PIPELINE_STAGES.map((stage) => {
      const value = legacy.chapters_done?.[stage]
      return [stage, Array.isArray(value) ? value : []]
    }),
  ) as Record<string, number[]>

  const {
    stage5_modes: _retiredStage5Modes,
    chapters_done: _legacyChaptersDone,
    ...current
  } = legacy
  void _retiredStage5Modes
  void _legacyChaptersDone

  return {
    ...current,
    stages,
    stage_done: stageDone,
    stage_reviewable: stageReviewable,
    chapters_done: chaptersDone,
    summary: {
      verdict,
      text,
      detail,
      artifact_count: existing.length,
      latest_mtime: Math.max(0, ...existing.map((artifact) => artifact.mtime ?? 0)) || null,
    },
  }
}

function mockNovels(): NovelsResponse {
  const legacy = raw<{ base: string; novels: LegacyNovel[] }>('novels.json')
  return { base: legacy.base, novels: legacy.novels.map(currentMockNovel) }
}

/* ------------------------------------------------- backend_v1（现役数据源） */

/**
 * 新后端真实响应的 fixture（FE-06 的「有版本含义的目录」）。
 * `src/mocks/` 那 7 份是**冻结的证据基线**，从此不再被新形状的端点消费；
 * mock 现役读的是这一批 —— 与真后端同一形状，屏幕在两种模式下走同一条代码路径。
 */
const RAW_V1 = import.meta.glob([
  '../fixtures/backend_v1/*.json',
], {
  eager: true,
  import: 'default',
}) as Record<string, unknown>

function fx<T>(file: string): T {
  const hit = RAW_V1['../fixtures/backend_v1/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/fixtures/' + file)
  return hit as T
}

/* --------------------------------------------- STR-06 候选链监控 fixture */

/** 录制的合成候选根（fake transport 全链产物，非真实书正文；与真后端同形状）。 */
interface Str06Fixture {
  candidates: Str06CandidateList
  run: Str06RunView
  characters: Str06CharactersView
  blueprint: Str06BlueprintView
  design: Str06DesignView
  ir: Str06IrNavView
  chapter1: Str06ChapterView
  hook: Str06HookView
  naming: Str06NamingView
  delivery: Str06DeliveryView
  book: string
}

/** 样本候选根名：与 fixture 的 candidates[0].candidate 对账（写死两份必然分家）。 */
const STR06_FIXTURE_CANDIDATE = 'cand'
/** 样本只录了第 1 章；别的章号答 nosample，不编一章空数据。 */
const STR06_FIXTURE_CHAPTER = 1

function str06Fixture(): Str06Fixture {
  return fx<Str06Fixture>('str06_candidate.json')
}

function str06Read<K extends keyof Omit<Str06Fixture, 'book'>>(
  candidate: string, key: K,
): Promise<Str06Fixture[K]> {
  if (candidate !== STR06_FIXTURE_CANDIDATE) {
    return noBook('/str06/candidates/' + candidate + '/' + key)
  }
  return later(structuredClone(str06Fixture()[key]))
}

function str06ReadBook(candidate: string): string {
  if (candidate !== STR06_FIXTURE_CANDIDATE) {
    throw new ApiError('nosample', '/str06/candidates/' + candidate + '/book')
  }
  return str06Fixture().book
}

/* ---------------------------------------------------------------- 延迟 */

const LATENCY = 120

function later<T>(v: T): Promise<T> {
  return new Promise((res) => setTimeout(() => res(v), LATENCY))
}

function fail(err: ApiError): Promise<never> {
  return new Promise((_res, rej) => setTimeout(() => rej(err), LATENCY))
}

/** 这本书不在样本里 */
function noBook(path: string): Promise<never> {
  return fail(new ApiError('nosample', path, { scope: 'book' }))
}

/** 这一章不在样本里 */
function noChapter(path: string): Promise<never> {
  return fail(new ApiError('nosample', path, { scope: 'chapter' }))
}

function bookOnly<T>(sid: string, path: string, load: () => T): Promise<T> {
  if (sid !== SAMPLE_SID) return noBook(path)
  return later(load())
}

/* ------------------------------------------------------------ 命令行拼装 */

/** 枚举取值直接透传当前后端生成的样例；仅补历史显示辅助字段。 */
function enums(): EnumsResponse {
  return fx<EnumsResponse>('enums.json')
}

/** 花不花钱由服务端的动作表说了算。这里是「假服务端」，所以查的是同一张表。 */
function paidOf(req: JobRequest): boolean {
  const hit = lookupAction(enums(), req)
  return hit ? hit.paid : false
}

function argvOf(req: JobRequest): string {
  const parts = ['python', '-m', 'novel.cli', req.kind, '--sid', req.sid]
  if (req.extra) parts.push(...req.extra)
  if (req.args) parts.push(...req.args)
  return parts.join(' ')
}

function previewRequest(req: JobRequest): JobRequest {
  const action = lookupAction(enums(), req)
  const inputs = req.required_inputs ?? action?.required_inputs ?? []
  const present = [...(req.extra ?? []), ...(req.args ?? [])]
  const placeholders = inputs
    .filter((input) => !present.includes(input.flag))
    .flatMap((input) => [input.flag, '待填·' + input.flag.replace(/^--/, '')])
  return placeholders.length
    ? { ...req, args: [...(req.args ?? []), ...placeholders] }
    : req
}

function mockJobPreview(req: JobRequest): JobPreview {
  const planned = previewRequest(req)
  return {
    argv: argvOf(planned),
    paid: paidOf(req),
    label: req.label,
    kind: req.kind,
    executor_backend: null,
    egress_authorized: null,
    steps: previewStepsOf(planned),
  }
}

function previewStepsOf(req: JobRequest): JobPreview['steps'] {
  const first = {
    label: req.label,
    paid: paidOf(req),
    writes: 'mock 产物',
    argv: argvOf(req),
    executor_backend: null,
    egress_authorized: null,
  }
  const checkArgv = (kind: string) =>
    ['python', '-m', 'novel.cli', kind, '--sid', req.sid, '--check-only'].join(' ')
  // 新链每个 str06 节点自带 --check-only 免费复检；预览把这条尾巴显示出来，
  // 让「同一请求的 0 成本复检」在确认框里就看得见。
  if (req.kind.startsWith('str06.')) {
    return [
      first,
      {
        label: req.kind + ' · 节点级复检',
        paid: false,
        writes: '',
        argv: checkArgv(req.kind),
        executor_backend: null,
        egress_authorized: null,
      },
    ]
  }
  return [first]
}

/* ------------------------------------------------------- localStorage 层 */

const LS_PREFIX = 'nrc.v1.'

function lsRead<T>(key: string): T | null {
  try {
    const s = localStorage.getItem(LS_PREFIX + key)
    return s ? (JSON.parse(s) as T) : null
  } catch {
    return null
  }
}

function lsWrite(key: string, v: unknown): void {
  try {
    localStorage.setItem(LS_PREFIX + key, JSON.stringify(v))
  } catch {
    /* 隐私模式下写不进去也不该崩 */
  }
}

/* -------------------------------------------------------------- 作业引擎 */

interface MockJob extends Job {
  log: string[]
}

let JOBS: MockJob[] = []
let SEQ = 0

/**
 * 服务重启后原本 running 的一律改判 unknown —— 不是 ok。
 * 页面刷新就是这里的「重启」：计时器没了，那一轮到底跑完没有，我们真的不知道。
 */
function bootJobs(): void {
  const saved = lsRead<MockJob[]>('jobs') ?? []
  JOBS = saved.map((j) =>
    j.state === 'running'
      ? {
          ...j,
          state: 'unknown' as const,
          log: [...j.log, '[--:--] 页面重载，这一轮的结局无从判断——不改判成 ok。'],
        }
      : j,
  )
  SEQ = JOBS.length
  lsWrite('jobs', JOBS)
}

bootJobs()

function persistJobs(): void {
  lsWrite('jobs', JOBS.slice(-20))
}

const STEPS: { at: number; line: string }[] = [
  { at: 1500, line: '[00:01] 载入上游产物 …' },
  { at: 3000, line: '[00:03] 正在跑 …' },
  { at: 4500, line: '[00:04] 写盘 …' },
  { at: 6000, line: '[00:06] 完成' },
]

function spawn(req: JobRequest, label?: string): Job {
  SEQ += 1
  const argv = argvOf(req)
  const job: MockJob = {
    id: 'job_' + SEQ + '_' + Date.now().toString(36),
    kind: req.kind,
    label: label ?? req.label,
    argv,
    state: 'running',
    paid: paidOf(req),
    started: Math.floor(Date.now() / 1000),
    finished: null,
    sid: req.sid,
    ordinal: req.ordinal ?? null,
    executor_backend: null,
    egress_authorized: null,
    log: ['$ ' + argv],
  }
  JOBS = [...JOBS, job]
  persistJobs()

  STEPS.forEach((s) => {
    setTimeout(() => {
      const cur = JOBS.find((j) => j.id === job.id)
      if (!cur || cur.state !== 'running') return
      cur.log = [...cur.log, s.line]
      if (s.line.includes('完成')) {
        cur.state = 'ok'
        cur.finished = Math.floor(Date.now() / 1000)
      }
      JOBS = [...JOBS]
      persistJobs()
    }, s.at)
  })

  return stripLog(job)
}

/** Stage 0–1 的真实接口是同步子进程；mock 也返回已经结束的作业，页面才能走同一条链。 */
function finish(req: JobRequest, label?: string): Job {
  const started = spawn(req, label)
  const stored = JOBS.find((job) => job.id === started.id)
  if (!stored) return started
  stored.state = 'ok'
  stored.finished = Math.floor(Date.now() / 1000)
  stored.log = [...stored.log, '[mock] 完成']
  JOBS = [...JOBS]
  persistJobs()
  return stripLog(stored)
}

/** 日志不进 /api/jobs 的响应，它走 /jobs/:id/log */
function stripLog(j: MockJob): Job {
  return {
    id: j.id,
    kind: j.kind,
    label: j.label,
    argv: j.argv,
    state: j.state,
    paid: j.paid,
    started: j.started,
    finished: j.finished,
    sid: j.sid,
    ordinal: j.ordinal,
    executor_backend: j.executor_backend ?? null,
    egress_authorized: j.egress_authorized ?? null,
  }
}

function publicJobs(sid: string): JobsResponse {
  const mine = JOBS.filter((j) => j.sid === sid).map(stripLog)
  return { jobs: mine, running: mine.find((j) => j.state === 'running') ?? null }
}

function codePoints(text: string): number {
  return [...text].length
}

function mockStage0Segments(_sid: string): Stage0Segments {
  const data = raw<Stage0Extras>('stage0_extras.json')
  const pieces = [
    ...data.blocks.map((block, index) => ({
      id: 'keep-' + index,
      text: block.label + '\n' + block.preview + '\n\n',
      machine: false,
      marker: block.label,
      removedKind: 'extra',
    })),
    ...data.candidates.map((candidate, index) => ({
      id: 'remove-' + index,
      text: candidate.marker + '\n' + candidate.head + '\n' + candidate.tail + '\n\n',
      machine: true,
      marker: candidate.marker,
      removedKind: candidate.kind,
    })),
  ]
  let offset = 0
  const segments = pieces.map((piece) => {
    // stage0 人工选择已随 2026-09-15 全自动裁决退役：mock 只呈现机器结论。
    const initial = piece.machine
    const chars = codePoints(piece.text)
    const start = offset
    offset += chars
    return {
      id: piece.id,
      kind: initial ? 'remove' as const : 'keep' as const,
      start,
      end: offset,
      chars,
      flagged: initial,
      text: piece.text,
      origin: initial ? (piece.machine ? 'machine' as const : 'human' as const) : null,
      removed_kind: initial ? piece.removedKind : null,
      marker_line: initial ? piece.marker : null,
    }
  })
  return { available: true, reason: null, segments }
}

function mockStage0Report(sid: string): Stage0Report {
  const data = raw<Stage0Extras>('stage0_extras.json')
  // 样本书的 stage0 尚未跑过：executable 只投影后端机械判定，mock 不代它放行。
  void sid
  return {
    original_filename: data.original_filename,
    total_code_points: data.total_code_points,
    warnings: [],
    executable: false,
    revision_id: 'a'.repeat(64),
    revision_origin: 'machine' as const,
  }
}

function mockStage1View(): Stage1View {
  return fx<Stage1View>('stage1.json')
}

/* ---------------------------------------------------------- 跑书进度样例 */

/** mock 的静态镜像（CS-3 bind 退役后 13 个 `str06.` 节点，B5 对齐）；
 *  真实响应由后端直接从 NODES 派生。label/paid 与 `contracts/invariants.py::NODES` 逐字对账，
 *  夹具与 mock 的 node id 由 `tests/test_retired_surface_scan.py` 钉到 NODES。 */
const PIPELINE_STEPS: { node: string; label: string; paid: boolean }[] = [
  { node: 'stage0.remove', label: 'stage0 非正文剔除', paid: false },
  { node: 'stage1.partition', label: 'stage1 分章', paid: false },
  { node: 'str06.stage2.book', label: 'Stage2 书级观察（人物候选、称谓及证据、初态、世界约束）', paid: true },
  { node: 'str06.stage2.chapters', label: 'Stage2 逐章观察（事件与三类转移；互不依赖，D3）', paid: true },
  { node: 'str06.stage2.names', label: 'Stage2 源名称结构观察（家族、名、化名、称谓构词）', paid: true },
  { node: 'str06.stage2.associate', label: 'Stage2 跨章关联（同一命题、同维状态、悬念配对、跨章因果与续场，D8）', paid: true },
  { node: 'str06.stage2.compile', label: 'Stage2 确定性编译：解析证据、生成 ID、执行关联，发布 Characters＋Blueprint（D9）', paid: false },
  { node: 'str06.stage4.preserve', label: 'Stage4 Preserve（保存策略义务）与 ReconstructionBrief 投影', paid: true },
  { node: 'str06.stage4.body_design', label: 'Stage4 目标故事与导演说明设计（BodyDesign）', paid: true },
  { node: 'str06.stage4.hook_design', label: 'Stage4 Hook 设计并经 publish_graph 唯一发布 PresentationGraph', paid: true },
  { node: 'str06.stage4.compile', label: 'Stage4 确定性编译：ExecutionPlan／Guard／WriterBookIR／HookContext', paid: false },
  { node: 'str06.stage5.hook', label: 'Stage5 Hook 成文（同 Context 有限重写＋机械验）', paid: true },
  { node: 'str06.stage5.body', label: 'Stage5 按章正文成文（机械检查＋有限重写＋章级回执）', paid: true },
  { node: 'str06.stage7.naming', label: 'Stage7 Naming（NameMap v5 赋值；Apply 归 assembly）', paid: true },
  { node: 'str06.stage7.assemble', label: 'Stage7 改名替换与成书装配（TXT 先落盘、manifest 最后提交）', paid: false },
]

/** 静态样例：全节点 pending + 空事件流 + 全 null 的 stage_done。 */
function mockRunProgress(sid: string): RunProgress {
  return {
    sid,
    resume_frontier: {
      state: 'ready',
      node: 'stage0.remove',
      seq: 1,
      label: 'stage0 非正文剔除',
      resume_verdict: 'run',
      why: 'mock：stage0 产物尚未形成',
      waiting_on: null,
      paid: false,
      writes: 'artifact',
      next_handler: 'code',
      review_states: null,
      verified_skipped_nodes: [],
      verified_skipped_count: 0,
    },
    steps: PIPELINE_STEPS.map((s, i) => ({
      seq: i + 1,
      node: s.node,
      label: s.label,
      paid: s.paid,
      beacon: 'pending',
      last_exit: null,
      last_ts: null,
      elapsed: null,
    })),
    stage_done: {
      stage0: null,
      stage1: null,
      stage2: null,
      stage4: null,
      stage5: null,
      stage7: null,
    },
    events_tail: [],
  }
}

/* ---------------------------------------------------------- agent 代跑样例 */

/** 后端 preview 与真跑同一构造函数；Commander 退役后 argv / prompt 恒为 null
 *  （schema 兼容保留），mock 忠实镜像，不得替真实形状伪造非空值。 */
function mockAgentRunPreview(req: AgentRunRequest): AgentRunPreview {
  return {
    pause_before_node: req.pause_before_node ?? null,
    argv: null,
    prompt: null,
    authorized: req.authorized,
    egress_authorized: req.egress_authorized,
    profiles: req.profiles ?? null,
  }
}

function mockAgentRunRecord(req: AgentRunRequest): AgentRunRecord {
  const preview = mockAgentRunPreview(req)
  const start = {
    ...(preview.pause_before_node ? { pause_before_node: preview.pause_before_node } : {}),
    sid: req.sid,
    base: '/mock/base',
    argv: preview.argv,
    prompt: preview.prompt,
    authorized: req.authorized,
    egress_authorized: req.egress_authorized,
    code_version: 'mock-code-version',
    skill_sha256: 'a'.repeat(64),
    profiles: preview.profiles,
  }
  return {
    run_id: 'agentrun_mock',
    start,
    contract: {
      wire_version: 2,
      ...(preview.pause_before_node ? { pause_before_node: preview.pause_before_node } : {}),
      sid: start.sid,
      base: start.base,
      authorized: start.authorized,
      egress_authorized: start.egress_authorized,
      profiles: req.profiles,
    },
    epochs: [],
    active_epoch_id: null,
    started: Math.floor(Date.now() / 1000),
    ended: null,
    exit_code: null,
    state: 'running',
    note: null,
    outcome: null,
    allowed_controls: ['pause', 'stop'],
  }
}

let mockNetworkSettingsSequence = 0
let mockNetworkSettings: NetworkSettingsView = {
  state: 'unset',
  network: null,
  revision: '0'.repeat(64),
  updated_at: null,
  issue: null,
}

let mockAgentSettingsSequence = 0
let mockAgentProfileSettings: AgentProfileSettingsView = {
  state: 'unset',
  profiles: null,
  revision: '0'.repeat(64),
  updated_at: null,
  issue: null,
}

function cloneMockProfiles(profiles: AgentProfileSettingsView['profiles']) {
  if (profiles === null) return null
  const cloneBackend = (backend: typeof profiles.executor_default) =>
    ({ kind: 'legacy' as const, profile: { ...backend.profile } })
  return {
    schema_version: 4 as const,
    executor_default: cloneBackend(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        cloneBackend(backend),
      ]),
    ),
  }
}

function mockControlledAgentRun(runId: string, state: 'paused' | 'unknown'): AgentRunRecord {
  const profiles = fx<AgentCapabilitiesResponse>('agent_capabilities.json').default_profiles
  if (profiles === null) throw new Error('mock agent capability 必须提供 default_profiles')
  const record = mockAgentRunRecord({
    sid: SAMPLE_SID,
    authorized: false,
    egress_authorized: true,
    profiles,
  })
  return {
    ...record,
    run_id: runId,
    state,
    note: state === 'unknown' ? 'mock：熄火后结局不明，不改判 ok' : 'mock：安全暂停',
    allowed_controls: state === 'paused' ? ['resume', 'stop'] : [],
    ended: state === 'unknown' ? Math.floor(Date.now() / 1000) : null,
  }
}

/* ------------------------------------------------------------------ Api */

export const mockApi: Api = {
  getNovels: async () => mockNovels(),

  getNovel: (sid) =>
    bookOnly(sid, `/novels/${sid}`, () =>
      currentMockNovel(raw<LegacyNovel>('novel_overview.json')),
    ),

  // 进度屏在 stage0.remove 前就要能开（书目录可能还没建）——任何 sid 都给静态样例，不走 bookOnly
  getRunProgress: (sid) => later(mockRunProgress(sid)),
  getRunMetrics: (sid, runId) => runId ? noBook(`/novels/${sid}/run/metrics?run_id=${runId}`)
    : noBook(`/novels/${sid}/run/metrics`),
  previewWorkUnitGrant: async () => { throw new Error('离线样本未提供工作单元额度授权，请连接真实服务') },
  grantWorkUnit: async () => { throw new Error('离线样本未提供工作单元额度授权，请连接真实服务') },

  getEnums: () => later(enums()),

  getStage0Report: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/report`, () => mockStage0Report(sid)),

  getStage0Segments: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/segments`, () => mockStage0Segments(sid)),

  // stage0 人工写面与手动 partition 已退役；mock 只提供只读展示。

  getStage1: (sid) => bookOnly(sid, `/novels/${sid}/stage1`, mockStage1View),

  // F8c：层级复检取值面只剩 stage0/stage1，样本书上两层都没跑过（真后端答 404）
  // —— null = 「还没跑」，不是「通过」。
  getCheck: (sid, kind: CheckKind) => {
    const path = `/novels/${sid}/check/${kind}`
    if (sid !== SAMPLE_SID) return noBook(path)
    return later(null)
  },

  getJobs: (sid) => later(publicJobs(sid)),

  getJobLog: (id, offset) => {
    const j = JOBS.find((x) => x.id === id)
    if (!j) return fail(new ApiError('notfound', '/jobs/' + id + '/log'))
    const lines = j.log.slice(offset).map((text) => ({
      text,
      raw: null,
      details: null,
      kind: 'plain' as const,
    }))
    return later<JobLogResponse>({
      lines,
      offset: j.log.length,
      done: j.state !== 'running',
    })
  },

  streamJobLog: (id, offset, sink) => {
    let closed = false
    let cursor = offset
    let timer: ReturnType<typeof setTimeout> | undefined
    const encoder = new TextEncoder()
    const decoder = new TextDecoder()
    const pump = () => {
      if (closed) return
      const j = JOBS.find((x) => x.id === id)
      if (!j) {
        sink.onError(new ApiError('notfound', '/jobs/' + id + '/stream'))
        return
      }
      const bytes = encoder.encode(j.log.length ? j.log.join('\n') + '\n' : '')
      const text = decoder.decode(bytes.slice(cursor))
      cursor = bytes.length
      sink.onEvent({
        text,
        next_byte: cursor,
        done: j.state !== 'running',
        state: j.state === 'running' ? null : j.state,
      })
      if (!closed && j.state === 'running') timer = setTimeout(pump, 80)
    }
    timer = setTimeout(pump, 0)
    return () => {
      closed = true
      if (timer !== undefined) clearTimeout(timer)
    }
  },

  previewJob: (req) => later(mockJobPreview(req)),

  startJob: (req) => later(spawn(req)),

  /* agent 代跑：静态样例 —— 列表恒空（mock 不养 agent 进程），start 回一条 running 记录 */
  getAgentCapabilities: () => later(fx<AgentCapabilitiesResponse>('agent_capabilities.json')),
  getAgentProfileSettings: () => later({
    ...mockAgentProfileSettings,
    profiles: cloneMockProfiles(mockAgentProfileSettings.profiles),
  }),
  saveAgentProfileSettings: (req) => {
    if (req.expected_revision !== mockAgentProfileSettings.revision) {
      return fail(new ApiError('server', '/settings/agent-profiles', {
        status: 409,
        backendKind: 'AgentProfileSettingsConflict',
        message: '常用 Agent 配置已被另一个页面更新；请重读后再保存',
      }))
    }
    mockAgentSettingsSequence += 1
    mockAgentProfileSettings = {
      state: req.profiles === null ? 'unset' : 'configured',
      profiles: cloneMockProfiles(req.profiles),
      revision: mockAgentSettingsSequence.toString(16).padStart(64, '0'),
      updated_at: Date.now() / 1000,
      issue: null,
    }
    return later({
      ...mockAgentProfileSettings,
      profiles: cloneMockProfiles(mockAgentProfileSettings.profiles),
    })
  },
  getNetworkSettings: () => later({
    ...mockNetworkSettings,
    network: mockNetworkSettings.network === null
      ? null
      : { ...mockNetworkSettings.network },
  }),
  saveNetworkSettings: (req) => {
    if (req.expected_revision !== mockNetworkSettings.revision) {
      return fail(new ApiError('server', '/settings/network', {
        status: 409,
        backendKind: 'NetworkSettingsConflict',
        message: '网络偏好已被另一个页面更新；请重读后再保存',
      }))
    }
    mockNetworkSettingsSequence += 1
    mockNetworkSettings = {
      state: req.network === null ? 'unset' : 'configured',
      network: req.network === null ? null : { ...req.network },
      revision: mockNetworkSettingsSequence.toString(16).padStart(64, '0'),
      updated_at: Date.now() / 1000,
      issue: null,
    }
    return later({
      ...mockNetworkSettings,
      network: mockNetworkSettings.network === null
        ? null
        : { ...mockNetworkSettings.network },
    })
  },
  getAgentRunContinuation: async () => { throw new Error('离线样本没有历史运行停止证明；请连接真实服务。') },
  previewAgentRun: (req) => later(mockAgentRunPreview(req)),
  startAgentRun: (req) => later(mockAgentRunRecord(req)),
  getAgentRuns: () => later<AgentRunsResponse>({ runs: [] }),
  getAgentRunLog: () => later<AgentRunLogChunk>({ text: '', next_byte: 0 }),
  stopAgentRun: (runId) =>
    later<AgentRunRecord>(mockControlledAgentRun(runId, 'unknown')),
  pauseAgentRun: (runId) =>
    later<AgentRunRecord>(mockControlledAgentRun(runId, 'paused')),
  resumeAgentRun: (runId) =>
    later<AgentRunRecord>({
      ...mockControlledAgentRun(runId, 'paused'),
      state: 'running',
      note: 'mock：沿同一 execution epoch 精确续会',
      allowed_controls: ['pause', 'stop'],
    }),

  /* 批量：闸写死 3（真实模式由服务端下发）。前 3 本算在跑，其余按提交顺序排队。
     ⚠ mock 不养进程，所以这里只是**形状**正确的一份样例——它让批量入口的空/满/
     排队三态在离线开发时都渲染得出来，不假装真的起了 agent。 */
  startAgentBatch: (req) =>
    later<AgentQueueView>({
      running: req.sids.slice(0, 3).map((sid) =>
        mockAgentRunRecord({
          sid,
          authorized: req.authorized,
          egress_authorized: req.egress_authorized,
          profiles: req.profiles,
        }),
      ),
      pending: req.sids.slice(3).map((sid, i) => ({
        sid,
        position: i + 1,
        profiles: cloneMockProfiles(req.profiles)!,
      })),
      rejected: [],
      max_concurrent: 3,
    }),
  getAgentQueue: () =>
    later<AgentQueueView>({ running: [], pending: [], rejected: [], max_concurrent: 3 }),

  uploadCorpus: (file, options: UploadCorpusOptions): Promise<UploadResponse> => {
    // mock 里只有这一本真书。上传等于把它重置回第 0 步；授权只回显，不真开跑。
    void options.profiles
    const job = finish(
      {
        kind: 'stage0.remove',
        label: 'stage0 非正文剔除',
        extra: ['--source', 'corpus/' + file.name],
        sid: SAMPLE_SID,
        ordinal: null,
      },
      'stage0 番外扫描',
    )
    return later({
      sid: SAMPLE_SID,
      job,
      run: null,
      // mock 不真开跑：授权路径回显 started，未授权保持 null。
      autostart:
        options.authorized && options.egressAuthorized ? 'started' : null,
      idempotency_key: options.idempotencyKey,
    })
  },

  // 冻结样本不提供可恢复的目录移动，明确答 nosample，不伪装已经删除。
  trashNovel: (sid) => noBook('/novels/' + sid + '/trash'),
  // service 生命周期维护只属于真实本地服务；mock 不伪造 preview token 或删除结果。
  previewRetention: () => noBook('/maintenance/retention/preview'),
  applyRetention: () => noBook('/maintenance/retention/apply'),
  previewTrashRestore: () => noBook('/maintenance/trash/restore/preview'),
  applyTrashRestore: () => noBook('/maintenance/trash/restore/apply'),

  /* STR-06 候选链只读监控：录制的合成候选根响应，真实后端同形状。
     只读——mock 不伪造任何候选链写动作（那属于 CLI 子进程，不属于服务）。 */
  getStr06Candidates: () => later(str06Fixture().candidates),
  getStr06Run: (candidate) => str06Read(candidate, 'run'),
  getStr06Characters: (candidate) => str06Read(candidate, 'characters'),
  getStr06Blueprint: (candidate) => str06Read(candidate, 'blueprint'),
  getStr06Design: (candidate) => str06Read(candidate, 'design'),
  getStr06Ir: (candidate) => str06Read(candidate, 'ir'),
  getStr06Chapter: (candidate, o) =>
    o === STR06_FIXTURE_CHAPTER ? str06Read(candidate, 'chapter1')
      : noChapter('/str06/candidates/' + candidate + '/chapters/' + o),
  getStr06Hook: (candidate) => str06Read(candidate, 'hook'),
  getStr06Naming: (candidate) => str06Read(candidate, 'naming'),
  getStr06Delivery: (candidate) => str06Read(candidate, 'delivery'),
  getStr06Book: (candidate) => later(str06ReadBook(candidate)),
}
