/**
 * STR-06 候选链只读监控面的前端类型（F7d）。
 *
 * 与 `contracts/schema/str06/api.py` 的响应模型一一对应；候选链在 F8 激活前
 * 不进现役产品面，这一层也不伪装成已切换——页面顶部常驻「候选链」标注。
 *
 * 两条纪律随类型一起搬过来：
 *  · 没有 badge / passed / counts：判定只有现役 check 报告与回执的机械项名单；
 *  · present=false 与「合法空值」是两件事：半程候选根用 present 表达，空数组
 *    只在盘上真的有空列表时出现。
 */

/** 一个称谓的时间窗（源事实）：从 from_chapter 起可解析到该人物。 */
export interface Str06SurfaceWindow {
  surface: string
  from_chapter: number
  until_chapter: number | null
}

export interface Str06Character {
  char_id: string
  canonical: string
  surfaces: Str06SurfaceWindow[]
}

export interface Str06NameStructure {
  families: Record<string, string>
  people: Record<string, Record<string, unknown>>
}

export interface Str06CharactersView {
  present: boolean
  contract_version: string | null
  characters: Str06Character[]
  name_structure: Str06NameStructure | null
}

export interface Str06BlueprintBeat {
  beat_id: string
  event: string
  participants: string[]
  effects: Record<string, unknown>[]
  /** v13：认知转移（命题＋持有者＋模式），取代 reveals */
  cognition: Record<string, unknown>[]
  /** v13：悬念转移（open/close） */
  loops: Record<string, unknown>[]
  scene_boundary: string | null
  temporal_mode: string | null
}

export interface Str06BlueprintChapter {
  chapter: number
  beats: Str06BlueprintBeat[]
  /** v13：compiler 生成的场景分段（跨章场景在续章同样出现） */
  scenes: Record<string, unknown>[]
  causal_edges: Record<string, unknown>[]
}

export interface Str06BlueprintView {
  present: boolean
  world_constraints: Record<string, unknown>[]
  chapters: Str06BlueprintChapter[]
}

export interface Str06DesignScene {
  scene_id: string
  driver: Record<string, string>
  realization_brief: string
  pov: string
  voice: string
  avoid: string[]
  beats: Record<string, unknown>[]
}

export interface Str06DesignChapter {
  chapter_ordinal: number
  purpose: string
  scene_ids: string[]
}

export interface Str06DesignHook {
  person: string
  focalizers: string[]
  materials: string[]
  concealed: string[]
  forbidden_reveals: number
  design_brief: string
}

export interface Str06DesignBinding {
  obligations: number
  entries: Record<string, unknown>[]
  unbound: string[]
}

export interface Str06DesignView {
  present: boolean
  graph_id: string | null
  scenes: Str06DesignScene[]
  chapters: Str06DesignChapter[]
  hook: Str06DesignHook | null
  binding: Str06DesignBinding | null
}

export interface Str06IrChapter {
  chapter_ordinal: number
  purpose: string
  length_budget: number
  scene_ids: string[]
  scene_lines: string[]
}

export interface Str06IrNavView {
  present: boolean
  book_title: string
  target_chapter_count: number | null
  total_length_budget: number | null
  length_band_low: number | null
  length_band_high: number | null
  chapters: Str06IrChapter[]
  release_schedule: Record<string, unknown>[]
}

export interface Str06ChapterReceipt {
  status: string
  mechanical_checks: string[]
  semantic: string
  length: Record<string, number>
  length_disclosed: boolean
  request_identity: string
  protocol_version: string
  renderer_version: string
  validator_version: string
}

export interface Str06ChapterView {
  ordinal: number
  present: boolean
  prose: string | null
  receipt: Str06ChapterReceipt | null
}

export interface Str06HookView {
  present: boolean
  prose: string | null
  receipt: Record<string, unknown> | null
}

export interface Str06NamingEntry {
  char_ref: string
  family: string
  given: string | null
  full: string
  aliases: Record<string, string>
  keep_reason: string | null
}

export interface Str06NamingView {
  present: boolean
  entries: Str06NamingEntry[]
}

export interface Str06DeliveryChapter {
  chapter_ordinal: number
  prose_sha256: string | null
  length: Record<string, number> | null
  length_disclosed: boolean
}

export interface Str06DeliveryView {
  present: boolean
  delivery_identity: string | null
  final_text_sha256: string | null
  chapters: Str06DeliveryChapter[]
  length_reading: Record<string, number> | null
  disclosure: string | null
}

export interface Str06Node {
  node: string
  stage: string
  owner: string
  paid: boolean
  produced: boolean
}

export interface Str06LedgerNodeCost {
  node: string
  rows: number
  new_paid_rows: number
  cached_rows: number
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
}

export interface Str06LedgerCost {
  present: boolean
  rows: number
  new_paid_rows: number
  cached_rows: number
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
  unknown_cost_rows: number
  per_node: Str06LedgerNodeCost[]
}

export interface Str06Epoch {
  epoch_id: string
  ordinal: number
  reason: string
  session_run_id: string | null
}

export interface Str06RunView {
  candidate: string
  present: boolean
  status: string | null
  failed_node: string | null
  accepted_body: boolean | null
  epoch: Str06Epoch | null
  nodes: Str06Node[]
  cost: Str06LedgerCost | null
}

export interface Str06CandidateSummary {
  candidate: string
  present: boolean
  status: string | null
  updated_at: number | null
}

export interface Str06CandidateList {
  candidates: Str06CandidateSummary[]
}
