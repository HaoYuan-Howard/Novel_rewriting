import type { Stage2Global } from '@/types/stages'

export const SPOILER_PLACEHOLDER = '（核心剧透字段已隐藏）'
export const MASKED_SECRET_ID = '__display_masked__'

/**
 * Display-only projection. The server intentionally sends the complete stage2 document;
 * this adapter prevents Screen components from receiving the four configured raw fields.
 * Browser/network/devtools visibility is unchanged, so this is not a security boundary.
 */
export function projectGlobalSpoilers(raw: Stage2Global, reveal: boolean): Stage2Global {
  if (reveal) return raw
  return {
    ...raw,
    characters: raw.characters.map((character) => ({
      ...character,
      fate: SPOILER_PLACEHOLDER,
    })),
    secrets: [
      {
        secret_id: MASKED_SECRET_ID,
        fact: SPOILER_PLACEHOLDER,
        group_id: null,
        known_by: [],
        setup_kind: 'cold',
        setup_chapters: [],
        first_reveal_chapter: 0,
        reader_reveal_chapters: [],
      },
    ],
    arc: {
      ...raw.arc,
      climax: SPOILER_PLACEHOLDER,
      ending: SPOILER_PLACEHOLDER,
    },
  }
}
