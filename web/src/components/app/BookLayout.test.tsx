import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { BookLayout } from './BookLayout'
import { Rail } from './Rail'
import type { ApprovalDoc } from '@/types/approvals'

const pending: ApprovalDoc = {
  v: 6,
  sid: 'book-1',
  gate: { s0: false, s1: false, s2: false, s4: false, s5: false, s6: false, s7: false },
  seen: { read: false, global: false },
  writing: false,
  design_review: {
    state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
    current_decision: null, history: [], binding_infeasibility: null, repair_capability: null,
  },
  structure: {
    state: 'unavailable',
    current_subject_sha256: null,
    current_decision: null,
    history: [],
    binding_concerns: [],
  },
  writing_approval: {
    available: false, current_subject_sha256: null, current_decision: null, history: [],
  },
  closure: {
    state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
  },
  spawns: {},
}

describe('BookLayout approval source', () => {
  it('语料库侧栏不展示本机地址或空书解释', () => {
    renderRoute(
      <Rail sid={null} screen="corpus" ordinal={null} collapsed={false} onToggle={() => {}} />,
    )

    expect(screen.getByText('小说重构 · 审校台')).toBeVisible()
    expect(screen.queryByText(/127\.0\.0\.1:8765/)).not.toBeInTheDocument()
    expect(screen.queryByText(/还没打开哪一本书/)).not.toBeInTheDocument()
  })

  it('侧栏可以折叠成窄轨并保留展开入口', () => {
    const onToggle = vi.fn()
    renderRoute(
      <Rail sid={null} screen="corpus" ordinal={null} collapsed onToggle={onToggle} />,
    )

    expect(screen.queryByText('小说重构 · 审校台')).not.toBeInTheDocument()
    expect(screen.queryByText('库')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '语料库' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开侧栏' }))
    expect(onToggle).toHaveBeenCalledOnce()
  })

  it('折叠后阶段按钮的可访问名称仍保留确认与锁定状态', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue({
      sid: 'book-1',
      title: '测试书',
      dir: 'book-1',
      dirname: 'book-1',
      chapters: 11,
      stages: {},
      stage_done: {},
      stage_reviewable: {},
      chapters_done: {},
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 0,
        latest_mtime: null,
      },
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...pending,
      gate: { ...pending.gate, s0: true },
    })
    renderRoute(
      <Rail sid="book-1" screen="stage0" ordinal={null} collapsed onToggle={() => {}} />,
    )

    expect(await screen.findByRole('button', { name: '剔除番外，已确认' })).toBeVisible()
    expect(screen.getByRole('button', { name: /分章报告，未确认/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /开篇文案，锁定，先过 stage5 成稿确认/ })).toBeDisabled()
  })

  it('labels backfilled gates as disk-derived rather than user-clicked', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue({
      sid: 'book-1',
      title: '测试书',
      dir: 'book-1',
      dirname: 'book-1',
      chapters: 11,
      stages: {},
      stage_done: { stage1: true },
      stage_reviewable: {},
      chapters_done: {},
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 0,
        latest_mtime: null,
      },
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue(pending)
    const backfill = vi.spyOn(api, 'backfillApprovals').mockResolvedValue({
      approvals: { ...pending, gate: { ...pending.gate, s0: true } },
      backfilled: ['s0'],
    })
    renderRoute(<BookLayout />)

    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '按盘面补录验收链' }))
    await waitFor(() => expect(backfill).toHaveBeenCalledWith('book-1'))
    expect(await screen.findByText(/这些闸门来自盘面核对，不是你逐屏点击的记录/)).toBeVisible()
  })
})
