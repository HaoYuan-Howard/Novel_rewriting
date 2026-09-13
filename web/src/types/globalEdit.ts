import type { GlobalFactRef, GlobalTimeWindow } from './stages'

/**
 * Stage2 全局事实 typed 编辑（GlobalEdit v6）与独立只读预检（GlobalEditPreview）。
 *
 * 形状对齐 `contracts/schema/stage2.py::GlobalEdit` 与
 * `contracts/schema/api.py::GlobalEditPreview`；稀疏 patch 以字段是否出现为准，
 * 前端只透传模板 JSON，不在浏览器补默认值或预判候选。
 * Stage2 语义审阅已退役：预检结果不是预授权，语义未审随交付披露。
 */

export type CoreEditEvidence = string | { chapter_ordinal: number; locator: string }

export interface EditableRelation extends GlobalTimeWindow {
  relation_id: string
  subject_id: string
  predicate: string
  object_id: string
  evidence_refs: CoreEditEvidence[]
}

export interface EditableRule {
  rule_id: string
  kind: string
  versions: (GlobalTimeWindow & { version_id: string; desc: string; evidence_refs: CoreEditEvidence[] })[]
}

export interface EditableSecret {
  secret_id: string
  fact: string | null
  fact_ref: GlobalFactRef | null
  evidence_refs: CoreEditEvidence[]
  setup_chapters: number[]
  revealed_from: number
}

export interface GlobalCollectionEdit<C extends string, T> {
  collection: C
  expected_collection_sha256: string
  operations: (
    | { op: 'add'; item: T }
    | { op: 'delete'; subject_id: string }
    | { op: 'split'; subject_id: string; items: T[] }
  )[]
}

/** 全局事实 typed 编辑；不能承载人物或文案（人物走 CharactersEdit）。 */
export interface GlobalEdit {
  arc_secrets?: (Pick<EditableSecret, 'secret_id'> & Partial<Omit<EditableSecret, 'secret_id'>>)[]
  world_rules?: (Pick<EditableRule, 'rule_id'> & Partial<Omit<EditableRule, 'rule_id'>>)[]
  relations?: (Pick<EditableRelation, 'relation_id'> & Partial<Omit<EditableRelation, 'relation_id'>>)[]
  setting?: {
    era?: string | null
    social_context?: string | null
    era_evidence_refs?: CoreEditEvidence[]
    social_context_evidence_refs?: CoreEditEvidence[]
  } | null
  arc_secrets_replace?: EditableSecret[] | null
  world_rules_replace?: EditableRule[] | null
  relations_replace?: EditableRelation[] | null
  collection_edits?: (
    | GlobalCollectionEdit<'relations', EditableRelation>
    | GlobalCollectionEdit<'world_rules', EditableRule>
    | GlobalCollectionEdit<'arc_secrets', EditableSecret>
  )[]
}

/** 独立只读预检结果；不是预授权。 */
export interface GlobalEditPreview {
  /** 编辑后业务候选的 canonical 摘要；与 apply 现场重判同一判据。 */
  result_subject_sha256: string
  /** 实际改动的业务路径。 */
  changed: (string)[]
  /** 非空表示当前候选会被落盘闸门拦下并写入问题稿。 */
  hard_hits: (string)[]
}
