/**
 * 跨 endpoint 复用的形状。字段名一律照抄后端响应，不改名、不压平嵌套。
 */
import type { Freshness, Severity } from '@/constants/vocabulary'

/**
 * 人名引用。stage2 层是对象（带是否解析成功），
 * 到 stage4 / stage5 的 facts 里已经被解引用成纯字符串 —— 两层形状不同，别合并。
 */
export interface CharRef {
  raw: string
  kind: string
  display: string
  resolved: boolean
}

/** 过期原因。mock 里恒为空数组，非空路径按类型建，不按数据建。 */
export interface StaleRef {
  input?: string
  key?: string
  was?: string
  now?: string
}

/** 来源记录。形状随 stage 而异，`{}` 很常见 —— 空对象即「没有来源记录」。 */
export interface Provenance {
  model?: string
  code?: string
  prompts?: Record<string, string>
  inputs?: Record<string, string>
  [k: string]: unknown
}

/** 一条判据结论。book[] 与 chapter.findings[] 同型。 */
export interface Finding {
  rule: string
  sev: Severity
  item: string
  detail: string
  evidence: string[]
  owner?: string | null
  actionable?: boolean
  identity_hit?: boolean
  evidence_key?: string
  unresolved_rounds?: number
  converge?: string
}

/** 判据登记表的一行（stage6 / stage7 的 rules[]） */
export interface RuleRow {
  rule: string
  item: string
  sev: Severity
  hit: number
  identity?: string
}

export interface WriterContextSource {
  state: Freshness
  detail: StaleRef[]
  code: string
}

export interface Sources {
  writer_context: WriterContextSource
  drafts?: { stale: StaleRef[]; no_prov: StaleRef[] }
}

/** stage4 / stage5 共用的「写手真正拿得到的事实」 */
export interface Facts {
  setting: Record<string, string>
  relations: { subject: string; predicate: string; object: string }[]
  secrets: {
    secret_id: string
    fact: string
    known_by: string[]
    reader_knows_by_now: boolean
  }[]
  world_rules: { rule_id: string; kind: string; description: string }[]
  arc: { current_stage: { stage: string; chapters: number[] } }
  address: { who: string; surface: string; by: string; context: string }[]
}

export interface PlanNarration {
  segment: string
  grammatical_person: string
  narrator: string
  pronoun_surfaces: string[]
}
