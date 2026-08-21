import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage0Screen } from './Stage0Screen'
import type { Stage0Segments } from '@/types/stages'

describe('Stage0Screen segment virtualization', () => {
  it('keeps a 622-row source DOM bounded and preserves the selected segment across filters', async () => {
    const rows: Stage0Segments['segments'] = Array.from({ length: 622 }, (_, index) => ({
      id: 'seg-' + index,
      kind: 'keep',
      start: index * 10,
      end: index * 10 + 10,
      chars: 10,
      flagged: false,
      text: '正文分区 ' + index,
      origin: null,
      removed_kind: null,
      marker_line: null,
    }))
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      original_path: 'book.txt',
      code: 'code',
      source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 6220,
      body_code_points: 6220,
      warnings: [],
      approved: 'pending',
      approved_at: null,
      approval_origin: null,
      approved_by: null,
      round: 1,
      edited_by_human: false,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      source_sha1: 'source',
      available: true,
      reason: null,
      segments: rows,
    })
    renderRoute(<Stage0Screen />)

    fireEvent.click(await screen.findByRole('button', { name: '查看完整原文' }))
    const viewport = await screen.findByTestId('stage0-segment-viewport')
    await waitFor(() => expect(within(viewport).getAllByRole('button').length).toBeGreaterThan(0))
    expect(within(viewport).getAllByRole('button').length).toBeLessThan(80)

    fireEvent.click(within(viewport).getByRole('button', { name: '正文分区 0' }))
    expect(screen.getByText(/当前删除 1 段/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '只看候选 / warning' }))
    expect(await screen.findByRole('button', { name: '正文分区 0' })).toBeVisible()
  })

  it('resolves every typed merge conflict through the audited API request', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      original_path: 'book.txt',
      code: 'code',
      source_sha1: '1111111111111111111111111111111111111111',
      clean_sha1: '2222222222222222222222222222222222222222',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 10,
      body_code_points: 10,
      warnings: [],
      approved: 'approved',
      approved_at: 'now',
      approval_origin: 'human',
      approved_by: 'tester',
      round: 2,
      edited_by_human: true,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      source_sha1: '1111111111111111111111111111111111111111',
      available: true,
      reason: null,
      segments: [],
    })
    vi.spyOn(api, 'getStage0Merge').mockResolvedValue({
      candidate_id: 'a'.repeat(64),
      created_at: 'now',
      expected_round: 2,
      expected_source_sha1: '1'.repeat(40),
      expected_clean_sha1: '2'.repeat(40),
      base_snapshot_sha256: 'b'.repeat(64),
      base_clean_sha1: '3'.repeat(40),
      ours_clean_sha1: '4'.repeat(40),
      theirs_source_sha1: '5'.repeat(40),
      theirs_clean_sha1: '6'.repeat(40),
      base_text: 'base',
      ours_text: 'ours',
      theirs_text: 'theirs',
      proposed_clean_text: 'proposal',
      conflicts: [
        {
          id: 'conflict-1',
          base_span: [0, 4],
          base_text: 'base',
          ours_text: 'ours',
          theirs_text: 'theirs',
        },
      ],
      theirs_snapshot: {},
    })
    const resolve = vi.spyOn(api, 'resolveStage0Merge').mockResolvedValue({
      id: 'job',
      kind: 'stage0.merge.resolve',
      label: 'resolve',
      argv: 'resolve',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid: 'book-1',
      ordinal: null,
    })
    renderRoute(<Stage0Screen />)

    fireEvent.change(await screen.findByLabelText('自报身份 actor'), {
      target: { value: 'reviewer-a' },
    })
    fireEvent.change(screen.getByLabelText('本次批准 / 编辑理由 reason'), {
      target: { value: '逐项核对三方文本' },
    })
    fireEvent.click(screen.getByLabelText('已批准正文'))
    fireEvent.click(screen.getByRole('button', { name: '接受三方合并候选' }))

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    expect(resolve.mock.calls[0]?.[1]).toMatchObject({
      actor: 'reviewer-a',
      reason: '逐项核对三方文本',
      resolutions: [{ conflict_id: 'conflict-1', choice: 'ours', custom_text: null }],
    })
  })
})
