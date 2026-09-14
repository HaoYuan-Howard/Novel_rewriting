/**
 * 由 scripts/export_backend_types.py 从 contracts/invariants.py 生成；
 * 修改取值域后重新生成，勿手改。Op / DotShape 等显示层联合不在此列。
 */
export const FRESHNESS = ['fresh', 'stale', 'unknown'] as const
export type Freshness = (typeof FRESHNESS)[number]

export const ARTIFACT_DISPLAY = ['missing', 'fresh', 'stale', 'unknown'] as const
export type FreshnessDisplay = (typeof ARTIFACT_DISPLAY)[number]

export const BADGES = ['pass', 'amber', 'fail'] as const
export type Badge = (typeof BADGES)[number]

export const SEVERITIES = ['hard', 'info', 'metric', 'unver'] as const
export type Severity = (typeof SEVERITIES)[number]

export const JOB_STATES = ['running', 'ok', 'landed_red', 'failed', 'unknown'] as const
export type JobState = (typeof JOB_STATES)[number]

export const INBOX_KINDS = ['invalid', 'rejected', 'stalled', 'repairable', 'human_only', 'verification', 'upstream', 'approval'] as const
export type InboxKind = (typeof INBOX_KINDS)[number]

export const STAGE_IDS = ['stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7'] as const
export type StageId = (typeof STAGE_IDS)[number]
