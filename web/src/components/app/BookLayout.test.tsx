import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { BookLayout } from './BookLayout'
import { Rail } from './Rail'

function bookWithStages(
  stage_done: Record<string, boolean | null>,
): Awaited<ReturnType<typeof api.getNovel>> {
  return {
    sid: 'book-1', title: '测试书', dir: 'book-1', dirname: 'book-1', chapters: 11,
    stages: {}, stage_done, stage_reviewable: {}, chapters_done: {},
    summary: {
      verdict: 'pending', text: '', detail: '', artifact_count: 0, latest_mtime: null,
    },
  }
}

describe('BookLayout 书级外壳', () => {
  it('语料库侧栏不展示本机地址或空书解释', () => {
    renderRoute(
      <Rail sid={null} screen="corpus" collapsed={false} onToggle={() => {}} />,
    )

    expect(screen.getByText('小说重构 · 审校台')).toBeVisible()
    expect(screen.queryByText(/127\.0\.0\.1:8765/)).not.toBeInTheDocument()
    expect(screen.queryByText(/还没打开哪一本书/)).not.toBeInTheDocument()
  })

  it('侧栏可以折叠成窄轨并保留展开入口', () => {
    const onToggle = vi.fn()
    renderRoute(
      <Rail sid={null} screen="corpus" collapsed onToggle={onToggle} />,
    )

    expect(screen.queryByText('小说重构 · 审校台')).not.toBeInTheDocument()
    expect(screen.queryByText('库')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '语料库' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开侧栏' }))
    expect(onToggle).toHaveBeenCalledOnce()
  })

  it('折叠后阶段按钮只剩现役屏，不保留旧链读屏入口', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage1: true }))
    renderRoute(
      <Rail sid="book-1" screen="stage0" collapsed onToggle={() => {}} />,
    )

    expect(await screen.findByRole('button', { name: '剔除番外' })).toBeVisible()
    expect(screen.getByRole('button', { name: '分章报告' })).toBeVisible()
    // STR-06 切换（F8c）：旧链读屏与待办入口整族退役，左导航不再出现。
    for (const retired of ['原文↔骨架', '全局设定', '计划↔上下文', '成稿↔材料', '人名+成书', '待办']) {
      expect(screen.queryByRole('button', { name: retired })).not.toBeInTheDocument()
    }
  })

  it('不再提供按盘面补录验收链写按钮，人工闸写面全部退役', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage1: true, stage7: true }))
    renderRoute(<BookLayout />)

    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    expect(screen.queryByRole('button', { name: '按盘面补录验收链' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看Stage7/ })).not.toBeInTheDocument()
    // 人工闸写面整族退役：按形状查（不写死方法名），审批/补录/定稿类缝一个都不剩。
    expect(Object.keys(api).filter((key) => /approv|reset|backfill/i.test(key))).toEqual([])
  })

  it('书名取不到时分别呈现加载中、缺样本与失败，不猜标题', async () => {
    vi.spyOn(api, 'getNovel').mockRejectedValue(new Error('后端没起来'))
    renderRoute(<BookLayout />)

    expect(await screen.findByText('（取不到书名）')).toBeVisible()
    expect(screen.getByText('book-1 · 章数待分章')).toBeVisible()
  })

  it('交付结论不在这层推断：没有最终缺口提示条', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue(bookWithStages({ stage7: true }))
    renderRoute(<BookLayout />)

    await waitFor(() => expect(screen.getByText('book-1 · 11 章')).toBeVisible())
    expect(screen.queryByText(/尚未对齐当前选择|检查单尚未闭合|最终定稿当前未生效/))
      .not.toBeInTheDocument()
  })
})
