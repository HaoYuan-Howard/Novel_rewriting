/** 后端当前义务投影；界面不从字段差异或历史摘要推断接受。 */
export interface Stage2ReviewObligation {
  obligation_id: string
  subject_sha256: string
  owner: string
  chapter_ordinals: number[]
  subject_path: string
  kind: string
  context_refs: string[]
  problem_subject_sha256: string | null
}

export interface Stage2ReviewUnit {
  obligation: Stage2ReviewObligation
  decision: string
  actor: string
  reason: string
  source_decision_sha256: string | null
}

export interface Stage2ReviewCoverage {
  scope: {
    contract: 'stage2-review-obligations-v1'
    node: string
    scope_sha256: string
    obligations: Stage2ReviewObligation[]
  }
  inherited: Stage2ReviewUnit[]
  pending_obligation_ids: string[]
  prior_problems: Stage2ReviewUnit[]
}

export interface Stage2ReviewException {
  obligation_id: string
  decision: string
  reason: string
}

export interface Stage2ReviewSubmission {
  scope_sha256: string
  default_decision: string
  exceptions: Stage2ReviewException[]
}
