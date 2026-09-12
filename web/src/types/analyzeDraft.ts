/** 编译前的已购候选；只传回后端模板，界面不替代正式 schema 或分配身份。 */
export type AnalyzeDraftOwner = 'stage2.characters' | 'stage2.global'

export interface AnalyzeOwnerDraftEdit {
  owner: AnalyzeDraftOwner
  source_kind: 'analyze_owner_draft'
  source_receipt_sha256: string
  source_draft_sha256: string
  source_mapping_sha256: string
  character_bindings: Record<string, string>
  characters: { characters: Record<string, unknown>[] } | null
  global_draft: Record<string, unknown> | null
}

export interface AnalyzeDraftView {
  sid: string
  owner: AnalyzeDraftOwner
  source_state: 'draft_invalid' | 'awaiting_characters' | 'prepared'
  template: AnalyzeOwnerDraftEdit
  diagnostics: string[]
  evidence_candidates: { evidence_candidate_id: string; chapter_ordinal: number; excerpt: string }[]
}

export interface AnalyzeDraftEditRequest {
  actor: string
  reason: string
  edit: AnalyzeOwnerDraftEdit
}
