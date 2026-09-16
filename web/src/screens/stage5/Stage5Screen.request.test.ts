import { describe, expect, it } from 'vitest'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { chapterSelectionArgs } from './Stage5Screen'
import {
  retainPretrimArgs,
  stage5WontfixBlockedReason,
  stage5WontfixSpec,
  stage5WriteActions,
  stage5WritingBefore,
} from './useStage5Screen'

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
      label: 'stage5 全书闭卷写作 --chapters',
      extra,
      args: chapterSelectionArgs(extra, '11'),
      sid: SAMPLE_SID,
      ordinal: null,
    })
    const command = preview.steps[0]?.argv ?? ''

    expect(command).toContain('--chapters 11')
    expect(command).not.toContain('--mode')
    expect(command.match(/--chapters/g)).toHaveLength(1)
  })

  it('hides canonical revalidate from Stage5 user action catalog (batch C)', async () => {
    const enums = await mockApi.getEnums()
    const actions = stage5WriteActions(enums.actions)
    const revalidate = actions.filter((action) => action.extra?.includes('--revalidate'))

    expect(revalidate).toHaveLength(0)
    expect(actions.some((action) =>
      action.extra?.some((flag) => ['--actor', '--reason'].includes(flag)),
    )).toBe(false)
    expect(actions.some((action) => action.extra?.includes('--apply-edit'))).toBe(false)
  })

  it('only prepares writing authorization for an unapproved paid action', async () => {
    let calls = 0
    const startWriting = async () => {
      calls += 1
    }

    expect(stage5WritingBefore({ paid: false }, false, startWriting)).toBeUndefined()
    expect(stage5WritingBefore({ paid: true }, true, startWriting)).toBeUndefined()
    const before = stage5WritingBefore({ paid: true }, false, startWriting)

    expect(before).toBeTypeOf('function')
    await before?.()
    expect(calls).toBe(1)
  })

  it('builds retain-pretrim preview flags from the exact chapter and trimmed audit inputs', () => {
    expect(retainPretrimArgs(7, ' reviewer-a ', ' 保留原稿，已核对超限原因 ')).toEqual([
      '--chapters', '7',
      '--actor', 'reviewer-a',
      '--reason', '保留原稿，已核对超限原因',
    ])
  })

  it('binds wontfix to chapter/book + rule + the complete evidence key', () => {
    expect(stage5WontfixSpec(3, 'length_report', '887|880|875', ' 人工接受 ')).toBe(
      '3|length_report|887|880|875=人工接受',
    )
    expect(stage5WontfixSpec('book', 'source_overlap_high', 'book:abc', '接受')).toBe(
      'book|source_overlap_high|book:abc=接受',
    )
    expect(stage5WontfixSpec(3, 'length_report', null, '接受')).toBeNull()
    expect(stage5WontfixSpec(3, 'length_report', '887', '   ')).toBeNull()
  })

  it('refuses stale, already-wontfix, and identity-less findings before opening confirm', () => {
    const open = { evidence_key: 'ev-1', wontfix: null }
    expect(stage5WontfixBlockedReason('fresh', open, false)).toBeNull()
    expect(stage5WontfixBlockedReason('stale', open, false)).toContain('fresh Review')
    expect(stage5WontfixBlockedReason('fresh', {
      evidence_key: 'ev-1',
      wontfix: { why: '接受', at: 'review_2' },
    }, false)).toContain('已经有 wontfix')
    expect(stage5WontfixBlockedReason('fresh', {
      evidence_key: null,
      wontfix: null,
    }, false)).toContain('evidence_key')
  })
})
