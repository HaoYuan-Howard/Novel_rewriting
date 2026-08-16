/**
 * 跨 endpoint 复用的形状。字段名一律照抄后端响应，不改名、不压平嵌套。
 * 权威：`contracts/schema/api.py` 与 `contracts/schema/report.py`（backend_v1 fixture 是抓包证据）。
 */
import type { Freshness, Severity } from '@/constants/vocabulary'

/**
 * 一个「谁」的引用，服务端已解引用（stage2 全局屏）。
 * 解析不动时 `resolved=false` 且 `display === raw` —— 服务端不编名字。
 */
export interface NameRef {
  raw: string
  display: string
  resolved: boolean
}

/** 过期原因的一条边（writer_context 来源账的 detail）。 */
export interface StaleRef {
  key: string
  was: string | null
  now: string | null
}

/** 来源记录。形状随 stage 而异，`{}` 很常见 —— 空对象即「没有来源记录」。 */
export interface Provenance {
  root_clean_sha1: string
  model?: string
  code?: string
  round?: number
  origin?: string
  prompts?: Record<string, string>
  inputs?: Record<string, unknown>
  [k: string]: unknown
}

/**
 * 一条判据结论（后端 `Finding` 十四字段，键恒在）。
 * `wontfix` 非空 = 有人看过并豁免 —— 结论不删、单独计数。
 */
export interface Finding {
  rule: string
  sev: Severity
  item: string
  detail: string
  evidence: string[]
  fix_args: Record<string, string>
  evidence_key: string | null
  owner: string | null
  actionable: boolean
  identity_hit: boolean
  identity_dead: boolean
  converge: string | null
  unresolved_rounds: number | null
  wontfix: { why: string; at: string } | null
}

/** 一条能修它的动作（服务端下发，前端不拼 argv）。没有它就不画「重跑修它」。 */
export interface FixRoute {
  node: string
  flags: string[]
  paid: boolean
  available: boolean
  unavailable_reason: string | null
}

/**
 * 校验报告的一行（`CheckReport.rules[]`，`contracts/schema/report.py::RuleLine`）。
 * 两轴在此：`check_kind`（结论怎么来）× `remediation`（谁能修），与 `sev`/`status` 正交。
 */
export interface RuleLine {
  rule: string
  sev: Severity
  status: 'ok' | 'hit' | 'unver' | 'skipped'
  item: string
  requirement: string
  check_kind: 'mechanical' | 'semantic'
  remediation: 'llm' | 'human' | 'deterministic' | 'none'
  fix: FixRoute | null
  findings: Finding[]
  why: string | null
  scanned: string | null
  baseline: string | null
  note: string | null
}

/** `writer_context` 这一条上游的新鲜度（三态：没有 inputs 是「不知道」不是「匹配」）。 */
export interface WriterContextSource {
  state: Freshness
  detail: StaleRef[]
  code: string | null
}

export interface Sources {
  writer_context: WriterContextSource
  /** 只有 stage5 验收有：成稿那一侧的来源账。 */
  drafts?: { stale: string[]; no_prov: string[] }
}

/** stage4 / stage5 共用的「写手真正拿得到的事实」（`CtxFacts`）。 */
export interface Facts {
  setting: Record<string, string>
  relations: { subject: string; predicate: string; object: string }[]
  secrets: {
    secret_id: string
    fact: string
    known_by: string[]
    reader_knows_by_now: boolean
  }[]
  /** 键名是 `description` 不是 `desc` —— 照抄后端。 */
  world_rules: { rule_id: string; kind: string; description: string }[]
  arc: {
    current_stage: { stage: string; chapters: number[] }
    /** 只有末章才有；非末章恒 null（发了就是泄结局）。 */
    arc_tracks:
      | { subject: string; dimension: string; change_type: string; trajectory: string }[]
      | null
    climax: string | null
    ending: string | null
  }
  address: { who: string; surface: string; by: string | null; context: string }[]
}

/** 逐章叙述任务（`CtxNarration`）。 */
export interface PlanNarration {
  segment: string
  grammatical_person: string
  narrator: string | null
  focalizer: string | null
  pronoun_surfaces: string[] | null
}
