import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { qkStr06 } from '@/api/queryKeys'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Str06MonitorScreen } from './Str06MonitorScreen'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function renderCandidate(candidate = 'cand') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const rendered = render(<QueryClientProvider client={queryClient}>
    <MemoryRouter initialEntries={['/str06/' + candidate]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes><Route path="/str06/:candidate" element={<Str06MonitorScreen />} /></Routes>
    </MemoryRouter>
  </QueryClientProvider>)
  return { ...rendered, queryClient }
}

function stubCandidateApi(candidate = 'cand') {
  vi.spyOn(api, 'getStr06Candidates').mockImplementation(() => mockApi.getStr06Candidates())
  vi.spyOn(api, 'getStr06Run').mockImplementation(() => mockApi.getStr06Run(candidate))
  vi.spyOn(api, 'getStr06SourceStory').mockImplementation(() => mockApi.getStr06SourceStory(candidate))
  vi.spyOn(api, 'getStr06TargetStory').mockImplementation(() => mockApi.getStr06TargetStory(candidate))
  vi.spyOn(api, 'getStr06Chapter').mockImplementation((_candidate, ordinal) => mockApi.getStr06Chapter(candidate, ordinal))
  vi.spyOn(api, 'getStr06Hook').mockImplementation(() => mockApi.getStr06Hook(candidate))
  vi.spyOn(api, 'getStr06Delivery').mockImplementation(() => mockApi.getStr06Delivery(candidate))
}

describe('三层只读监控', () => {
  it('通过同一数据缝展示三层、当前章包与真实来源回执，并能切换章节', async () => {
    stubCandidateApi()
    renderCandidate()
    expect(await screen.findByText('小说重写 · 三层只读监控')).toBeVisible()
    expect(await screen.findByText('Stage2 · 源事实层')).toBeVisible()
    expect(await screen.findByText('Stage4 · 目标故事层')).toBeVisible()
    expect(await screen.findByText('Stage5 · 正文执行层')).toBeVisible()
    await waitFor(() => expect(screen.getAllByTestId('story-prose-receipt').length).toBe(2))
    expect(screen.getByText('源事实 → 目标故事 → 正文执行。机械检查与阅读质量分别呈现。')).toBeVisible()
    expect(screen.getByRole('button', { name: '第 1 章' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '第 2 章' }))
    await waitFor(() => expect(screen.getByText(/来客说明自己走错巷子，我才发觉先前的猜测没有根据。/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '第 2 章' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.getStr06Chapter).toHaveBeenCalledWith('cand', 2)
    expect(screen.queryByRole('button', { name: /批准|编辑|重新生成/ })).not.toBeInTheDocument()
  })

  it('区分材料缺失、契约错误与已生成，不以空数据冒充通过', async () => {
    stubCandidateApi()
    vi.spyOn(api, 'getStr06SourceStory').mockResolvedValue({ candidate_id: 'cand', revision: 'bad',
      status: 'invalid', data: null, errors: ['源事实引用不存在，已拒绝发布。'] })
    vi.spyOn(api, 'getStr06TargetStory').mockResolvedValue({ candidate_id: 'cand', revision: 'missing',
      status: 'missing', data: null, errors: [] })
    renderCandidate()
    expect(await screen.findByText('源事实引用不存在，已拒绝发布。')).toBeVisible()
    expect(await screen.findByText('目标故事尚未发布，等待目标建模与编译。')).toBeVisible()
    expect(screen.getByText('尚无可写作章节')).toBeVisible()
    expect(api.getStr06Chapter).not.toHaveBeenCalled()
  })

  it('run修订变化后在同一挂载中刷新材料；相同修订不反复失效', async () => {
    stubCandidateApi()
    const run = await mockApi.getStr06Run('cand')
    const source = await mockApi.getStr06SourceStory('cand')
    if (!source.data) throw new Error('合成样本缺SourceStory')
    const getRun = vi.spyOn(api, 'getStr06Run').mockResolvedValue({ ...run, revision: 'revision-1' })
    const getSource = vi.spyOn(api, 'getStr06SourceStory').mockResolvedValue(source)
    const { queryClient } = renderCandidate()
    await screen.findAllByText('我猜来客是姐姐请来的，却没有证据。')
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    const firstCalls = getSource.mock.calls.length
    await queryClient.invalidateQueries({ queryKey: qkStr06.run('cand') })
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(getSource.mock.calls.length).toBe(firstCalls)
    getRun.mockResolvedValue({ ...run, revision: 'revision-2' })
    getSource.mockResolvedValue({ ...source, revision: 'revision-2', data: { ...source.data,
      expression: { ...source.data.expression, statement: '最新源事实已发布，保留错误信念的纠正。' } } })
    await queryClient.invalidateQueries({ queryKey: qkStr06.run('cand') })
    expect(await screen.findByText('最新源事实已发布，保留错误信念的纠正。')).toBeVisible()
    expect(getSource.mock.calls.length).toBeGreaterThan(firstCalls)
  })

  it('保留未知用量及节点失败定位，不补造费用或下一步动作', async () => {
    stubCandidateApi()
    const run = await mockApi.getStr06Run('cand')
    vi.spyOn(api, 'getStr06Run').mockResolvedValue({ ...run, revision: 'failed', status: 'failed',
      failure_kind: 'needs_design', failed_node: 'str06.stage5.body', detail: '入场站位有冲突，需要目标层修订。',
      error_node: 'str06.stage4.chapters', error_item: 'chapter:2',
      cost: { present: true, rows: 1, new_paid_rows: 1, cached_rows: 0, prompt_tokens: null,
        completion_tokens: null, reasoning_tokens: null, unknown_cost_rows: 1, per_node: [] } })
    renderCandidate()
    const failed = await screen.findByTestId('str06-run-failure')
    expect(failed).toHaveTextContent('chapter:2')
    expect(failed).toHaveTextContent('str06.stage4.chapters')
    expect(failed).toHaveTextContent('入场站位有冲突，需要目标层修订。')
    expect(screen.getAllByText('unknown').length).toBeGreaterThan(0)
  })

  it('下载失败明确显示后端错误，不把未读取成功当作下载完成', async () => {
    stubCandidateApi()
    vi.spyOn(api, 'getStr06Book').mockRejectedValue(new Error('最终字节与manifest不一致，拒绝下载。'))
    renderCandidate()
    const button = await screen.findByRole('button', { name: '下载成书全文' })
    fireEvent.click(button)
    expect(await screen.findByText('最终字节与manifest不一致，拒绝下载。')).toBeVisible()
    expect(button).toBeEnabled()
  })

  it('下载沿用后端给出的中文书名，不从candidate重新拼文件名', async () => {
    stubCandidateApi()
    const file = await mockApi.getStr06Book('cand')
    vi.spyOn(api, 'getStr06Book').mockResolvedValue(file)
    const create = vi.fn((_blob: Blob | MediaSource) => 'blob:verified-book')
    const revoke = vi.fn()
    class DownloadURL extends URL {
      static override createObjectURL = create
      static override revokeObjectURL = revoke
    }
    vi.stubGlobal('URL', DownloadURL)
    const names: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download)
    })
    renderCandidate()
    fireEvent.click(await screen.findByRole('button', { name: '下载成书全文' }))
    await waitFor(() => expect(names).toEqual(['三层样书_rewriting.txt']))
    expect(create).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('blob:verified-book')
  })
})
