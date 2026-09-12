import type { CharactersEdit } from './characters'
import type { GlobalFactRef, GlobalTimeWindow } from './stages'

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

type CollectionEdit<C extends string, T> = {
  collection: C
  expected_collection_sha256: string
  operations: (
    | { op: 'add'; item: T }
    | { op: 'delete'; subject_id: string }
    | { op: 'split'; subject_id: string; items: T[] }
  )[]
}

/** 批次只允许业务编辑；post_edit_review 由同一节点审阅请求承担。 */
export interface GlobalCoreEdit {
  relations?: (Pick<EditableRelation, 'relation_id'> & Partial<Omit<EditableRelation, 'relation_id'>>)[]
  world_rules?: (Pick<EditableRule, 'rule_id'> & Partial<Omit<EditableRule, 'rule_id'>>)[]
  arc_secrets?: (Pick<EditableSecret, 'secret_id'> & Partial<Omit<EditableSecret, 'secret_id'>>)[]
  setting?: {
    era?: string | null
    social_context?: string | null
    era_evidence_refs?: CoreEditEvidence[]
    social_context_evidence_refs?: CoreEditEvidence[]
  } | null
  relations_replace?: EditableRelation[] | null
  world_rules_replace?: EditableRule[] | null
  arc_secrets_replace?: EditableSecret[] | null
  collection_edits?: (
    | CollectionEdit<'relations', EditableRelation>
    | CollectionEdit<'world_rules', EditableRule>
    | CollectionEdit<'arc_secrets', EditableSecret>
  )[]
}

export interface Stage2CoreEditBatch {
  expected_characters_sha256: string
  expected_global_sha256: string
  characters?: CharactersEdit | null
  global?: GlobalCoreEdit | null
}
