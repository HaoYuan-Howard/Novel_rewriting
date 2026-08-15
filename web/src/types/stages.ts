/**
 * stage0 – stage7 的响应形状。字段名照抄后端契约（`contracts/schema/api.py`），
 * 抓包证据在 `src/fixtures/backend_v1/`。
 */
import type { Badge, Freshness, Mode, Severity, InboxKind } from '@/constants/vocabulary'
import type {
  Facts,
  Finding,
  NameRef,
  PlanNarration,
  Provenance,
  RuleLine,
  Sources,
} from './common'

/* ---------------------------------------------------------------- stage0 */

export interface Stage0Removed {
  kind: string
  marker_line: string
  line_no: number
  span: [number, number]
  code_points: number
  head_preview: string
  tail_preview: string
  origin: 'machine' | 'human'
}

export interface Stage0Warning {
  kind: string
  message: string
}

export interface Stage0Report {
  original_filename: string
  original_path: string
  code: string
  source_sha1: string
  clean_sha1: string
  source_encoding: string
  text_norm: string
  removed: Stage0Removed[]
  total_code_points: number
  body_code_points: number
  warnings: Stage0Warning[]
  approved: 'not_run' | 'pending' | 'approved'
  approved_at: string | null
  round: number
  edited_by_human: boolean
}

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
  source_sha1: string
  available: boolean
  reason: string | null
  segments: Stage0Segment[]
}

export interface Stage0EditRequest {
  expected_round: number
  expected_source_sha1: string
  expected_clean_sha1: string
  removed_segment_ids: string[]
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

export interface Stage0Extras {
  source_id: string
  original_filename: string
  source_sha1: string
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

export type Stage1SegmentKind = 'hook' | 'chapter' | 'end_marker' | 'hole'

export interface Stage1Segment {
  id: string
  kind: Stage1SegmentKind
  start: number
  end: number
  chars: number
  flagged: boolean
  text: string
  chapter_ordinal: number | null
  source_chapter_label: string | null
}

export interface Stage1Segments {
  input_sha1: string
  segments: Stage1Segment[]
}

export interface Stage1Report {
  source_id: string
  title: string
  platform: string | null
  original_filename: string
  original_path: string
  source_sha1: string
  source_encoding: string
  text_norm: string
  total_code_points: number
  body_code_points: number
  body_ratio: number
  num_sections: number
  num_chapters: number
  chapter_nos: number[]
  chapter_nos_strictly_increasing: boolean
  chapter_no_gaps: number[]
  chapter_no_duplicates: number[]
  has_end_marker: boolean
  hook_copy_present: boolean
  hook_copy_code_points: number
  dropped: unknown[]
  dropped_code_points: number
  /** 切得可疑但不算错，要人看一眼再决定要不要重切 */
  warnings: string[]
  notes: string[]
}

export interface Stage1Chapter {
  chapter_ordinal: number
  source_chapter_label: string
  chars: number
  preview: string
}

export interface Stage1Chapters {
  chapters: Stage1Chapter[]
}

export interface Stage1HookCopy {
  hook_copy: { section_id: string; text: string }
  note: string
}

/* ------------------------------------------------- 产物处境（六格一次拿全） */

/** 一份产物此刻的处境（`contracts/schema/orchestration.py::ArtifactState`）。 */
export interface ArtifactState {
  rel: string
  exists: boolean
  bytes: number | null
  mtime: number | null
  freshness: Freshness
  reason: string
  ghosts: string[]
  invalid: boolean
  readable: boolean
}

/* ------------------------------------------------- stage2 · 原文↔骨架 */

/** 该章原文上的一个连续分区。`beat_id=null` ⇒ 空隙（hole）。服务端已切好 text。 */
export interface ReadingSegment {
  start: number
  end: number
  chars: number
  /** 前端绝不按 start/end 自己切字符串（Python 码点 ≠ JS 码元） */
  text: string
  beat_id: string | null
  /** 空隙且 ≥ uncovered_min_chars 才 true；beat 分区恒 false */
  flagged: boolean
}

export interface ReadingHinge {
  kind: string
  /** null = 未定位（INVALID 状态的 hinge 没有原文可引） */
  text: string | null
}

export interface ReadingBeat {
  beat_id: string
  located: boolean
  start: number | null
  end: number | null
  time_layer: string
  place: string | null
  present: string[]
  event: string
  hinges: ReadingHinge[]
}

export interface ReadingNarration {
  grammatical_person: string
  narrator: string | null
  focalizer: string | null
  pronoun_surfaces: string[]
}

export interface ReadingResponse {
  sid: string
  title: string
  chapter_ordinal: number
  text_chars: number
  /** false = stage2 没跑（或蓝图里没有这一章 / 读不回）。左半边仍是 stage1 的原文。 */
  blueprint: boolean
  summary: string | null
  segments: ReadingSegment[]
  beats: ReadingBeat[]
  narration: ReadingNarration[]
  /** 没被任何 beat 覆盖的字数 */
  loose: number
  uncovered_min_chars: number
  /** blueprint=false 的两种来路（没跑 / 读不回）靠它分 */
  blueprint_state: ArtifactState
  _provenance: Provenance | null
}

/* ------------------------------------------------- stage2 · 全局设定 */

export interface GlobalAddress {
  surface: string
  by: NameRef
  context: string
  from_chapter: number
}

export interface GlobalCharacter {
  char_id: string
  canonical: string
  name_distinctiveness: string
  role: string
  /** 全量下发；剧透遮蔽是前端的纯展示行为（R-11.13） */
  fate: string | null
  address: GlobalAddress[]
}

export interface GlobalRelation {
  relation_id: string
  subject: NameRef
  predicate: string
  object: NameRef
  valid_from_chapter: number
  valid_to_chapter: number | null
}

export interface GlobalWorldRule {
  rule_id: string
  kind: string
  versions: { effective_from_chapter: number; desc: string }[]
}

export interface GlobalSecret {
  secret_id: string
  fact: string
  known_by: NameRef[]
  setup_chapters: number[]
  reader_reveal_chapters: number[]
}

export interface Stage2Global {
  sid: string
  title: string
  /** 键是那两个固定中文 key（时代背景 / 世界观） */
  setting: Record<string, string>
  characters: GlobalCharacter[]
  relations: GlobalRelation[]
  world_rules: GlobalWorldRule[]
  /** 空数组 = 这本书没抽到秘密。那**可能是漏抽了**，不是「无」。 */
  secrets: GlobalSecret[]
  arc: {
    conflict_stages: { stage: string; chapters: number[] }[]
    climax: string
    ending: string
    arc_tracks: {
      subject: NameRef
      dimension: string
      change_type: string
      trajectory: string
    }[]
  }
  /** 全屏解引用失败的总数（读数，不是判定） */
  unresolved_refs: number
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

/* ---------------------------------------------------------------- stage3 */

export interface StyleVoice {
  key: string
  text: string
  chars: number
}

export interface StyleHookKey {
  key: string
  text: string
}

export interface StyleBodyMetrics {
  chars: number
  paragraphs: number
  sentences: number
  avg_paragraph_chars: number
  paragraph_sep_chars: number
  avg_sentence_chars: number
  dialogue_ratio: number | null
  dialogue_ratio_applicable: boolean
  quote_styles: string[]
  chapter_chars?: { min: number; max: number; avg: number } | null
  per_chapter_avg_sentence?: { min: number; max: number } | null
  [k: string]: unknown
}

export interface StyleMeasured {
  body: StyleBodyMetrics
  hook: StyleBodyMetrics
}

/** 一项实测漂移（具名对象，不是三元组）。`（缺）` 是特殊值：少一个键也算漂移。 */
export interface DriftItem {
  key: string
  stored: number | string | string[] | null
  now: number | string | string[] | null
}

export interface Stage3Style {
  sid: string
  title: string
  /** false = stage3 还没跑。左半边（实测）仍然有内容。 */
  exists: boolean
  invalid_exists: boolean
  /** 三态：null = 产物不在盘上，「读不读得回来」无从谈起 */
  readable: boolean | null
  voice: StyleVoice[]
  surface_habits: string[]
  hook_style: StyleHookKey[]
  measurable_stored: StyleMeasured | null
  bands_stored: Record<string, string> | null
  _provenance: Provenance | null
  hook_copy_text: string | null
  measured_now: StyleMeasured | null
  bands_now: Record<string, string> | null
  measure_error: string | null
  /** 异常类名（id 不是措辞）：UpstreamMissing = 先跑 stage1；ArtifactUnreadable = 去看那份产物 */
  measure_error_kind: string | null
  /** `[]` = 比过了没漂移；`null` = 有一边算不出来，无从比 */
  drift: DriftItem[] | null
  freshness: Freshness
  reason: string
  ghosts: string[]
}

/* ---------------------------------------------------------------- stage4 */

export interface PlanBeat {
  i: number
  from_beats: string[]
  /** null 只出现在恒等基线上（算不出源字数的拍，不估算） */
  budget_chars: number | null
}

export interface PlanChapterLine {
  output_ordinal: number
  from_source_ordinals: number[]
  chapter_goal: string
  open_hook_goal: string
  end_hook_goal: string
  beats: PlanBeat[]
}

/** 章索引一行。⚠ 没有 in_band/style_hits —— 那是判据结论，去 join /check/stage4。 */
export interface Stage4IndexRow {
  output_ordinal: number
  budget_chars: number | null
  span_chars: number | null
  beats: number
  /** 本章不许泄的 secret 数；null = ctx 不在盘上，判不了 */
  hidden: number | null
}

/** 写手一拍的任务（`CtxBeat`）。 */
export interface CtxBeat {
  events: string[]
  budget_chars: number
  source_chars: number | null
  time_layer?: string | null
  place?: string | null
  present?: string[] | null
  segments?: { time_layer: string; place: string | null; present: string[] }[] | null
  verbatim?: string[] | null
  keep_value?: string[] | null
  must_appear?: string[] | null
  must_hold?: string[] | null
}

/** 一个输出章的完整任务书（`CtxChapter`）。 */
export interface CtxChapter {
  output_ordinal: number
  from_source_ordinals: number[]
  chapter_goal: string
  open_hook_goal: string
  end_hook_goal: string
  budget_chars: number | null
  source_chars: number
  beats: CtxBeat[]
  characters: Record<string, { display_name: string; role: string }>
  narration: PlanNarration[]
  facts: Facts
  must_not_reveal: string[]
}

export interface Stage4Response {
  sid: string
  title: string
  /** true = 恒等基线（没跑过 stage4，现算的「如果什么都不改」），不是真计划 */
  identity_plan: boolean
  reconstruction_strategy: string
  chapters: number[]
  ordinal: number
  index: Stage4IndexRow[]
  plan_chapter: PlanChapterLine
  /** null = 计划在而 ctx 不在盘上（右半边缺席，ctx_state 说明处境） */
  ctx_chapter: CtxChapter | null
  /** 与写手同一把尺子（prompts.target_band）；null ⇔ ctx_chapter=null */
  writer_target: [number, number] | null
  plan_state: ArtifactState
  ctx_state: ArtifactState
  _provenance: Provenance | null
  _ctx_provenance: Provenance | null
}

/* ---------------------------------------------------------------- stage5 */

/** 写手拿到的下发面 —— 与写手同一份（build_material 的 blocks），不是重组的第二份。 */
export interface Materials {
  blocks: string[]
  chars: number
  writer_target: [number, number]
}

/** 原文↔成稿的一个对齐块。anchored=false = 本块并入了无锚的拍。 */
export interface AlignRow {
  beats: number[]
  l: string
  r: string
  anchored: boolean
}

/** 对齐的账。锚点覆盖率必须常驻显示（「锚 3/8 拍」）。 */
export interface DraftAlign {
  beats: number
  anchors: number
  rows: number
  inversions: number
  coverage: number
  /** 常驻：这是对应关系，不是判定 */
  note: string
}

export interface Draft {
  mode: Mode
  relpath: string
  invalid: boolean
  round: number
  /** human = --apply-edit 的人工稿；llm = 模型产的 */
  origin: string
  chars: number
  budget_chars: number | null
  source_chars: number
  overlap_excl_verbatim: number | null
  text: string
  /** null = 本章无锚可对（对齐辅助诚实缺席，不编分界线） */
  align: DraftAlign | null
  align_rows: AlignRow[]
  freshness: Freshness
  reason: string
  _provenance: Provenance
}

export interface DraftIndexRow {
  output_ordinal: number
  budget_chars: number | null
  modes: Mode[]
  invalid_modes: Mode[]
  dup: boolean
  chars: number | null
  overlap_excl_verbatim: number | null
  freshness: Freshness | null
}

export interface Stage5Response {
  sid: string
  title: string
  /** false = 写作上下文还没建。给「先跑 stage4」，**不显示假材料**。 */
  ctx_exists: boolean
  /** null = 计划读不回来，「该有几章」判不了（与 0 是两件事） */
  planned: number | null
  written: number
  invalid: number
  missing: number[]
  duplicates: number[]
  chapters: number[]
  ordinal: number
  index: DraftIndexRow[]
  source_text: string | null
  source_chars_raw: number | null
  materials: Materials | null
  /** 一章常常只有 1 份成稿（另一条臂没写这一章） */
  drafts: Draft[]
  invalid_drafts: Draft[]
  ctx_state: ArtifactState
}

/* ------------------------------------------------------ stage5 · 验收 */

export interface ReviewIndexRow {
  output_ordinal: number
  mode: Mode | null
  round: number | null
  chars: number | null
  budget_chars: number | null
  /** 四 sev 键恒在（0 也写） */
  finding_counts: Record<Severity, number>
  unresolved: number
  /** 验收判的那份与盘上当前那份还是不是同一份；null = 判不了 */
  stale: boolean | null
}

export interface ReviewChapter {
  output_ordinal: number
  findings: Finding[]
  mode: Mode | null
  chars: number | null
  budget_chars: number | null
  round: number | null
  origin: string | null
  /** 当前盘上的正式稿正文（改稿入口贴着它）；null = 本章无稿 */
  text: string | null
  /** 已归档的轮次号（_rounds/），升序 */
  rounds: number[]
  stale: boolean | null
}

export interface ReviewResponse {
  sid: string
  title: string
  review_seq: number
  sources: Sources
  /** 全书按 sev 现数，四键恒在 —— 不读产物的稀疏 counts */
  finding_counts: Record<Severity, number>
  baselines: Record<string, string>
  baselines_identity: Record<string, string>
  book: Finding[]
  wontfix: number
  chapters: number[]
  ordinal: number
  index: ReviewIndexRow[]
  chapter: ReviewChapter
  _provenance: Provenance
}

/* ---------------------------------------------------------------- stage6 */

export interface HookMech {
  punct_fixed: number
  quotes_fixed: number
  paras_fixed: number
}

export interface HookContradiction {
  assertion: string
  evidence: string
  reason: string
}

export interface HookQualityScores {
  read_on_desire: number
  body_voice_match: number
  concrete_scene_and_specificity: number
  curiosity_and_payoff_promise: number
  hook_craft: number
  synopsis_penalty: number
}

export interface HookCandidate {
  index: number
  approach: string
  /** measure() 那把尺子的去空白字数（服务端现算，与判据同源） */
  chars: number
  text: string
  mech: HookMech
  findings: Finding[]
  semantic_state: 'clear' | 'contradiction' | 'uncertain' | 'unreviewed'
  semantic_reason: string
  contradictions: HookContradiction[]
  quality: HookQualityScores | null
  quality_score: number | null
  quality_rank: number | null
  /** 只有最新质量审判为 clear 才能定稿 */
  selectable: boolean
  /** 这一条是不是当前定稿 */
  approved: boolean
}

export interface HookApproved {
  approach: string
  chars: number
  text: string
  origin: string
  mech: HookMech
  findings: Finding[]
}

export interface JudgedDraft {
  output_ordinal: number
  round: number
  sha1: string
  consumed: { output_ordinal: number; text_sha1: string; has_budget_chars: boolean | null } | null
}

export interface Stage6Response {
  sid: string
  title: string
  lead_upto: number
  rationale: string
  candidates: HookCandidate[]
  /** null = 还没定稿 */
  approved: HookApproved | null
  /** 键是成稿完整相对路径 */
  judged: Record<string, JudgedDraft>
  sources: Sources
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

/* ---------------------------------------------------------------- stage7 */

export interface NameChar {
  char_id: string
  old_canonical: string
  new_canonical: string
  renamed: boolean
  /** renamed=true 时恒 null */
  not_renamed_reason: string | null
  reason: string
  aliases: { old: string; new: string }[]
  /** 这个角色全部字面在 applied 记账里的替换总次数（按人求和的读数） */
  hits: number
}

/** 漏网叫法候选 —— 唯一能发现人名表本身漏了人的地方。顺序是承重的。 */
export interface Residual {
  stem: string
  where: string
  /** 命中处起 3 个字（后端是字符串，不是数字） */
  hit: string
  context: string
  confidence: string
  unit: string | null
  suggest: string | null
}

export interface StemBaseline {
  total: number
  covered: number
  residual: number
  /** total=0 时是 null（那种 stem 原文里一次都没出现过），不是 0.0 */
  ratio: number | null
}

/** 逐章对照一行。changed 是 bool（不是 kind 字符串）。 */
export interface NameRow {
  l: string
  r: string
  changed: boolean
}

export interface IdentityBaseline {
  roundtrip_ok: boolean
  chapters: number
  mismatches: string[]
  blind: string[]
}

export interface Stage7Response {
  sid: string
  title: string
  surname_map: Record<string, string>
  characters: NameChar[]
  extra_subs: { old: string; new: string; origin: string; note?: string | null }[]
  subs_count: number
  kept_count: number
  residual: Residual[]
  baselines: Record<string, StemBaseline>
  approved: { origin: string } | null
  book: Finding[]
  /** null = 当前流程从没跑过恒等基线 */
  identity: IdentityBaseline | null
  /** 组装时的账；book_txt_on_disk 才是盘上此刻的实况（两者可分家） */
  book_txt: { sha1: string; chars: number; chapters: number } | null
  book_txt_on_disk: boolean
  sources: Sources
  chapters: number[]
  /** 逐章对照时才有；与 rows 同进同出 */
  ordinal: number | null
  rows: NameRow[] | null
  /** null = 文案没定稿（对照的左边没有来源） */
  hook: { before: string; after: string } | null
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

/* ----------------------------------------------------------------- 其他 */

export interface InboxItem {
  kind: InboxKind
  stage: string
  /** 输出章口径；null = 书级 / 整份产物 */
  chapter: number | null
  rule: string
  what: string
  how: string
}

export interface InboxResponse {
  items: InboxItem[]
  /** 六键全出（0 也要出） */
  counts: Record<InboxKind, number>
  total: number
  wontfix: number
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
  revalidated: boolean
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

/** 与服务端 `STAGES` 逐值相等（stage0~stage7）。 */
export type CheckKind =
  | 'stage0'
  | 'stage1'
  | 'stage2'
  | 'stage3'
  | 'stage4'
  | 'stage5'
  | 'stage6'
  | 'stage7'

export const CHECK_KINDS: CheckKind[] = [
  'stage0',
  'stage1',
  'stage2',
  'stage3',
  'stage4',
  'stage5',
  'stage6',
  'stage7',
]
