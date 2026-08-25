import type { GlobalFactEvidence, Stage2Global } from '@/types/stages'

export const SPOILER_PLACEHOLDER = '（核心剧透字段已隐藏）'
export const MASKED_SECRET_ID = '__display_masked__'

/**
 * Display-only projection. The server intentionally sends the complete stage2 document;
 * this adapter prevents Screen components from receiving the configured raw fields or
 * the source text attached to their evidence.
 * Browser/network/devtools visibility is unchanged, so this is not a security boundary.
 */
function maskEvidence<T extends GlobalFactEvidence | null>(evidence: T): T {
  if (evidence === null || evidence.source === null) return evidence
  return {
    ...evidence,
    source: {
      ...evidence.source,
      text: SPOILER_PLACEHOLDER,
    },
  } as T
}

export function projectGlobalSpoilers(raw: Stage2Global, reveal: boolean): Stage2Global {
  if (reveal) return raw
  return {
    ...raw,
    characters: raw.characters.map((character) => ({
      ...character,
      fate: SPOILER_PLACEHOLDER,
      fate_evidence: maskEvidence(character.fate_evidence),
    })),
    secrets: [
      {
        secret_id: MASKED_SECRET_ID,
        fact: SPOILER_PLACEHOLDER,
        fact_evidence: { chapter_ordinal: 1, source: null },
        knowledge_events: [],
        setup_kind: 'cold',
        setup_chapters: [],
        first_reveal_chapter: 1,
      },
    ],
    arc: {
      ...raw.arc,
      climax: SPOILER_PLACEHOLDER,
      ending: SPOILER_PLACEHOLDER,
      arc_tracks: raw.arc.arc_tracks.map((track) => ({
        ...track,
        trajectory_evidence: maskEvidence(track.trajectory_evidence),
      })),
    },
  }
}
