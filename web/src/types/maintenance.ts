export interface RetentionPolicy {
  log_ttl_seconds: number | null
  telemetry_ttl_seconds: number | null
  trash_ttl_seconds: number | null
  checkpoint_ttl_seconds: number | null
  max_entries: number
}

export interface RetentionCandidate {
  kind: 'log' | 'operator_telemetry' | 'trash' | 'checkpoint'
  sid: string
  rel: string
  observed_mtime_ns: number
  observed_size: number
  observed_sha256: string
  evidence: Record<string, unknown>
}

export interface RetentionPlan {
  wire_version: 1
  generated_at: number
  policy: RetentionPolicy
  examined: number
  candidates: RetentionCandidate[]
  plan_token: string
}

export interface RetentionApplyResult {
  plan_token: string
  removed: string[]
  receipts: string[]
}

export interface TrashRestorePlan {
  wire_version: 1
  generated_at: number
  trash_id: string
  sid: string
  title: string
  original_dirname: string
  metadata_sha256: string
  manifest_file_sha256: string
  manifest_sha256: string
  plan_token: string
}

export interface TrashRestoreResult {
  sid: string
  title: string
  trash_id: string
  book_rel: string
}
