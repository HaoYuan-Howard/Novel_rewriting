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

describe('Stage0Screen 批准前分章预览', () => {
  it('把纯数字番外的章号异常摆在批准动作之前', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      original_path: 'book.txt',
      code: 'code',
      source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 300,
      body_code_points: 300,
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
      segments: [
        {
          id: 'seg-0',
          kind: 'keep',
          start: 0,
          end: 4,
          chars: 4,
          flagged: false,
          text: '正文一段',
          origin: null,
          removed_kind: null,
          marker_line: null,
        },
      ],
    })
    // 机器零命中（removed 为空、无 warning）——审批页原有的三样看不出任何异常，
    // 唯一能看见的就是这一屏里第 3 章的原始章号退回了 1。
    vi.spyOn(api, 'getStage0PartitionPreview').mockResolvedValue({
      input_sha1: 'clean',
      available: true,
      reason: null,
      hook_chars: 27,
      hook_preview: '开篇钩子。',
      chapters: [
        {
          chapter_ordinal: 1,
          source_chapter_label: '1',
          chars: 129,
          preview: '正文第1章。',
          label_delta: null,
          anomaly: 'repeat',
        },
        {
          chapter_ordinal: 2,
          source_chapter_label: '2',
          chars: 129,
          preview: '正文第2章。',
          label_delta: 1,
          anomaly: null,
        },
        {
          chapter_ordinal: 3,
          source_chapter_label: '1',
          chars: 31,
          preview: '十年后他们在旧城重逢。',
          label_delta: -2,
          anomaly: 'reset',
        },
      ],
      tail_chars: 0,
      anomaly_count: 2,
      note: '批准前的只读预览：这一屏不写 stage1 产物。',
    })

    renderRoute(<Stage0Screen />)

    expect(await screen.findByText(/章号异常 2/)).toBeVisible()
    expect(screen.getByText(/章号退回/)).toBeVisible()
    expect(screen.getByText(/章号重复/)).toBeVisible()
    expect(screen.getByText(/十年后他们在旧城重逢/)).toBeVisible()
  })

  it('分不出章时给出理由而不是一屏空表', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      original_path: 'book.txt',
      code: 'code',
      source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 300,
      body_code_points: 300,
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
      segments: [
        {
          id: 'seg-0',
          kind: 'keep',
          start: 0,
          end: 4,
          chars: 4,
          flagged: false,
          text: '正文一段',
          origin: null,
          removed_kind: null,
          marker_line: null,
        },
      ],
    })
    vi.spyOn(api, 'getStage0PartitionPreview').mockResolvedValue({
      input_sha1: 'clean',
      available: false,
      reason: '只识别到 0 个正文章节，少于最低要求 2',
      hook_chars: 0,
      hook_preview: '',
      chapters: [],
      tail_chars: 0,
      anomaly_count: 0,
      note: '按当前剔除结果分不出章。',
    })

    renderRoute(<Stage0Screen />)

    expect(await screen.findByText('分不出章')).toBeVisible()
    expect(screen.getByText(/只识别到 0 个正文章节/)).toBeVisible()
  })
})
