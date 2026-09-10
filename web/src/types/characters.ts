import type { ArtifactState, GlobalAddress, GlobalCharacter } from './stages'
import type { Provenance } from './common'

export type RegisteredCharacter = Omit<GlobalCharacter, 'char_id' | 'fate' | 'fate_evidence' | 'address'> & {
  address: (Omit<GlobalAddress, 'by'> & { by: string })[]
}

export type EditableCharacter = Omit<RegisteredCharacter, 'role_evidence'> & {
  role_evidence: { chapter_ordinal: number; locator: string }
}

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

export interface CharacterRegistryView {
  sid: string
  source_sha256: string
  artifact: ArtifactState
  registry: { characters: Record<string, RegisteredCharacter>; _provenance: Provenance }
  edit_template: CharactersEdit | null
  template_unavailable_reason: string | null
}
