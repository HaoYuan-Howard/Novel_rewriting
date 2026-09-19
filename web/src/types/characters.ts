import type { ArtifactState, CoreEvidenceAnchors, CoreEvidenceSnippets } from './stages'
import type { Provenance } from './common'

export type MentionKind =
  | 'proper_name'
  | 'short_name'
  | 'name_bearing_address'
  | 'relational_address'
  | 'social_address'
  | 'descriptive'
  | 'pronoun'
  | 'source_typo'

export type MentionDisposition = 'bound' | 'excluded' | 'unresolved'

export type CharacterIdentityStatus = 'confirmed' | 'unresolved_surface'

export interface SurfaceRecord {
  surface: string
  kind: MentionKind
  identity_revealed_from: number | null
}

export interface RegisteredCharacter {
  canonical_surface: string
  status: CharacterIdentityStatus
  first_mention_chapter: number
  name_usable_from: number | null
  surfaces: SurfaceRecord[]
  name_distinctiveness: 'high' | 'medium' | 'common'
}

export interface MentionRecord {
  mention_id: string
  chapter_ordinal: number
  start: number
  end: number
  surface: string
  kind: MentionKind
  disposition: MentionDisposition
  char_id: string | null
  exclude_reason: string | null
  reveals_identity: boolean
  evidence_refs: string[]
}

export type CharacterOperation =
  | {
      kind: 'add'
      character_ref: string
      canonical_surface: string
      status?: CharacterIdentityStatus
      seed_surface?: string | null
      seed_kind?: MentionKind
    }
  | {
      kind: 'replace'
      char_id: string
      canonical_surface: string
      status?: CharacterIdentityStatus
    }
  | { kind: 'delete'; char_id: string; reason: string }
  | {
      kind: 'rebind_mentions'
      mention_ids: string[]
      disposition: MentionDisposition
      char_id?: string | null
      mention_kind: MentionKind
      exclude_reason?: string | null
    }

export interface CharactersEdit {
  source_sha256: string
  operations: CharacterOperation[]
}

export interface CharactersEditRequest {
  actor: string
  reason: string
  edit: CharactersEdit
}

export interface CharactersProvenance extends Provenance {
  retired_character_ids: string[]
  characters_edit: {
    actor: string
    reason: string
    at: string
    source_sha256: string
    edit_sha256: string
    deleted_character_ids: string[]
    added_character_ids: Record<string, string>
  } | null
}

export interface CharacterRegistryView {
  sid: string
  source_sha256: string
  artifact: ArtifactState
  registry: {
    characters: Record<string, RegisteredCharacter>
    mentions: Record<string, MentionRecord>
    evidence_anchors: CoreEvidenceAnchors
    _provenance: CharactersProvenance
  }
  evidence_snippets: CoreEvidenceSnippets
  edit_template: CharactersEdit | null
  template_unavailable_reason: string | null
}
