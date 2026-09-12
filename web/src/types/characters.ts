import type { ArtifactState, CoreEvidenceAnchors, CoreEvidenceSnippets } from './stages'
import type { Provenance } from './common'

export interface CharacterNameBinding<Ref = string> {
  surface: string
  identity_revealed_from: number
  evidence_refs: Ref[]
}

export interface RegisteredCharacter<Ref = string> {
  canonical: string
  aliases: CharacterNameBinding<Ref>[]
  first_chapter: number
  first_appearance_evidence_refs: Ref[]
  role: string
  role_evidence_refs: Ref[]
  name_distinctiveness: 'high' | 'medium' | 'common'
}

export type EditableCharacter = RegisteredCharacter<string | { chapter_ordinal: number; locator: string }>

export interface CharactersEdit {
  source_sha256: string
  operations: (
    | { kind: 'add' | 'replace'; char_id: string; character: EditableCharacter }
    | { kind: 'delete'; char_id: string; reason: string }
  )[]
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
  } | null
}

export interface CharacterRegistryView {
  sid: string
  source_sha256: string
  artifact: ArtifactState
  registry: {
    characters: Record<string, RegisteredCharacter>
    evidence_anchors: CoreEvidenceAnchors
    _provenance: CharactersProvenance
  }
  evidence_snippets: CoreEvidenceSnippets
  edit_template: CharactersEdit | null
  template_unavailable_reason: string | null
}
