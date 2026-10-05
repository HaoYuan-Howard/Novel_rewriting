/**
 * Stage0 / Stage1 与校验报告的响应形状。字段名照抄后端契约
 * （`contracts/schema/api.py` 与 `contracts/schema/report.py`），
 * 抓包证据在 `src/fixtures/backend_v1/` 与 `src/mocks/`。
 *
 * ⚠ STR-06 切换（F8c）后旧链 Stage2/4/5/7 的读屏响应类型整体退役：
 * `GET /check/:kind` 的取值面只剩 stage0/stage1（新链 Stage2/4/5/7 的复检是
 * 节点级 `--check-only`，没有层级 CheckReport），候选链只读监控面的形状归
 * `types/str06.ts`。本文件不再为已退役的层保留第二个类型落点。
 */
import type { Badge, Freshness } from '@/constants/vocabulary'
import type { RuleLine } from './common'

/* ---------------------------------------------------------------- stage0 */

/**
 * 一条可疑项 = 产物里的**结构化证据** + 服务端现渲染的那句话。
 *
 * ⚠ `message` 只活在 HTTP 这一层（产物 2026-08-22 起只记证据）。**前端不许自己拼
 * 措辞**：那会变成同一句话的第二个落点，与 `novel/core/warnings.py` 必然分家，
 * 而分家之后界面上那句话按的是哪版阈值谁都说不清。
 * ⚠ 认告警仍然一律用 `warning.kind`，不许拿 `message` 做分支。
 */
export interface Stage0Warning {
  warning: { kind: string; [evidence: string]: unknown }
  message: string
}

export interface Stage0Report {
  original_filename: string
  total_code_points: number
  warnings: Stage0Warning[]
  /**
   * 当前 Stage0 此刻能不能放行 stage1 与付费链。**后端唯一落点的投影，前端不重算。**
   * ⚠ 别再从 `approval` 自己推：后端谓词收紧时改一处就够，两处各写一遍迟早漏一处，
   *   而漏掉的那处就是绕过闸门的复活口。
   * ⚠ 配套的 `block_reason` 已于 2026-08-22 删除：理由码只剩一个取值之后，
   *   它与本格是同一个 bit，而一个 bit 只该有一个投影。
   */
  executable: boolean
  /**
   * 当前这一版的内容身份，**同时是编辑/裁决路的乐观锁**。
   * ⚠ 它替掉了 `round`：revision id 由内容与 parent 一起派生，
   * 「基于哪一版」与「那一版长什么样」它一格就钉完了。
   */
  revision_id: string
  /**
   * 这一版怎么来的。⚠ 它替掉了 `edited_by_human`——那一格是
   * `origin === 'human_edit'` 的同义反复，而且说不出「人**采纳了机器新结果**」
   * 与「人自己改的」之间的差别，两者的正文含义完全相反。
   */
  revision_origin: Stage0RevisionOrigin
}

export type Stage0RevisionOrigin = 'machine' | 'human_edit' | 'human_adopt'

export type Stage0SegmentKind = 'keep' | 'remove' | 'end_marker'

export interface Stage0Segment {
  id: string
  kind: Stage0SegmentKind
  start: number
  end: number
  chars: number
  flagged: boolean
  text: string
  origin: 'machine' | 'human' | null
  removed_kind: string | null
  marker_line: string | null
}

export interface Stage0Segments {
  available: boolean
  reason: string | null
  segments: Stage0Segment[]
}

export interface S0Block {
  kind: 'hook_copy' | 'chapter'
  label: string
  chars: number
  preview: string
  /** 文案段没有 ordinal —— 是「不存在这个字段」，不是 null */
  ordinal?: number
}

export interface S0Candidate {
  id: string
  marker: string
  kind: string
  from_block: string
  chars: number
  confidence: string
  head: string
  tail: string
}

/** mock 的 Stage0 输入解码（`src/mocks/stage0_extras.json`）；不是现役端点响应。 */
export interface Stage0Extras {
  source_id: string
  original_filename: string
  normalized_source_sha1: string
  source_encoding: string
  total_code_points: number
  scanned_at: number
  markers: string[]
  end_marker_hit: string
  blocks: S0Block[]
  /** 真实检出结果。空数组 = 没扫到标记行，**不等于**没有番外。 */
  candidates: S0Candidate[]
  note: string
  demo_candidates: S0Candidate[]
  demo_note: string
}

/* ---------------------------------------------------------------- stage1 */

/** 批准前分章预览里逐章原始章号的异常分类（`contracts/invariants.py::CHAPTER_LABEL_ANOMALY`）。 */
export type ChapterLabelAnomaly = 'reset' | 'gap' | 'repeat'

export type Stage1SegmentKind = 'hook' | 'chapter' | 'end_marker' | 'hole'

export interface Stage1Segment {
  id: string
  kind: Stage1SegmentKind
  start: number
  end: number
  chars: number
  text: string
  chapter_ordinal: number | null
  source_chapter_no: number | null
  label_delta: number | null
  anomaly: ChapterLabelAnomaly | null
}

export interface Stage1View {
  segments: Stage1Segment[]
  anomaly_count: number
}

/* ------------------------------------------------------------ 校验报告 */

export interface CheckAccountingLine {
  key: string
  item: string
  recorded: boolean
  count: number
  detail: unknown[]
}

export interface CheckProvenance {
  freshness: Freshness
  reason: string
  ghosts: string[]
  edges: unknown[]
  invalid: string[]
  origin: string | null
  [k: string]: unknown
}

/**
 * 一次校验的完整输出（`contracts/schema/report.py::CheckReport`）。
 * 分组按 `rules[].status` 现做（groupby 是展示不是判定）；
 * `badge`/`passed`/`counts` 是**服务端折好的判定**，前端不再数。
 */
export interface CheckReport {
  sid: string
  node: string
  stage: string
  provenance: CheckProvenance
  rules: RuleLine[]
  accounting: CheckAccountingLine[]
  badge: Badge
  passed: boolean
  counts: { ok: number; hit: number; unver: number; skipped: number }
}

/**
 * `GET /api/novels/:sid/check/:kind` 的取值面 = `novel.stages.checkable_stages()`。
 * F8c 起只剩 stage0/stage1：新链 Stage2/4/5/7 的复检是节点级 `--check-only`
 * （`novel.cli run <str06 节点> <sid> --check-only`），没有层级 CheckReport；
 * 层名不在取值面里 ⇒ 后端 400 并把可选值列出来。
 */
export type CheckKind = 'stage0' | 'stage1'

export const CHECK_KINDS: CheckKind[] = ['stage0', 'stage1']
