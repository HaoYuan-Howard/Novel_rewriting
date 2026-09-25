import { api } from '@/api/endpoints'
import { useLocation } from 'react-router-dom'
import { qk } from '@/api/queryKeys'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { BookLayout } from './BookLayout'
import { Rail } from './Rail'
import type { ApprovalDoc } from '@/types/approvals'

const pending: ApprovalDoc = {
  v: 9, prepare: missingPrepareState(),
  sid: 'book-1',
  gate: { s0: false, s1: false, s4: false, s5: false, s6: false, s7: false },
  writing: false,
  design_review: {
    state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
    current_decision: null, history: [], binding_infeasibility: null,
    preflight: null, repair_capability: null,
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

function bookWithStages(
  stage_done: Awaited<ReturnType<typeof api.getNovel>>['stage_done'],
): Awaited<ReturnType<typeof api.getNovel>> {
  return {
    sid: 'book-1', title: '测试书', dir: 'book-1', dirname: 'book-1', chapters: 11,
    stages: {}, stage_done, stage_reviewable: {}, chapters_done: {},
    summary: {
      verdict: 'pending', text: '', detail: '', artifact_count: 0, latest_mtime: null,
    },
  }
}

const current: ApprovalDoc = {
  ...pending,
  gate: { s0: true, s1: true, s4: true, s5: true, s6: true, s7: true },
  writing: true,
}

function CurrentPath() {
  return <output>{useLocation().pathname}</output>
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
    expect(screen.getByRole('button', { name: /人名\+成书，未确认/ })).toBeVisible()
  })

  it('不再提供按盘面补录验收链写按钮', async () => {
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
    renderRoute(<BookLayout />)

    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
    expect('backfillApprovals' in api).toBe(false)
  })

  it('Stage7 的最终缺口只导航只读页，不提供补录代批', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: true }))
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...current, gate: { ...current.gate, s7: false },
    })
    renderRoute(<><BookLayout /><CurrentPath /></>)

    const review = await screen.findByRole('button', { name: '查看Stage7 人物对照' })
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
    fireEvent.click(review)
    expect(await screen.findByText('/n/book-1/stage7')).toBeVisible()
    expect('approveNames' in api).toBe(false)
  })

  it('Stage7 已有产物而最终闸未对齐时提示检查前置，不声称需要重新签字', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: true }))
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...current, gate: { ...current.gate, s5: false, s7: false },
    })
    renderRoute(<BookLayout />)

    expect(await screen.findByRole('button', { name: '查看Stage7 人物对照' })).toBeVisible()
    expect(screen.queryByText(/重新签|必须重审|缺少对应的前端人工验收/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
  })

  it('最终缺口只提供只读导航，不提供补录写按钮', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage1: true, stage7: true }))
    vi.spyOn(api, 'getApprovals').mockResolvedValue(pending)
    renderRoute(<BookLayout />)

    expect(await screen.findByRole('button', { name: '查看Stage7 人物对照' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
  })

  it('后端最终审批已经 current 时不再提示前端缺章或要求补录', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: true }))
    vi.spyOn(api, 'getApprovals').mockResolvedValue(current)
    const { queryClient } = renderRoute(<BookLayout />)
    await waitFor(() => expect(queryClient.getQueryData(qk.approvals('book-1'))).toEqual(current))
    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    expect(screen.queryByText(/检查单尚未闭合|最终定稿当前未生效|尚未对齐当前选择/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
  })

  it('审批读取失败不从已有最终产物推断缺少决定或提供补录', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: true }))
    vi.spyOn(api, 'getApprovals').mockRejectedValue(new Error('审批当前状态未知'))
    const { queryClient } = renderRoute(<BookLayout />)
    await waitFor(() => expect(queryClient.getQueryState(qk.approvals('book-1'))?.status).toBe('error'))
    expect(screen.queryByText(/检查单尚未闭合|最终定稿当前未生效/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看Stage7/ })).not.toBeInTheDocument()
  })

  it('最终产物是否完成尚不明确时不创建缺口提示', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: false }))
    vi.spyOn(api, 'getApprovals').mockResolvedValue(pending)
    const { queryClient } = renderRoute(<BookLayout />)
    await waitFor(() => expect(queryClient.getQueryData(qk.approvals('book-1'))).toEqual(pending))
    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    expect(screen.queryByText(/检查单尚未闭合|最终定稿当前未生效/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看Stage7/ })).not.toBeInTheDocument()
  })

})
