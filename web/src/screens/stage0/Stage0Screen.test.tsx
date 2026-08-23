import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage0Screen } from './Stage0Screen'
import type { Stage0Segments } from '@/types/stages'

describe('Stage0Screen 新书起跑入口', () => {
  it('上传扫描后可以直接交给 agent，同时保留人工 Stage0 入口', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      source_ref: {
        kind: 'managed_upload' as const,
        blob_sha256: 'f'.repeat(64),
        filename: 'book.txt',
      },
      code: 'code',
      normalized_source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 300,
      body_code_points: 300,
      warnings: [],
      approval: { status: 'pending' as const },
      executable: false,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      normalized_source_sha1: 'source',
      available: true,
      reason: null,
      segments: [],
    })
    vi.spyOn(api, 'getStage0Candidate').mockResolvedValue(null)
    vi.spyOn(api, 'getStage0PartitionPreview').mockResolvedValue({
      input_sha1: 'clean',
      available: false,
      reason: '尚未分章',
      hook_chars: 0,
      hook_preview: '',
      chapters: [],
      tail_chars: 0,
      anomaly_count: 0,
      note: '只读预览',
    })
    const preview = vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      argv: ['claude', '-p', 'prompt'],
      prompt: '从当前盘面续跑',
      authorized: false,
      egress_authorized: true,
    })

    renderRoute(<Stage0Screen />, '/n/book-1/stage0')

    expect(
      await screen.findByRole('button', { name: '继续人工审校 Stage0' }),
    ).toBeVisible()
    expect(screen.getByText(/不会跳过任何硬闸/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'agent 从 Stage0 开始跑' }))

    expect(await screen.findByText('让 agent 跑这本书')).toBeVisible()
    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith({
        sid: 'book-1',
        authorized: false,
        egress_authorized: true,
      }),
    )
  })
})

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
      source_ref: { kind: 'external_file' as const, absolute_path: 'book.txt' },
      code: 'code',
      normalized_source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 6220,
      body_code_points: 6220,
      warnings: [],
      approval: { status: 'pending' as const },
      executable: false,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      normalized_source_sha1: 'source',
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

  it('把机器候选摆成整版三选一，并按乐观锁提交采纳', async () => {
    // ⚠ 阶段 C 起页面上要决定的不再是「逐个冲突选 ours 还是 theirs」——diff3 与
    //   typed 冲突已整份下线（聚合根是 {原文身份, 正文, 剔除账本, 告警} 整团，
    //   只对正文做 diff3 保证不了整个聚合一致）——而是**整版留哪一版**。
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      source_ref: { kind: 'external_file' as const, absolute_path: 'book.txt' },
      code: 'code',
      normalized_source_sha1: '1111111111111111111111111111111111111111',
      clean_sha1: '2222222222222222222222222222222222222222',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 10,
      body_code_points: 10,
      warnings: [],
      approval: { status: 'approved' as const,
                  at: 'now', by: 'tester', reason: '看过了' },
      executable: true,
      revision_id: 'c'.repeat(64),
      revision_origin: 'human_edit' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      normalized_source_sha1: '1111111111111111111111111111111111111111',
      available: true,
      reason: null,
      segments: [],
    })
    vi.spyOn(api, 'getStage0Candidate').mockResolvedValue({
      candidate: {
        revision_id: 'd'.repeat(64),
        parent_revision_id: 'c'.repeat(64),
        origin: 'machine' as const,
        actor: null,
        reason: null,
        at: 'now',
        code: 'code',
        content: {
          original_filename: 'book.txt',
          normalized_source_sha1: '1'.repeat(40),
          source_encoding: 'utf-8',
          text_norm: 'NFC+LF',
          total_code_points: 10,
          clean_text: '机器新版正文',
          clean_sha1: '6'.repeat(40),
          body_code_points: 6,
          removed: [],
          warnings: [],
        },
      },
      current_revision_id: 'c'.repeat(64),
      current_body_code_points: 10,
      current_removed_count: 0,
      current_origin: 'human_edit',
    })
    const adopt = vi.spyOn(api, 'adoptStage0Candidate').mockResolvedValue({
      id: 'job',
      kind: 'stage0.candidate.adopt',
      label: 'adopt',
      argv: 'adopt',
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
      target: { value: '两版都读过，机器新版更准' },
    })
    expect(screen.getByText(/机器新版正文/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '采纳机器新版' }))

    await waitFor(() => expect(adopt).toHaveBeenCalledTimes(1))
    expect(adopt.mock.calls[0]?.[1]).toMatchObject({
      candidate_revision_id: 'd'.repeat(64),
      expected_revision_id: 'c'.repeat(64),
      actor: 'reviewer-a',
      reason: '两版都读过，机器新版更准',
    })
  })
})

describe('Stage0Screen 批准前分章预览', () => {
  it('把纯数字番外的章号异常摆在批准动作之前', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      source_ref: { kind: 'external_file' as const, absolute_path: 'book.txt' },
      code: 'code',
      normalized_source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 300,
      body_code_points: 300,
      warnings: [],
      approval: { status: 'pending' as const },
      executable: false,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      normalized_source_sha1: 'source',
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
          source_chapter_no: 1,
          chars: 129,
          preview: '正文第1章。',
          label_delta: null,
          anomaly: 'repeat',
        },
        {
          chapter_ordinal: 2,
          source_chapter_no: 2,
          chars: 129,
          preview: '正文第2章。',
          label_delta: 1,
          anomaly: null,
        },
        {
          chapter_ordinal: 3,
          source_chapter_no: 1,
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
      source_ref: { kind: 'external_file' as const, absolute_path: 'book.txt' },
      code: 'code',
      normalized_source_sha1: 'source',
      clean_sha1: 'clean',
      source_encoding: 'utf-8',
      text_norm: 'NFC+LF',
      removed: [],
      total_code_points: 300,
      body_code_points: 300,
      warnings: [],
      approval: { status: 'pending' as const },
      executable: false,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      normalized_source_sha1: 'source',
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
