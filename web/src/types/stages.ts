/**
 * stage0 – stage7 的响应形状。字段名照抄，联合类型显式写出来。
 */
import type { Badge, Freshness, Mode, Op, Severity, InboxKind } from '@/constants/vocabulary'
import type {
  CharRef,
  Facts,
  Finding,
  PlanNarration,
  Provenance,
  RuleRow,
  Sources,
  StaleRef,
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

/* ------------------------------------------------- stage2 · 原文↔骨架 */

export interface ReadingSegmentBeat {
  kind: 'beat'
  start: number
  end: number
  chars: number
  beat_id: string
  /** 服务端已经切好片。前端绝不按 start/end 自己切字符串（Python 码点 ≠ JS 码元） */
  text: string
}

export interface ReadingSegmentHole {
  kind: 'hole'
  start: number
  end: number
  chars: number
  /** 达到 uncovered_min_chars 才判为「漏」 */
  flagged: boolean
  text: string
}

export type ReadingSegment = ReadingSegmentBeat | ReadingSegmentHole

export interface Hinge {
  kind: string
  text: string
}

export interface ReadingBeat {
  beat_id: string
  located: boolean
  start: number
  end: number
  time_layer: string
  place: string
  present: CharRef[]
  event: string
  hinges: Hinge[]
  span: { starts_with: string; ends_with: string }
}

export interface ReadingNarration {
  last_beat_id: string | null
  grammatical_person: string
  narrator: CharRef
  focalizer: CharRef
  pronoun_surfaces: string[]
}

export interface ReadingResponse {
  chapter_ordinal: number
  text_chars: number
  summary: string
  segments: ReadingSegment[]
  beats: ReadingBeat[]
  narration: ReadingNarration[]
  /** 空数组 = 抽取过程没报出问题。**不代表抽全了。** */
  problems: string[]
  /** 没被任何 beat 覆盖的字数 */
  loose: number
  uncovered_min_chars: number
  blueprint: boolean
}

/* ------------------------------------------------- stage2 · 全局设定 */

export interface GlobalAddress {
  surface: string
  by: CharRef
  context: string
  from_chapter: number
}

export interface GlobalCharacter {
  char_id: string
  canonical: string
  display: string
  name_distinctiveness: string
  role: string
  address: GlobalAddress[]
  /** 剧透开关关着时服务端不下发这个字段 */
  fate?: string
}

export interface GlobalRelation {
  relation_id: string
  subject: CharRef
  predicate: string
  object: CharRef
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
  known_by: string[]
  reader_knows_by_now: boolean
}

export interface Stage2Global {
  source_id: string
  title: string
  setting: Record<string, string>
  characters: GlobalCharacter[]
  relations: GlobalRelation[]
  world_rules: GlobalWorldRule[]
  /** 空数组 = 这本书没抽到秘密。那**可能是漏抽了**，不是「无」。 */
  secrets: GlobalSecret[]
  arc: {
    conflict_stages: { stage: string; chapters: number[] }[]
    arc_tracks: {
      subject: CharRef
      dimension: string
      change_type: string
      trajectory: string
    }[]
  }
  spoilers: boolean
  /** 被遮住的条数。secrets: [] 配 hidden.secrets: 7 —— 遮蔽和空是两回事 */
  hidden: { fate: number; secrets: number; climax: boolean; ending: boolean }
  /** 这句必须常驻显示在剧透开关旁边，不能收进 tooltip */
  partial_mask_note: string
  unresolved_refs: number
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
  dialogue_ratio: number
  dialogue_ratio_applicable: boolean
  quote_styles: string[]
  chapter_chars?: { min: number; max: number; avg: number }
  per_chapter_avg_sentence?: { min: number; max: number }
}

export interface StyleMeasured {
  body: StyleBodyMetrics
  hook: StyleBodyMetrics
}

export interface Stage3Style {
  /** false = stage3 还没跑。左半边（实测）仍然有内容。 */
  exists: boolean
  invalid_exists: boolean
  title: string
  source_id: string
  voice: StyleVoice[]
  voice_extra: StyleVoice[]
  surface_habits: string[]
  hook_style: StyleHookKey[]
  hook_copy_text: string
  measurable_stored: StyleMeasured | null
  measured_now: StyleMeasured | null
  measure_error: string | null
  /** 档位词。档位词有没有变，比数字有没有变更要紧。 */
  bands_now: Record<string, string>
  bands_stored: Record<string, string>
  bands_error: string | null
  drift: string[]
  _provenance: Provenance
}

/* ---------------------------------------------------------------- stage4 */

export interface PlanBeat {
  i: number
  from_beats: string[]
  unknown_refs: string[]
  budget_chars: number
  source_chars: number
  verbatim_chars: number
  events: string[]
  /** 会是 null：这一拍跨了多个时间层 / 地点，单一值填不了 */
  time_layer: string | null
  place: string | null
  /** 这里是显示名字符串，不是 CharRef */
  present: string[]
  segments: { time_layer: string; place: string; present: string[] }[]
  /** 必须逐字写进去的原话 */
  verbatim: string[]
  keep_value: string[]
  must_appear: string[]
  must_hold: string[]
}

export interface PlanIndexRow {
  output_ordinal: number
  op: Op
  span_chars: number
  budget_chars: number
  in_band: boolean
  style_hits: string[]
  span_coverage: [number, number]
  beats: number
  hidden: number
}

export interface PlanChapter {
  output_ordinal: number
  from_source_ordinals: number[]
  op: Op
  chapter_goal: string
  open_hook_goal: string
  end_hook_goal: string
  span_chars: number
  span_coverage: [number, number]
  band: [number, number]
  budget_chars: number
  in_band: boolean
  writer_target: [number, number]
  writer_target_estimated: boolean
  chapter_source_chars: number
  beats: PlanBeat[]
  style_hits: string[]
  characters: Record<string, { display_name: string; role: string }>
  facts: Facts
  narration: PlanNarration[]
  must_not_reveal: string[]
}

export interface Stage4Response {
  plan_exists: boolean
  /** 'identity' = 还没跑过 stage4，这是恒等基线，不是真计划 */
  plan_source: string
  ctx_source: string
  invalid_exists: boolean
  title: string
  source_id: string
  book: {
    reconstruction_strategy: string
    strategy_style_hits: string[]
    ops: Record<string, number>
    chapters: number
    span_chars: number
    budget_chars: number
    ratio: number
    beat_density: number
    characters: Record<
      string,
      { display_name: string; role: string; name_distinctiveness: string }
    >
  }
  not_in_writer_prompt: string
  identity_note: string
  plan_scope_note: string
  _provenance: Provenance
  _ctx_provenance: Provenance
  index: PlanIndexRow[]
  ordinal: number
  chapter: PlanChapter
}

/* ---------------------------------------------------------------- stage5 */

/**
 * 写手拿到的材料里的一拍。
 * 注意与 PlanBeat 的差别：少 i / from_beats / unknown_refs / source_chars / verbatim_chars，
 * 且五个约束字段缺省是 **null** 而不是 []（stage4 那边是 []）。两层不一致，判空要写两套。
 */
export interface MaterialBeat {
  budget_chars: number
  events: string[]
  /** 这三个也会是 null（样本里第 7 拍就是）。null ≠ 空数组，判空要判两次。 */
  time_layer: string | null
  place: string | null
  present: string[] | null
  segments: { time_layer: string; place: string; present: string[] }[] | null
  verbatim: string[] | null
  keep_value: string[] | null
  must_appear: string[] | null
  must_hold: string[] | null
}

export interface Materials {
  chapter_goal: string
  open_hook_goal: string
  end_hook_goal: string
  budget_chars: number
  source_chars: number
  writer_target: [number, number]
  writer_target_estimated: boolean
  beats: MaterialBeat[]
  facts: Facts
  narration: PlanNarration[]
  characters: Record<string, { display_name: string; role: string }>
  must_not_reveal_count: number
}

/** 原文↔成稿的一个对齐块。一行可能盖好几拍 —— anchored 为 false 时必须显式标出来。 */
export interface AlignRow {
  beats: number[]
  l: string
  r: string
  budget_chars: number
  source_chars: number
  anchored: boolean
}

export interface DraftAlign {
  beats: number
  anchors: number
  rows: number
  inversions: number
  /** 锚点覆盖率。这个数要常驻显示（「锚 3/8 拍」），不能藏。 */
  coverage: number
  note: string
}

export interface Draft {
  mode_dir: string
  mode: Mode
  tagged: boolean
  op: Op
  chars: number
  budget_chars: number
  source_chars: number
  overlap_excl_verbatim: number
  text: string
  align_rows: AlignRow[]
  align: DraftAlign
  freshness: Freshness
  stale: StaleRef[]
  provenance: Provenance
}

export interface DraftIndexRow {
  output_ordinal: number
  op: Op
  budget_chars: number
  modes: Mode[]
  invalid_modes: Mode[]
  dup: boolean
  chars: number
  overlap_excl_verbatim: number
  freshness: Freshness
}

export interface DraftChapter {
  output_ordinal: number
  op: Op
  from_source_ordinals: number[]
  source_text: string
  source_chars_raw: number
  materials: Materials
  /** 字段是数组，但一章常常只有 1 份成稿（另一条臂没写这一章） */
  drafts: Draft[]
  invalid_drafts: Draft[]
}

export interface Stage5Response {
  /** false = 写作上下文还没建。给「先跑 stage4」，**不显示假材料**。 */
  ctx_exists: boolean
  note: string | null
  modes: Mode[]
  book_draft_exists: boolean
  gate_note: string
  depth_note: string
  title: string
  source_id: string
  planned: number
  written: number
  invalid: number
  missing: number[]
  duplicates: number[]
  index: DraftIndexRow[]
  ordinal: number
  chapter: DraftChapter
}

/* ------------------------------------------------------ stage5 · 验收 */

export interface ReviewIndexRow {
  output_ordinal: number
  mode: Mode
  round: number
  chars: number
  budget_chars: number
  hard: number
  info: number
  unver: number
  unresolved: number
  stale: boolean
}

export interface ReviewChapter {
  output_ordinal: number
  findings: Finding[]
  mode: Mode
  mode_dir: string
  chars: number
  budget_chars: number
  round: number
  origin: string
  text: string
  segments: { text: string; rule?: string; sev?: Severity }[]
  errs: string[]
  warns: string[]
  rounds: unknown[]
  /** 空数组 = 这一章没有第二轮稿可比 */
  diff_rows: { l: string; r: string; kind: string }[]
  provenance: Provenance
}

export interface ReviewResponse {
  edit_note: string
  scope_note: string
  note: string | null
  review_seq: number
  title: string
  sources: Sources
  counts: Partial<Record<Severity, number>>
  baselines: Record<string, string>
  book: Finding[]
  drift: string[]
  index: ReviewIndexRow[]
  ordinal: number
  chapter: ReviewChapter
}

/* ---------------------------------------------------------------- stage6 */

export interface HookSegment {
  text: string
  rule?: string
  sev?: Severity
}

export interface HookMech {
  punct_fixed: number
  quotes_fixed: number
  quote_problems: string[]
  paras_fixed: number
}

export interface HookCandidate {
  index: number
  approach: string
  chars: number
  text: string
  segments: HookSegment[]
  findings: Finding[]
  mech: HookMech
  hard: number
  info: number
  approved: boolean
}

export interface HookApproved {
  approach: string
  text: string
  origin: string
  mech: HookMech
  findings: Finding[]
}

export interface Stage6Response {
  scope_note: string
  approve_note: string
  note: string | null
  title: string
  lead_upto: number
  rationale: string
  candidates: HookCandidate[]
  /** null = 还没定稿 */
  approved: HookApproved | null
  book: Finding[]
  sources: Sources
  drift: string[]
  baselines: Record<string, string>
  baselines_identity: Record<string, string>
  counts: Partial<Record<Severity, number>>
  context: {
    chapters: number[]
    judged: Record<
      string,
      { output_ordinal: number; round: number; sha1: string; mode: Mode }
    >
  }
  rules: RuleRow[]
}

/* ---------------------------------------------------------------- stage7 */

export interface NameChar {
  char_id: string
  old_canonical: string
  new_canonical: string
  renamed: boolean
  /** renamed:false 时这里说明为什么没改 */
  not_renamed_reason: string
  reason: string
  aliases: { old: string; new: string }[]
  changed_aliases: { old: string; new: string }[]
  hits: number
}

/** 漏网叫法候选 —— 唯一能发现人名表本身漏了人的地方 */
export interface Residual {
  stem: string
  where: string
  hit: number
  context: string
  confidence: string
  unit: string
  suggest: string | null
}

export interface NameRow {
  l: string
  r: string
  kind: 'same' | 'changed'
}

export interface Stage7Response {
  scope_note: string
  residual_note: string
  note: string | null
  title: string
  title_new: string
  surname_map: Record<string, string>
  characters: NameChar[]
  subs_count: number
  kept_count: number
  extra_subs: { old: string; new: string; origin: string }[]
  residual: Residual[]
  baselines: Record<
    string,
    { total: number; covered: number; residual: number; ratio: number }
  >
  approved: { origin: string } | null
  book: Finding[]
  sources: Sources
  drift: string[]
  counts: Partial<Record<Severity, number>>
  book_txt: {
    sha1: string
    chars: number
    chapters: number
    has_hook: boolean
    on_disk: string
  } | null
  chapters: number[]
  /** 逐章对照时才有 */
  ordinal: number | null
  rows: NameRow[] | null
  hook: { before: string; after: string }
  rules: RuleRow[]
}

/* ----------------------------------------------------------------- 其他 */

export interface InboxItem {
  kind: InboxKind
  stage: string
  chapter: number
  rule: string
  what: string
  how: string
}

export interface InboxResponse {
  items: InboxItem[]
  counts: Partial<Record<InboxKind, number>>
  total: number
  wontfix: number
}

/** 一条能修它的动作。没有这个字段的结论，界面就不该画「重跑修它」按钮。 */
export interface FixAction {
  kind: string
  extra: string[]
  paid: boolean
}

export interface CheckItem {
  item: string
  detail: string
  /** 涉及哪几章由服务端结构化下发。不要从 detail 那段中文里正则抠。 */
  chapters: number[] | null
  fix: FixAction | null
}

export interface CheckUnverItem {
  item: string
  detail: string
}

export interface CheckReport {
  title: string
  sid: string
  /** 查过、没问题的条目 */
  oks: string[]
  unver: CheckUnverItem[]
  info: CheckItem[]
  hard: CheckItem[]
  passed: boolean
  hard_count: number
  unver_count: number
  /** amber = passed 且 unver_count > 0。它不是绿色。 */
  badge: Badge
}

export type CheckKind =
  | 'stage1'
  | 'stage2'
  | 'stage3'
  | 'stage4'
  | 'stage5_review'
  | 'stage6'
  | 'stage7'

export const CHECK_KINDS: CheckKind[] = [
  'stage1',
  'stage2',
  'stage3',
  'stage4',
  'stage5_review',
  'stage6',
  'stage7',
]
