import type { Stage2Global } from '@/types/stages'

export const SPOILER_PLACEHOLDER = '（核心剧透字段已隐藏）'
export const MASKED_SECRET_ID = '__display_masked__'

/** 仅做展示遮蔽。完整响应已到达浏览器，关系和规则也可能暗示同一事实。 */
export function projectGlobalSpoilers(raw: Stage2Global, reveal: boolean): Stage2Global {
  if (reveal) return raw
  const visibleRefs = new Set([
    ...raw.setting.era_evidence_refs,
    ...raw.setting.social_context_evidence_refs,
    ...raw.relations.flatMap((row) => row.evidence_refs),
    ...raw.world_rules.flatMap((rule) => rule.versions.flatMap((row) => row.evidence_refs)),
  ])
  return {
    ...raw,
    arc_secrets: [{
      secret_id: MASKED_SECRET_ID,
      fact: SPOILER_PLACEHOLDER,
      fact_ref: null,
      evidence_refs: [],
      setup_chapters: [],
      revealed_from: 1,
    }],
    evidence_anchors: Object.fromEntries(Object.entries(raw.evidence_anchors).filter(([ref]) => visibleRefs.has(ref))),
    evidence_snippets: Object.fromEntries(Object.entries(raw.evidence_snippets).filter(([ref]) => visibleRefs.has(ref))),
  }
}
