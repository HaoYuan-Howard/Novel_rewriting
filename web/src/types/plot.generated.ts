/** 由 scripts/export_plot_types.py 生成；修改正式契约后重新生成。 */
export type AccountingLine = {
  "key": string
  "item": string
  "recorded": boolean
  "count": number
  "detail": (string)[]
}

export type ApprovalWorkpack = {
  "v": 2
  "sid": string
  "kind": "design" | "structure"
  "candidate_sha256": string | null
  "subject_sha256": string
  "review_token": string
  "chapter_ordinals": (number)[]
  "groups": (WorkpackGroup)[]
  "disposition": "review_required" | "already_current"
  "current_decision_sha256": string | null
  "reading": WorkpackReading | null
}

export type ArtifactState = {
  "rel": string
  "exists": boolean
  "bytes": number | null
  "mtime": number | null
  "freshness": "fresh" | "stale" | "unknown"
  "display_freshness": "missing" | "fresh" | "stale" | "unknown"
  "reason": "no_provenance" | "no_inputs" | "ghost" | "file_changed" | "consumed_changed" | "binding_input_contract_obsolete" | "binding_input_contract_unproven" | "consumed_undeclared" | "consumed_unresolved" | "legacy_blueprint_inputs" | "global_naming_blocked" | "match"
  "ghosts": (string)[]
  "invalid": boolean
  "invalid_freshness": "fresh" | "stale" | "unknown" | null
  "invalid_reason": "no_provenance" | "no_inputs" | "ghost" | "file_changed" | "consumed_changed" | "binding_input_contract_obsolete" | "binding_input_contract_unproven" | "consumed_undeclared" | "consumed_unresolved" | "legacy_blueprint_inputs" | "global_naming_blocked" | "match" | null
  "readable": boolean
  "condition": "missing" | "ready" | "invalid" | "unreadable"
}

export type BeatMoment = {
  "kind": "at_entry" | "at_beat"
  "beat_ref": string | null
}

export type BeatMomentResponse = {
  "kind": "at_entry" | "at_beat"
  "beat_ref": string | null
}

export type BeatPlacement = {
  "beat_ref": string
  "deliveries": (PresentationDelivery)[]
}

export type BindingConcern = {
  "constraint_code": "causal_conflict" | "stage_conflict" | "material_conflict" | "future_leak" | "insufficient_evidence"
  "chapter_ordinal": number | null
  "scenes": (string)[]
  "reason": string
  "closed_refs": (string)[]
}

export type BindingInfeasibilityWitness = {
  "chapter_ordinal": number
  "scenes": (string)[]
  "constraint_code": "missing_chapter_catalog"
  "closed_refs": (string)[]
  "proof_basis": string
}

export type BlindAnchorBinding = {
  "endpoint_alias": string
  "scene_ref": string
}

export type BlindBeatPlacement = {
  "beat_alias": string
  "deliveries": (BlindPresentationDelivery)[]
}

export type BlindCandidateChapter = {
  "structure_decision": BlindStructureDecision
  "chapter_goal": BlindLocalGoalResponse
  "open_hook_goal": BlindLocalGoalResponse
  "end_hook_goal": BlindLocalGoalResponse
  "chapter_rewrite_brief": string
  "scenes": (BlindCandidateScene)[]
  "beat_placements": (BlindBeatPlacement)[]
  "anchor_bindings": (BlindAnchorBinding)[]
  "output_ordinal": number
  "weight": "CH_MAJOR" | "CH_ABOVE" | "CH_EVEN" | "CH_BELOW" | "CH_MINOR"
}

export type BlindCandidateScene = {
  "scene_ref": string
  "weight": "W_PEAK" | "W_STRONG" | "W_MID" | "W_QUICK" | "W_BRUSH"
  "purpose": string
  "role": "R_COLD_OPEN" | "R_SETUP" | "R_ESCALATION" | "R_TURN" | "R_PAYOFF" | "R_BRIDGE"
  "delivery": "D_SCENE" | "D_SUMMARY"
  "transition": "X_START" | "X_CONTINUE" | "X_EARLIER" | "X_LATER" | "X_PARALLEL" | "X_RETURN"
  "context_beat_aliases": (string)[]
  "turn_beat_alias": string
  "candidate_scene_id": string
}

export type BlindChapterWeightResponse = {
  "chapter_ordinal": number
  "weight": "CH_MAJOR" | "CH_ABOVE" | "CH_EVEN" | "CH_BELOW" | "CH_MINOR"
}

export type BlindLocalGoalResponse = {
  "intent": "G_ESTABLISH" | "G_ADVANCE" | "G_OPEN" | "G_CLOSE" | "G_WITHHOLD" | "G_RESOLVE"
  "subject_aliases": (string)[]
  "anchor_beat_alias": string
  "anchor_scene_ref": string
}

export type BlindPresentationDelivery = {
  "scene_ref": string
  "presentation_instruction": string | null
}

export type BlindStructureDecision = {
  "mode": "retain" | "reconstruct"
  "reason": string
  "declared_change_kinds": ("entry_anchor" | "ending_anchor" | "presentation_grouping" | "disclosure_order" | "noncontiguous_regroup")[]
}

export type BlueprintChapterProgress = {
  "chapter_ordinal": number
  "run_id": string | null
  "checkpoint_state": "missing" | "current" | "expired" | "unver"
  "processing_state": "pending" | "generated" | "mechanical" | "history_adopted" | "cached" | "failed" | "unknown" | null
  "reason": string | null
}

export type BlueprintContext = {
  "architecture": "plot-blueprint-v1"
  "source_format": "numbered-paragraphs-v1"
  "global_projection_contract": "plot-global-scope-v1"
}

export type BlueprintProvenance = {
  "code": string
  "root_clean_sha1": string
  "inputs": {
    [key: string]: InputEdge
  }
  "usage": Usage | null
  "revalidated": boolean | null
  "gates_skipped": (string)[] | null
  "origin": "human" | null
  "input_basis": string | null
  "partial_rewrite": "--recheck" | "--renormalize" | "--identity" | "--apply-edit" | "--adopt-residual" | null
  "invalid_baseline_sha256": string | null
  "revalidation_audit": RevalidationAudit | null
  "prompts": {
    [key: string]: string
  }
  "model": string
  "adopted_models": (string)[] | null
  "scope_violations": (string)[]
  "context": BlueprintContext
  "plot_revisions": (PlotRevisionRecord)[]
}

export type BlueprintRefinementCatalog = {
  "origin": "design" | "structure"
  "review_token": string
  "subjects_by_chapter": {
    [key: string]: (string)[]
  }
}

export type BlueprintRefinementRequest = {
  "origin": "design" | "structure"
  "review_token": string
  "actor": string
  "chapter_ordinal": number
  "dimension": "completeness" | "attribution" | "causal_continuity" | "epistemic_reveal" | "carry_state"
  "subject_refs": (string)[]
  "reason": string
}

export type BlueprintReviewBinding = {
  "blueprint_rel": string
  "subject_kind": "formal" | "invalid"
  "main_text_artifact_sha256": string
  "blueprint_artifact_sha256": string
  "check_report_sha256": string
  "chapter_ordinals": (number)[]
}

export type BlueprintReviewWorkpack = {
  "sid": string
  "title": string
  "read_only": true
  "auto_decisions": false
  "review_contract_version": "stage2-plot-scope-review-v1"
  "scope": string
  "review_identity_sha256": string
  "validation_seal_sha256": string
  "review_token": string
  "subject_sha256": string
  "binding_sha256": string
  "binding": BlueprintReviewBinding
  "dependency_manifest": DependencyManifest
  "knowledge_links": KnowledgeLinkReview
  "material": {
    [key: string]: unknown
  }
  "progress": PlotReviewProgress
  "check": CompactApprovalCheck
  "accounting": (AccountingLine)[]
}

export type BlueprintRunProgress = {
  "total_chapters": number | null
  "generated_chapters": number | null
  "checkpoint_chapters": number | null
  "submitted_chapters": number | null
  "artifact_state": "missing" | "formal" | "invalid" | "unreadable"
  "review": PlotReviewProgress | null
  "chapters": (BlueprintChapterProgress)[]
  "unavailable_reason": string | null
}

export type ChapterCharacter = {
  "char_id": string
  "display_name": string
  "role": string
}

export type ChapterReviewCoverage = {
  "completeness": DimensionVerdict
  "attribution": DimensionVerdict
  "causal_continuity": DimensionVerdict
  "epistemic_reveal": DimensionVerdict
  "carry_state": DimensionVerdict
}

export type CharacterRef = {
  "kind": "character"
  "char_id": string
}

export type CompactApprovalCheck = {
  "rules_scanned": number
  "counts": StatusCounts
  "provenance": ProvenanceSection
  "attention": (RuleLine)[]
  "metrics": (RuleLine)[]
}

export type CtxAddress = {
  "who": string
  "surface": string
  "by": string | null
  "context": "address" | "reference"
}

export type CtxChapterView = {
  "output_ordinal": number
  "chapter_goal": CtxGoal
  "open_hook_goal": CtxGoal
  "end_hook_goal": CtxGoal
  "semantic_contract": CtxSemanticContract
  "chapter_rewrite_brief": string
  "beats": (WriterBeat)[]
  "background_beats": (WriterBeat)[]
  "states": (WriterState)[]
  "constraints": (WriterConstraint)[]
  "global_subjects": (WriterGlobalSubject)[]
  "restricted_subject_refs": (PlotSubjectRef)[]
  "knowledge_events": (WriterKnowledgeEvent)[]
  "scenes": (CtxScene)[]
  "characters": (ChapterCharacter)[]
  "narration": (CtxNarration)[]
  "address": (CtxAddress)[]
  "from_source_ordinals": (number)[]
  "budget_chars": number
  "source_chars": number | null
}

export type CtxGoal = {
  "intent": "establish" | "advance" | "open" | "close" | "withhold" | "resolve"
  "subject_refs": (PlotSubjectRef)[]
  "anchor_beat_ref": string
  "anchor_scene_ref": string
}

export type CtxNarration = {
  "segment": string
  "grammatical_person": "first" | "second" | "third"
  "narrator": string | null
  "focalizer": string | null
  "pronoun_surfaces": (string)[] | null
}

export type CtxProvenance = {
  "code": string
  "root_clean_sha1": string
  "inputs": {
    [key: string]: InputEdge
  }
  "usage": Usage | null
  "revalidated": boolean | null
  "gates_skipped": (string)[] | null
  "origin": "human" | null
  "input_basis": string | null
  "partial_rewrite": "--recheck" | "--renormalize" | "--identity" | "--apply-edit" | "--adopt-residual" | null
  "invalid_baseline_sha256": string | null
  "revalidation_audit": RevalidationAudit | null
  "prompts": {
    [key: string]: string
  } | null
}

export type CtxScene = {
  "slot": string
  "purpose": string
  "role": "cold_open" | "setup" | "escalation" | "turn" | "payoff" | "bridge"
  "delivery": "scene" | "summary"
  "transition": "start" | "continue" | "earlier" | "later" | "parallel" | "return"
  "budget_chars": number
  "deliveries": (WriterDelivery)[]
  "context_beat_refs": (string)[]
  "turn_beat_ref": string
  "state_events": (WriterStateEvent)[]
  "knowledge_event_refs": (string)[]
  "constraint_refs": (string)[]
}

export type CtxSemanticContract = {
  "chapter_function": string
  "beat_refs": (string)[]
  "state_refs": (string)[]
  "constraint_refs": (string)[]
  "entry_state_refs": (string)[]
  "carry_out_refs": (string)[]
}

export type DependencyManifest = {
  "contract": string
  "scope": string
  "inputs": (ScopeDependency)[]
  "depends_on_scopes": (string)[]
}

export type DesignActiveConstraint = {
  "constraint_sha256": string
  "constraint": DesignRepairConstraint
  "source_decision_sha256s": (string)[]
}

export type DesignBindingInfeasibility = {
  "candidate_sha256": string
  "binding_concerns": (BindingConcern)[]
  "infeasibility_proof": (BindingInfeasibilityWitness)[]
}

export type DesignBlindChapterTruth = {
  "chapter_ordinal": number
  "chapter_function": string
  "characters": (PlannerCharacter)[]
  "beats": (PlannerBeat)[]
  "states": (PlannerState)[]
  "inherited_states": (PlannerState)[]
  "background_beats": (PlannerBackgroundBeat)[]
  "constraints": (PlannerRelation | PlannerValueConstraint)[]
  "knowledge": (PlannerKnowledge)[]
  "endpoints": (PlannerEndpoint)[]
  "entry_state_aliases": (string)[]
  "carry_out_aliases": (string)[]
  "global_subjects": (PlannerGlobalSubject)[]
}

export type DesignChapterReading = {
  "kind": "stage4_design_chapter_reading_v2"
  "truth": DesignBlindChapterTruth
  "chapter": BlindCandidateChapter
}

export type DesignChapterRepairCapability = {
  "output_ordinal": number
  "committed_rounds": number
  "request_repair_available": boolean | null
  "agent_repair_available": boolean | null
  "full_chapter_edit_available": boolean | null
}

export type DesignChapterReview = {
  [key: string]: unknown
}

export type DesignConstraintCatalog = {
  "expected_constraint_set_sha256": string
  "items": (DesignActiveConstraint)[]
}

export type DesignPreflightFailure = {
  "code": string
  "rule": string
  "chapter_ordinal": number | null
  "scene_refs": (string)[]
  "refs": (string)[]
  "repair_owner": string
  "detail": string
}

export type DesignPreflightSummary = {
  "status": "pass" | "blocked"
  "candidate_sha256": string
  "blueprint_sha256": string
  "review_contract": string
  "code_version": string
  "blocked_owners": (string)[]
  "failures": (DesignPreflightFailure)[]
  "infeasibility": (BindingInfeasibilityWitness)[]
}

export type DesignRepairConstraint = {
  "code": "entry_anchor" | "ending_anchor" | "presentation_grouping" | "disclosure_order" | "noncontiguous_regroup" | "scene_goal" | "scene_goal_exclude" | "binding_constraint_unsat" | "semantic_constraint" | "structure_delta"
  "scene_refs": (string)[]
  "opaque_aliases": (string)[]
}

export type DesignReviewBasis = {
  "review_contract": string
  "root_clean_sha1": string
  "truth_sha256": string
  "alias_algorithm": string
}

export type DesignReviewDecision = {
  "actor": string
  "reviewer_kind": "human" | "delegated_operator" | "inherited"
  "review_run_id": string
  "general_reason": string
  "candidate_sha256": string
  "aggregate_subject_sha256": string
  "binding_infeasibility": DesignBindingInfeasibility | null
  "preflight": DesignPreflightSummary | null
  "repair_capability": DesignReviewRepairCapability
  "strategy": DesignStrategyReview
  "chapters": (DesignChapterReview)[]
  "decision_sha256": string
  "decided_at": string
}

export type DesignReviewHistoryEntry = {
  "actor": string
  "reviewer_kind": "human" | "delegated_operator" | "inherited"
  "review_run_id": string
  "general_reason": string
  "review_token": string
  "strategy_decision": "accept" | "request_replan"
  "chapter_decisions": {
    [key: string]: "accept" | "request_repair"
  }
  "decision_sha256": string
  "decided_at": string
}

export type DesignReviewRepairCapability = {
  "repairable": boolean | null
  "reason_code": string | null
  "agent_repairable": boolean | null
  "agent_reason_code": string | null
  "full_chapter_editable": boolean | null
  "full_chapter_edit_reason_code": string | null
  "chapters": (DesignChapterRepairCapability)[]
}

export type DesignReviewTemplate = {
  "actor": string
  "reviewer_kind": "human" | "delegated_operator" | "inherited"
  "review_run_id": string
  "general_reason": string
  "candidate_sha256": string
  "aggregate_subject_sha256": string
  "binding_infeasibility": DesignBindingInfeasibility | null
  "preflight": DesignPreflightSummary | null
  "repair_capability": DesignReviewRepairCapability
  "strategy": DesignStrategyReview
  "chapters": (DesignChapterReview)[]
  "constraint_catalog": {
    [key: string]: DesignConstraintCatalog
  }
  "review_token": string
}

export type DesignReviewView = {
  "state": "unavailable" | "missing" | "repair_requested" | "strategy_replan_requested" | "accepted" | "expired"
  "candidate_sha256": string | null
  "aggregate_subject_sha256": string | null
  "binding_infeasibility": DesignBindingInfeasibility | null
  "preflight": DesignPreflightSummary | null
  "repair_capability": DesignReviewRepairCapability | null
  "action_review_token": string | null
  "current_decision": DesignReviewDecision | null
  "history": (DesignReviewHistoryEntry)[]
}

export type DesignStrategyReplanConstraint = {
  "kind": "replace_reconstruction_strategy" | "change_chapter_weights"
  "subject_sha256": string
  "chapter_ordinals": (number)[]
}

export type DesignStrategyReview = {
  "subject": DesignStrategySubject
  "subject_sha256": string
  "placeholder": boolean
  "template_token": string | null
  "decision": "accept" | "request_replan"
  "reason": string
  "replan_constraints": (DesignStrategyReplanConstraint)[]
  "source_decision_sha256": string | null
}

export type DesignStrategySubject = {
  "basis": DesignReviewBasis
  "reconstruction_strategy": string
  "chapter_weights": (BlindChapterWeightResponse)[]
}

export type DimensionVerdict = {
  "state": "pass" | "issue" | "unver" | "not_applicable"
  "issue_refs": (string)[]
  "reason": string | null
}

export type EdgeLine = {
  "key": string
  "state": "fresh" | "stale" | "unknown"
  "reason": "no_provenance" | "no_inputs" | "ghost" | "file_changed" | "consumed_changed" | "binding_input_contract_obsolete" | "binding_input_contract_unproven" | "consumed_undeclared" | "consumed_unresolved" | "legacy_blueprint_inputs" | "global_naming_blocked" | "match"
  "recorded_file": string | null
  "now_file": string | null
  "recorded_consumed": string | null
  "now_consumed": string | null
}

export type EvidenceAnchor = {
  "start_para": number
  "end_para": number
  "evidence_id": string
  "start": number
  "end": number
}

export type Finding = {
  "output_ordinal": number | null
  "rule": string
  "sev": "hard" | "info" | "metric" | "unver"
  "item": string
  "detail": string
  "evidence": (string)[]
  "fix_args": {
    [key: string]: string
  }
  "evidence_key": string
  "owner": "stage0" | "stage1" | "stage2" | "stage4" | "stage5" | "stage6" | "stage7" | "corpus" | null
  "actionable": boolean
  "identity_hit": boolean
  "identity_dead": boolean | null
  "converge": string
  "unresolved_rounds": number
  "wontfix": WontFixNote | null
}

export type FixRoute = {
  "node": string
  "flags": (string)[]
  "paid": boolean
  "available": boolean
  "unavailable_reason": string | null
}

export type GlobalSubjectRef = {
  "kind": "character" | "relation" | "world_rule" | "secret" | "setting" | "character_state"
  "ref": string
}

export type GroupRef = {
  "kind": "group"
  "label": string
}

export type InheritedStateEnding = {
  "state_ref": string
  "at_beat_ref": string
  "evidence_refs": (string)[]
}

export type InheritedStateEndingResponse = {
  "state_ref": string
  "at_beat_ref": string
  "evidence": (ParagraphRange)[]
}

export type InputEdge = {
  "file": string
  "consumed": string | null
}

export type JsonValue = unknown

export type KnowledgeAtBeats = {
  "subject_refs": (PlotSubjectRef)[]
  "kind": "at_beats"
  "trigger_beat_ref": string
}

export type KnowledgeAtEntry = {
  "subject_refs": (PlotSubjectRef)[]
  "kind": "at_entry"
}

export type KnowledgeLinkDecision = {
  "obligation_id": string
  "expected_subject_sha256": string
  "resolution": KnowledgeAtBeats | KnowledgeAtEntry | KnowledgeLinkUnresolved
}

export type KnowledgeLinkObligation = {
  "obligation_id": string
  "kind": "secret_reveal" | "secret_setup" | "knowledge_event" | "world_rule" | "world_rule_reveal" | "relation" | "relation_visibility" | "character_state" | "setting" | "reader_reveal" | "character_knowledge"
  "knowledge_ref": string
  "subject_ref": PlotSubjectRef
  "audience": CharacterRef | GroupRef | null
  "chapter_ordinal": number
  "subject_sha256": string
  "candidate_subject_refs": (PlotSubjectRef)[]
  "entry_subject_refs": (PlotSubjectRef)[]
}

export type KnowledgeLinkRecord = {
  "obligation_id": string
  "expected_subject_sha256": string
  "resolution": KnowledgeAtBeats | KnowledgeAtEntry | KnowledgeLinkUnresolved
  "actor": string
  "reason": string
  "source_decision_sha256": string
}

export type KnowledgeLinkReview = {
  "scope": KnowledgeLinkScope
  "inherited": (KnowledgeLinkRecord)[]
  "pending_obligation_ids": (string)[]
}

export type KnowledgeLinkScope = {
  "contract": "stage2-knowledge-links-v2"
  "chapter_ordinal": number | null
  "scope_sha256": string
  "main_text_sha256": string
  "knowledge_sha256": string
  "blueprint_sha256": string
  "obligations": (KnowledgeLinkObligation)[]
  "registered_events": (RegisteredPlotKnowledgeEvent)[]
}

export type KnowledgeLinkSubmission = {
  "scope_sha256": string
  "actor": string
  "reason": string
  "decisions": (KnowledgeLinkDecision)[]
  "event_operations": (PutPlotKnowledgeEvent | RetirePlotKnowledgeEvent)[]
}

export type KnowledgeLinkUnresolved = {
  "kind": "unresolved"
  "reason": string
}

export type NewPlotIssue = {
  "issue_key": string
  "dimension": "completeness" | "attribution" | "causal_continuity" | "epistemic_reveal" | "carry_state"
  "problem": string
  "subjects": (PlotSubjectRef)[]
  "evidence_refs": (ReviewEvidenceRef)[]
}

export type ParagraphRange = {
  "start_para": number
  "end_para": number
}

export type PlanChapter = {
  "output_ordinal": number
  "from_source_ordinals": (number)[]
  "chapter_goal": PlanGoal
  "open_hook_goal": PlanGoal
  "end_hook_goal": PlanGoal
  "structure_decision": StructureDecision
  "chapter_rewrite_brief": string
  "scenes": (PlanScene)[]
  "beat_placements": (BeatPlacement)[]
  "anchor_bindings": (PlotAnchorBinding)[]
}

export type PlanChapterLine = {
  "output_ordinal": number
  "from_source_ordinals": (number)[]
  "chapter_goal": PlanGoal
  "open_hook_goal": PlanGoal
  "end_hook_goal": PlanGoal
  "structure_decision": StructureDecisionView
  "chapter_rewrite_brief": string
  "scenes": (PlanSceneLine)[]
  "beat_placements": (BeatPlacement)[]
  "anchor_bindings": (PlotAnchorBinding)[]
  "semantic_contract": SemanticContract
}

export type PlanGoal = {
  "intent": "establish" | "advance" | "open" | "close" | "withhold" | "resolve"
  "subject_refs": (PlotSubjectRef)[]
  "anchor_beat_ref": string
  "anchor_scene_ref": string
}

export type PlanProvenance = {
  "code": string
  "root_clean_sha1": string
  "inputs": {
    [key: string]: InputEdge
  }
  "usage": Usage | null
  "revalidated": boolean | null
  "gates_skipped": (string)[] | null
  "origin": "compiled" | "human"
  "input_basis": string | null
  "partial_rewrite": "--recheck" | "--renormalize" | "--identity" | "--apply-edit" | "--adopt-residual" | null
  "invalid_baseline_sha256": string | null
  "revalidation_audit": RevalidationAudit | null
  "prompts": {
    [key: string]: string
  } | null
  "blind_candidate_sha256": string | null
  "evidence_binding_sha256": string | null
}

export type PlanScene = {
  "slot": string
  "budget_chars": number
  "purpose": string
  "role": "cold_open" | "setup" | "escalation" | "turn" | "payoff" | "bridge"
  "delivery": "scene" | "summary"
  "transition": "start" | "continue" | "earlier" | "later" | "parallel" | "return"
  "context_beat_refs": (string)[]
  "turn_beat_ref": string
}

export type PlanSceneLine = {
  "slot": string
  "budget_chars": number
  "purpose": string
  "role": "cold_open" | "setup" | "escalation" | "turn" | "payoff" | "bridge"
  "delivery": "scene" | "summary"
  "transition": "start" | "continue" | "earlier" | "later" | "parallel" | "return"
  "context_beat_refs": (string)[]
  "turn_beat_ref": string
  "i": number
  "beat_refs": (string)[]
}

export type PlannerAudience = {
  "kind": "character" | "group"
  "char_id": string | null
  "label": string | null
}

export type PlannerBackgroundBeat = {
  "alias": string
  "content": string
  "participant_aliases": (string)[]
}

export type PlannerBeat = {
  "alias": string
  "content": string
  "participant_aliases": (string)[]
}

export type PlannerCharacter = {
  "alias": string
  "canonical": string
  "surfaces": (string)[]
  "role": string
}

export type PlannerEndpoint = {
  "endpoint_alias": string
  "kind": "state" | "constraint" | "knowledge"
  "endpoint": "established" | "ended" | "before" | "after" | "target" | "trigger"
  "trigger_beat_alias": string
  "subject_alias": string
}

export type PlannerGlobalSubject = {
  "alias": string
  "kind": "relation" | "world_rule" | "secret" | "setting" | "character_state"
  "value": JsonValue
}

export type PlannerKnowledge = {
  "event_alias": string
  "subject_alias": string
  "kind": "secret_reveal" | "secret_setup" | "knowledge_event" | "world_rule" | "world_rule_reveal" | "relation" | "relation_visibility" | "character_state" | "setting" | "reader_reveal" | "character_knowledge"
  "audience": PlannerAudience | null
  "timing": "at_entry" | "at_beats" | "future_chapter"
  "trigger_beat_alias": string | null
}

export type PlannerRelation = {
  "alias": string
  "kind": "cause" | "story_before" | "response_to" | "same_scene" | "observes"
  "before_beat_alias": string
  "after_beat_alias": string
  "observer_alias": string | null
}

export type PlannerState = {
  "alias": string
  "content": string | null
  "modality": "world" | "belief" | "claim" | "unresolved"
  "holder_alias": string | null
  "global_subject_alias": string | null
  "established_at_entry": boolean | null
  "supersedes_state_aliases": (string)[]
}

export type PlannerValueConstraint = {
  "alias": string
  "kind": "literal" | "value" | "object"
  "target_beat_alias": string
  "value": string
}

export type PlotAnchorBinding = {
  "kind": "state" | "constraint" | "knowledge" | "goal"
  "ref": string
  "endpoint": "established" | "ended" | "before" | "after" | "target" | "trigger" | "open" | "close" | "turn" | "chapter"
  "scene_ref": string
}

export type PlotBeat = {
  "beat_id": string
  "content": string
  "participant_char_ids": (string)[]
  "evidence_refs": (string)[]
  "source_contexts": (SourceContext)[]
}

export type PlotBeatResponse = {
  "beat_key": string
  "content": string
  "participant_char_ids": (string)[]
  "evidence": (ParagraphRange)[]
  "source_contexts": (SourceContextResponse)[]
}

export type PlotBlueprint = {
  "chapter_ordinal": number
  "source_sha256": string
  "chapter_function": string
  "beats": (PlotBeat)[]
  "evidence_anchors": (EvidenceAnchor)[]
  "states": (PlotState)[]
  "inherited_state_endings": (InheritedStateEnding)[]
  "constraints": (PlotRelationConstraint | PlotValueConstraint)[]
  "entry_state_refs": (string)[]
  "carry_out_refs": (string)[]
  "narration": (PlotNarration)[]
}

export type PlotChapterEdit = {
  "chapter": PlotChapterResponse
  "identities": (PlotIdentityOperation)[]
}

export type PlotChapterResponse = {
  "chapter_ordinal": number
  "chapter_function": string
  "beats": (PlotBeatResponse)[]
  "states": (PlotStateResponse)[]
  "inherited_state_endings": (InheritedStateEndingResponse)[]
  "constraints": (PlotRelationConstraintResponse | PlotValueConstraintResponse)[]
  "entry_state_refs": (string)[]
  "carry_out_refs": (string)[]
  "narration": (PlotNarration)[]
}

export type PlotChapterReviewMaterials = {
  "contract": "plot-chapter-evidence-v1"
  "blueprint": PlotBlueprint
  "support_subjects": (ReviewSupportSubject)[]
  "global_subjects": (ReviewSupportSubject)[]
  "knowledge_events": ({
    [key: string]: JsonValue
  })[]
  "source_context_catalog": {
    [key: string]: JsonValue
  }
  "evidence_anchors": (ReviewEvidenceAnchor)[]
  "source_materials": (ReviewSourceChapter)[]
}

export type PlotEdit = {
  "actor": string
  "reason": string
  "review_token": string
  "chapters": (PlotChapterEdit)[]
}

export type PlotEditPreview = {
  "review_token": string
  "result_subject_sha256": string
  "changed_chapters": (number)[]
  "changes": (PlotIdentityChange)[]
  "tombstones": (PlotNodeTombstone)[]
  "hard_hits": (string)[]
  "would_apply": boolean
}

export type PlotIdentityChange = {
  "chapter_ordinal": number
  "collection": "beats" | "states" | "constraints"
  "kind": "preserve" | "replace" | "add"
  "old_refs": (string)[]
  "new_refs": (string)[]
}

export type PlotIdentityOperation = {
  "collection": "beats" | "states" | "constraints"
  "kind": "preserve" | "replace" | "add"
  "old_refs": (string)[]
  "new_keys": (string)[]
}

export type PlotIssueDisposition = {
  "issue_id": string
  "state": "open" | "resolved" | "withdrawn"
  "reason": string
}

export type PlotKnowledgeEvent = {
  "kind": "reader_reveal" | "character_knowledge"
  "subject_ref": PlotSubjectRef
  "audience": CharacterRef | GroupRef | null
  "chapter_ordinal": number
  "evidence_refs": (PlotKnowledgeEvidence)[]
}

export type PlotKnowledgeEvidence = {
  "chapter_ordinal": number
  "evidence_ref": string
}

export type PlotNarration = {
  "grammatical_person": "first" | "second" | "third"
  "narrator_char_id": string | null
  "focalizer_char_id": string | null
  "pronoun_surfaces": (string)[]
}

export type PlotNodeTombstone = {
  "node_id": string
  "subject_sha256": string
  "superseded_by": (string)[]
  "reason": string
}

export type PlotRelationConstraint = {
  "constraint_id": string
  "kind": "cause" | "story_before" | "response_to" | "same_scene" | "observes"
  "before_beat_ref": string
  "after_beat_ref": string
  "observer_char_id": string | null
  "evidence_refs": (string)[]
}

export type PlotRelationConstraintResponse = {
  "constraint_key": string
  "kind": "cause" | "story_before" | "response_to" | "same_scene" | "observes"
  "before_beat_ref": string
  "after_beat_ref": string
  "observer_char_id": string | null
  "evidence": (ParagraphRange)[]
}

export type PlotReviewProgress = {
  "total_chapters": number
  "generated_chapters": number
  "accepted_chapters": number
  "next_scope": string | null
  "scopes": (PlotScopeStatus)[]
  "unresolved_issues": number
  "accepted": boolean
}

export type PlotReviewResponse = {
  "decision": "accept" | "revise" | "unver"
  "reason": string
  "coverage": ChapterReviewCoverage
  "new_issues": (NewPlotIssue)[]
  "dispositions": (PlotIssueDisposition)[]
}

export type PlotRevisionRecord = {
  "request_sha256": string
  "before_subject_sha256": string
  "result_subject_sha256": string
  "actor": string
  "reason": string
  "changes": (PlotIdentityChange)[]
  "tombstones": (PlotNodeTombstone)[]
}

export type PlotScopeStatus = {
  "scope": string
  "state": "missing" | "accepted" | "expired" | "rework" | "uncertain" | "stopped"
  "subject_sha256": string | null
  "decision_sha256": string | null
  "open_issue_ids": (string)[]
}

export type PlotState = {
  "state_id": string
  "content": string | null
  "global_ref": GlobalSubjectRef | null
  "modality": "world" | "belief" | "claim" | "unresolved"
  "holder_char_id": string | null
  "established_at": BeatMoment
  "ended_at": BeatMoment | null
  "supersedes_state_refs": (string)[]
  "evidence_refs": (string)[]
}

export type PlotStateResponse = {
  "state_key": string
  "content": string | null
  "global_ref": GlobalSubjectRef | null
  "modality": "world" | "belief" | "claim" | "unresolved"
  "holder_char_id": string | null
  "established_at": BeatMomentResponse
  "ended_at": BeatMomentResponse | null
  "supersedes_state_refs": (string)[]
  "evidence": (ParagraphRange)[]
}

export type PlotSubjectRef = {
  "kind": "beat" | "state" | "constraint" | "character" | "relation" | "world_rule" | "secret" | "setting" | "character_state" | "chapter_boundary"
  "ref": string
}

export type PlotValueConstraint = {
  "constraint_id": string
  "kind": "literal" | "value" | "object"
  "target_beat_ref": string
  "value": string
  "evidence_refs": (string)[]
}

export type PlotValueConstraintResponse = {
  "constraint_key": string
  "kind": "literal" | "value" | "object"
  "target_beat_ref": string
  "value": string
  "evidence": (ParagraphRange)[]
}

export type PresentationDelivery = {
  "scene_ref": string
  "presentation_instruction": string | null
  "evidence_refs": (string)[]
  "source_context_refs": (string)[]
}

export type ProvenanceSection = {
  "freshness": "fresh" | "stale" | "unknown"
  "reason": "no_provenance" | "no_inputs" | "ghost" | "file_changed" | "consumed_changed" | "binding_input_contract_obsolete" | "binding_input_contract_unproven" | "consumed_undeclared" | "consumed_unresolved" | "legacy_blueprint_inputs" | "global_naming_blocked" | "match"
  "ghosts": (string)[]
  "edges": (EdgeLine)[]
  "invalid": boolean
  "revalidated": boolean | null
  "origin": string | null
}

export type PutPlotKnowledgeEvent = {
  "operation": "put"
  "event_id": string
  "expected_event_sha256": string | null
  "event": PlotKnowledgeEvent
  "resolution": KnowledgeAtBeats | KnowledgeLinkUnresolved
}

export type ReadingView = {
  "sid": string
  "title": string
  "chapter_ordinal": number
  "text": string
  "text_chars": number
  "blueprint": PlotBlueprint | null
  "blueprint_state": ArtifactState
  "_provenance": BlueprintProvenance | null
}

export type RegisteredPlotKnowledgeEvent = {
  "event_id": string
  "event_sha256": string
  "event": PlotKnowledgeEvent
  "retired": boolean
  "reason": string | null
}

export type RetirePlotKnowledgeEvent = {
  "operation": "retire"
  "event_id": string
  "expected_event_sha256": string
  "reason": string
}

export type RevalidationAudit = {
  "actor": string
  "reason": string
  "baseline_locator_sha256": string
  "baseline_raw_sha256": string
  "candidate_raw_sha256": string
  "baseline_canonical_sha256": string
  "candidate_canonical_sha256": string
  "raw_changed": boolean
  "content_changed": boolean
  "edited_by_human": boolean
  "changed_json_pointers": (string)[]
}

export type ReviewEvidenceAnchor = {
  "evidence_ref": string
  "chapter_ordinal": number
  "start": number
  "end": number
}

export type ReviewEvidenceRef = {
  "chapter_ordinal": number
  "evidence_ref": string
}

export type ReviewSourceChapter = {
  "chapter_ordinal": number
  "ranges": (ReviewSourceRange)[]
}

export type ReviewSourceRange = {
  "start": number
  "end": number
  "text": string
}

export type ReviewSupportSubject = {
  "subject_ref": PlotSubjectRef
  "value": JsonValue
}

export type RuleLine = {
  "rule": string
  "sev": string
  "status": "ok" | "hit" | "unver" | "skipped"
  "item": string
  "requirement": string
  "check_kind": "mechanical" | "semantic"
  "remediation": "llm" | "human" | "deterministic" | "none"
  "fix": FixRoute | null
  "findings": (Finding)[]
  "why": string | null
  "scanned": string | null
  "baseline": string | null
  "note": string | null
}

export type ScopeDependency = {
  "selection": ScopeSelection
  "consumed_sha256": string
}

export type ScopeSelection = {
  "input_rel": string
  "projection": "plot_chapter" | "source_chapter" | "collection_members" | "plot_global" | "plot_inherited_states" | "source_ranges" | "plot_global_review" | "plot_subjects" | "plot_characters"
  "chapter_ordinal": number | null
  "path": (string)[]
  "key_field": string | null
  "selected_ids": (string)[] | null
}

export type SemanticContract = {
  "chapter_function": string
  "beat_refs": (string)[]
  "state_refs": (string)[]
  "constraint_refs": (string)[]
  "entry_state_refs": (string)[]
  "carry_out_refs": (string)[]
}

export type SourceContext = {
  "evidence_refs": (string)[]
  "time_layer": "present" | "recall" | "hearsay" | null
  "place": string | null
  "present_char_ids": (string)[] | null
}

export type SourceContextResponse = {
  "evidence": (ParagraphRange)[]
  "time_layer": "present" | "recall" | "hearsay" | null
  "place": string | null
  "present_char_ids": (string)[] | null
}

export type Stage4IndexRow = {
  "output_ordinal": number
  "chapter_goal": string
  "structure_decision": StructureDecisionView
  "budget_chars": number | null
  "scenes": number
}

export type Stage4View = {
  "sid": string
  "title": string
  "identity_plan": boolean
  "structure_subject_sha256": string | null
  "structure_subject_unavailable_reason": string | null
  "blueprint": PlotBlueprint
  "chapters": (number)[]
  "ordinal": number
  "index": (Stage4IndexRow)[]
  "plan_chapter": PlanChapterLine
  "ctx_chapter": CtxChapterView | null
  "writer_target": [number, number] | null
  "plan_state": ArtifactState
  "ctx_state": ArtifactState
  "_provenance": PlanProvenance | null
  "_ctx_provenance": CtxProvenance | null
}

export type StatusCounts = {
  "ok": number
  "hit": number
  "unver": number
  "skipped": number
}

export type StructureChapterDecision = {
  [key: string]: unknown
}

export type StructureChapterReading = {
  "kind": "stage4_structure_chapter_reading_v2"
  "materials": PlotChapterReviewMaterials
  "chapter": PlanChapter
  "structure_metrics": {
    [key: string]: unknown
  }
  "binding_concerns": (WorkpackConcernReading)[]
  "signature_conflict_codes": (string)[]
}

export type StructureDecision = {
  "mode": "retain" | "reconstruct"
  "reason": string
  "declared_change_kinds": ("entry_anchor" | "ending_anchor" | "presentation_grouping" | "disclosure_order" | "noncontiguous_regroup")[]
}

export type StructureDecisionView = {
  "mode": "retain" | "reconstruct"
  "reason": string
  "declared_change_kinds": ("entry_anchor" | "ending_anchor" | "presentation_grouping" | "disclosure_order" | "noncontiguous_regroup")[]
  "computed_change_kinds": ("entry_anchor" | "ending_anchor" | "presentation_grouping" | "disclosure_order" | "noncontiguous_regroup")[]
}

export type Usage = {
  "calls_made": number
  "calls_cached": number
  "prompt_tokens": number | null
  "completion_tokens": number | null
  "reasoning_tokens": number | null
}

export type WontFixNote = {
  "why": string
  "at": string
}

export type WorkpackClosedRefReading = {
  "ref": string
  "kind": "beat" | "state" | "constraint" | "evidence" | "knowledge" | "character" | "global" | "unknown"
  "text": string | null
}

export type WorkpackConcernReading = {
  "constraint_code": string
  "chapter_ordinal": number | null
  "scenes": (string)[]
  "reason": string
  "closed_refs": (WorkpackClosedRefReading)[]
}

export type WorkpackGroup = {
  "group": "blocking" | "semantic_attention" | "disclosure" | "metric_only" | "phase_deferred" | "coverage"
  "scanned": string
  "items": (WorkpackItem)[]
}

export type WorkpackItem = {
  "code": string
  "rule": string | null
  "sev": "hard" | "info" | "metric" | "unver" | null
  "status": "ok" | "hit" | "unver" | "skipped" | null
  "chapter_ordinal": number | null
  "scene_refs": (string)[]
  "refs": (string)[]
  "repair_owner": string | null
  "detail": string
  "effect": "block_b" | "block_context" | "attention" | "disclose" | "read_only" | "phase_deferred" | "coverage"
  "allowed_actions": ("accept" | "request_repair" | "request_replan")[]
  "changed": boolean
  "historical_accept_reusable": boolean
  "source_decision_sha256": string | null
  "review_token": string | null
  "reading": DesignChapterReading | StructureChapterReading | null
  "inherited_structure_decision": StructureChapterDecision | null
}

export type WorkpackReading = {
  "kind": "stage4_workpack_reading_v2"
  "legend": {
    [key: string]: string
  }
  "reconstruction_strategy": string | null
  "chapter_weights": ({
    [key: string]: unknown
  })[] | null
  "global_binding_concerns": (WorkpackConcernReading)[]
}

export type WriterBeat = {
  "beat_ref": string
  "content": string
  "participant_char_ids": (string)[]
}

export type WriterConstraint = {
  "constraint_ref": string
  "kind": "cause" | "story_before" | "response_to" | "same_scene" | "observes" | "literal" | "value" | "object"
  "before_beat_ref": string | null
  "after_beat_ref": string | null
  "observer_char_id": string | null
  "target_beat_ref": string | null
  "value": string | null
}

export type WriterDelivery = {
  "beat_ref": string
  "presentation_instruction": string | null
  "stages": (WriterStage)[]
}

export type WriterGlobalSubject = {
  "subject_ref": GlobalSubjectRef
  "value": JsonValue
  "allowed_slots": (string)[]
  "state_permissions": (WriterStatePermission)[]
}

export type WriterKnowledgeEvent = {
  "event_ref": string
  "kind": "secret_reveal" | "secret_setup" | "knowledge_event" | "world_rule" | "world_rule_reveal" | "relation" | "relation_visibility" | "character_state" | "setting" | "reader_reveal" | "character_knowledge"
  "subject_ref": PlotSubjectRef
  "audience": CharacterRef | GroupRef | null
  "timing": "at_entry" | "at_slot"
  "slot": string | null
  "allowed_slots": (string)[]
}

export type WriterStage = {
  "time_layer": "present" | "recall" | "hearsay" | null
  "place": string | null
  "present": (string)[] | null
}

export type WriterState = {
  "state_ref": string
  "content": string | null
  "global_ref": GlobalSubjectRef | null
  "modality": "world" | "belief" | "claim" | "unresolved"
  "holder_char_id": string | null
}

export type WriterStateEvent = {
  "state_ref": string
  "endpoint": "established" | "ended"
}

export type WriterStatePermission = {
  "state_ref": string
  "modality": "belief" | "claim" | "unresolved"
  "allowed_slots": (string)[]
}
