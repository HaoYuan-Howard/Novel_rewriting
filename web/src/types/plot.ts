/** 范围材料的 JSON 投影；正式主体形状来自后端生成类型。 */
import type { PlotBlueprint, PlotReviewResponse, PlotSubjectRef, ReviewEvidenceRef } from './plot.generated'

export type ReviewScope = `chapter:${number}` | 'cross_chapter'
export type Dimension = keyof PlotReviewResponse['coverage']
export interface PlotReviewMaterial {
  scope: ReviewScope
  sources: { chapter_ordinal: number; text: string }[]
  blueprints: PlotBlueprint[]
  dependencies: unknown[]
  issues: {
    issue_id: string
    dimension: Dimension
    problem: string
    state: 'open' | 'resolved' | 'withdrawn'
    subjects: PlotSubjectRef[]
    evidence_refs: ReviewEvidenceRef[]
    last_decision_sha256: string
  }[]
  chapter_acceptances: { scope: ReviewScope; subject_sha256: string; decision_sha256: string }[]
}
