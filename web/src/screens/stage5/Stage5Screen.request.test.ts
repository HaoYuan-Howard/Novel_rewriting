import { describe, expect, it } from 'vitest'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { chapterSelectionArgs } from './Stage5Screen'

describe('Stage5 dynamic chapter request', () => {
  it('adds the value without duplicating an action-owned --chapters token', () => {
    const extra = ['--chapters']
    const flags = [...extra, ...chapterSelectionArgs(extra, ' 11 ')]

    expect(flags).toEqual(['--chapters', '11'])
    expect(flags.filter((token) => token === '--chapters')).toHaveLength(1)
  })

  it('adds the complete pair when the selected action does not own --chapters', () => {
    expect(chapterSelectionArgs(null, '3,7')).toEqual(['--chapters', '3,7'])
  })

  it('produces one exact --chapters token in the mock preview command', async () => {
    const extra = ['--chapters']
    const preview = await mockApi.previewJob({
      kind: 'stage5.write',
      label: 'stage5 逐章写作（deep） --chapters',
      mode: 'deep',
      extra,
      args: chapterSelectionArgs(extra, '11'),
      sid: SAMPLE_SID,
      ordinal: null,
    })
    const command = preview.steps[0]?.argv ?? ''

    expect(command).toContain('--mode deep --chapters 11')
    expect(command.match(/--chapters/g)).toHaveLength(1)
  })
})
