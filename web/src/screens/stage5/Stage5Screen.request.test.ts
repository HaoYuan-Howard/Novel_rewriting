import { describe, expect, it } from 'vitest'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { chapterSelectionArgs } from './Stage5Screen'
import { stage5WriteActions } from './useStage5Screen'

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

  it('offers both revalidate arms with audit inputs and no credential-only buttons', async () => {
    const enums = await mockApi.getEnums()
    const actions = stage5WriteActions(enums.actions)
    const revalidate = actions.filter((action) => action.extra?.includes('--revalidate'))

    expect(revalidate.map((action) => action.mode).sort()).toEqual(['deep', 'shallow'])
    expect(revalidate.every((action) =>
      action.required_inputs?.map((input) => input.flag).join(',') === '--actor,--reason',
    )).toBe(true)
    expect(actions.some((action) =>
      action.extra?.some((flag) => ['--actor', '--reason'].includes(flag)),
    )).toBe(false)
    expect(actions.some((action) => action.extra?.includes('--apply-edit'))).toBe(false)
  })
})
