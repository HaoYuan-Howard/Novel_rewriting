import type { Finding, Provenance } from './common'
import type { SourceBlurbMaterials } from './stages'

export interface PrepareParagraphRef { start_para: number; end_para: number }
export interface PrepareMainEvidence extends PrepareParagraphRef { chapter_ordinal: number }

/** 编辑只提交原文段落引用；正式坐标和语义接受由后端产生。 */
export interface PrepareDraft {
  facts: (
    | { kind: 'supported'; claim_id: string; claim: string; evidence: PrepareMainEvidence }
    | { kind: 'hook_supported'; claim_id: string; claim: string; hook_evidence: PrepareParagraphRef }
    | { kind: 'unresolved'; claim_id: string; claim: string; reason: string }
  )[]
  opening:
    | { kind: 'proposed'; through_chapter: number; rationale: string; boundary_evidence: PrepareMainEvidence }
    | { kind: 'unresolved'; reason: string }
}

export interface PrepareArtifact {
  source_blurb: SourceBlurbMaterials
  main_text_sha256: string
  hook_copy_sha256: string
  _provenance: Provenance & {
    /** 当前源免费完整重编；模型字段仍属于此证明引用的历史生成。 */
    source_rebuild?: {
      source_sha256: string
      source_root_clean_sha1: string
      target_root_clean_sha1: string
      generation_root_clean_sha1: string
      source_revision_id: string
      target_revision_id: string
      main_text_sha256: string
      hook_copy_sha256: string
    }
  }
}

export interface PrepareEdit {
  actor: string
  reason: string
  review_token: string
  source_sha256: string
  main_text_sha256: string
  hook_copy_sha256: string
  source_blurb: PrepareDraft
}

export interface PrepareDecision {
  actor: string
  reason: string
  source_sha256: string
  review_token: string
  decision: 'accept' | 'rework' | 'uncertain'
  edit: PrepareEdit | null
}

export interface PrepareState {
  state: 'missing' | 'invalid' | 'pending' | 'accepted' | 'stale' | 'unknown'
  reason: string
  subject_sha256: string | null
  review_token: string | null
  prepare: PrepareArtifact | null
  review: {
    actor: string; reason: string; source_sha256: string; request_sha256: string
    decision: PrepareDecision['decision']; at: string
  } | null
  pending_commit: boolean
}

export interface PrepareView { sid: string; title: string; status: PrepareState }
export interface PrepareWorkpack {
  sid: string
  title: string
  state: PrepareState
  main_text: { chapter_ordinal: number; text: string }[]
  hook_copy: { text: string }
  edit_template: PrepareEdit
  decision_template: PrepareDecision
  review_contract: string
}

export interface PrepareEditRequest {
  review_token: string; actor: string; reason: string; source_blurb: PrepareDraft
}
export interface PrepareDecisionRequest {
  review_token: string; actor: string; reason: string
  decision: PrepareDecision['decision']; edit: PrepareDraft | null
}
export interface PrepareEditPreview {
  would_apply: boolean; findings: Finding[]; source_blurb: SourceBlurbMaterials
}
