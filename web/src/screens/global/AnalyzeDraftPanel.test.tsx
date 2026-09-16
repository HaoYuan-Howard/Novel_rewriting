import { describe, expect, it } from 'vitest'
import { mockApi, SAMPLE_SID } from '@/api/mock'

/**
 * 批次 C：人工 analyze-draft 写面退役。面板组件保留源文件仅供历史对照，
 * GlobalScreen 不再挂载；写 API 固定 405/unsupported。
 */
describe('AnalyzeDraft write surface retired (batch C)', () => {
  it('rejects editAnalyzeDraft as unsupported 405', async () => {
    await expect(mockApi.editAnalyzeDraft(SAMPLE_SID, {
      actor: 'x', reason: 'y',
      edit: {
        owner: 'stage2.characters',
        source_kind: 'analyze_owner_draft',
        source_receipt_sha256: 'a'.repeat(64),
        source_draft_sha256: 'b'.repeat(64),
        source_mapping_sha256: 'c'.repeat(64),
        character_bindings: {},
        characters: { characters: [] },
        global_draft: null,
      },
    })).rejects.toMatchObject({ status: 405 })
  })
})
