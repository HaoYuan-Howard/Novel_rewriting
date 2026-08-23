import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage0Screen } from './Stage0Screen'
import type { Stage0Segments } from '@/types/stages'

describe('Stage0Screen 新书起跑入口', () => {
  it('审批操作栏可以直接交给 Agent，同时保留人工 Stage0 操作', async () => {
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
    const partitionPreview = vi.spyOn(api, 'getStage0PartitionPreview')
    const preview = vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      argv: ['claude', '-p', 'prompt'],
      prompt: '从当前盘面续跑',
      authorized: false,
      egress_authorized: true,
    })

    renderRoute(<Stage0Screen />, '/n/book-1/stage0')

    expect(await screen.findByRole('button', { name: 'Agent 代跑' })).toBeVisible()
    expect(screen.queryByText(/不会跳过任何硬闸/)).not.toBeInTheDocument()
    expect(screen.getByText('点击正文分区可调整删除范围；结尾标记不可删除。')).toBeVisible()
    expect(screen.queryByText(/rev .*sha1/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('自报身份 actor')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('本次批准 / 编辑理由 reason')).not.toBeInTheDocument()
    expect(partitionPreview).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Agent 代跑' }))

    expect(await screen.findByText('让 agent 跑这本书')).toBeVisible()
    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith({
        sid: 'book-1',
        authorized: false,
        egress_authorized: true,
      }),
    )
  })

  it('人工批准无需填写表单，并按动作写入默认审计值', async () => {
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
      segments: [],
    })
    vi.spyOn(api, 'getStage0Candidate').mockResolvedValue(null)
    const approve = vi.spyOn(api, 'approveStage0').mockResolvedValue({
      id: 'approve',
      kind: 'stage0.approve',
      label: '批准 Stage0',
      argv: 'approve',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid: 'book-1',
      ordinal: null,
    })
    const partition = vi.spyOn(api, 'partitionStage1').mockResolvedValue({
      id: 'partition',
      kind: 'stage1.partition',
      label: '切分 Stage1',
      argv: 'partition',
      state: 'ok',
      paid: false,
      started: 2,
      finished: 3,
      sid: 'book-1',
      ordinal: null,
    })

    renderRoute(<Stage0Screen />, '/n/book-1/stage0')

    expect(screen.queryByLabelText('自报身份 actor')).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: /^保存选择并进入 stage1/ }))

    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve).toHaveBeenCalledWith('book-1', {
      actor: 'human-ui',
      reason: '前端人工审校并批准 Stage0',
    })
    expect(partition).toHaveBeenCalledWith('book-1')
  })
})

describe('Stage0Screen 原文审阅', () => {
  it('默认展示完整原文，收起再展开后保留选择，且长书 DOM 有界', async () => {
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

    const viewport = await screen.findByTestId('stage0-segment-viewport')
    await waitFor(() => expect(within(viewport).getAllByRole('button').length).toBeGreaterThan(0))
    expect(within(viewport).getAllByRole('button').length).toBeLessThan(80)

    const selectedSegment = within(viewport).getByRole('button', { name: '正文分区 0' })
    fireEvent.click(selectedSegment)
    expect(screen.getByText(/当前删除 1 段/)).toBeVisible()
    expect(selectedSegment).toHaveClass('bg-remove-bg', 'border-l-remove', 'text-remove')
    expect(screen.getByRole('img', { name: 'warning 说明' })).toHaveAttribute(
      'title',
      expect.stringMatching(/不能安全自动删除/),
    )
    expect(screen.queryByText(/个候选分区/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收起原文' }))
    expect(screen.queryByTestId('stage0-segment-viewport')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开原文' }))
    const reopened = await screen.findByTestId('stage0-segment-viewport')
    expect(within(reopened).getByRole('button', { name: '正文分区 0' })).toHaveAttribute(
      'title',
      '取消删除这一段',
    )
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

    expect(await screen.findByText(/机器新版正文/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '采纳机器新版' }))

    await waitFor(() => expect(adopt).toHaveBeenCalledTimes(1))
    expect(adopt.mock.calls[0]?.[1]).toMatchObject({
      candidate_revision_id: 'd'.repeat(64),
      expected_revision_id: 'c'.repeat(64),
      actor: 'human-ui',
      reason: '前端人工采纳机器 Stage0 版本',
    })
  })
})
