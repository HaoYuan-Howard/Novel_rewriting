/** 独立知识关联；取值与关闭式范围来自 Blueprint 工作包。 */
export type KnowledgeLinkResolution =
  | { kind: 'at_facts'; fact_refs: string[]; trigger_fact_ref: string }
  | { kind: 'at_entry'; fact_refs: string[] }
  | { kind: 'unresolved'; reason: string }

export interface KnowledgeLinkDecision {
  obligation_id: string
  expected_subject_sha256: string
  resolution: KnowledgeLinkResolution
}

export interface KnowledgeLinkSubmission {
  scope_sha256: string
  actor: string
  reason: string
  decisions: KnowledgeLinkDecision[]
}

export interface KnowledgeLinkRecord extends KnowledgeLinkDecision {
  actor: string
  reason: string
  source_decision_sha256: string
}

export interface KnowledgeLinkReview {
  scope: {
    contract: 'stage2-knowledge-links-v1'
    scope_sha256: string
    main_text_sha256: string
    knowledge_sha256: string
    blueprint_sha256: string
    obligations: {
      obligation_id: string
      kind: string
      knowledge_ref: string
      chapter_ordinal: number
      subject_sha256: string
      claim: string
      evidence: { chapter_ordinal: number; source: { text: string; start: number; end: number } | null }[]
      candidate_fact_refs: string[]
      entry_fact_refs: string[]
    }[]
  }
  inherited: KnowledgeLinkRecord[]
  pending_obligation_ids: string[]
}
