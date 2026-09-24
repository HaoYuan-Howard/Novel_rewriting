import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ChromeProvider } from '@/hooks/useChrome'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Str06MonitorScreen } from './Str06MonitorScreen'

afterEach(() => {
  vi.restoreAllMocks()
})

/** 候选监控屏不在 `/n/:sid` 路由树里：自带一套最小路由（providers 与 renderRoute 相同）。 */
function renderCandidate(candidate: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/str06/' + candidate]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ChromeProvider>
          <Routes>
            <Route path="/str06/:candidate" element={<Str06MonitorScreen />} />
          </Routes>
        </ChromeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** 屏只经 api.* 取数：用 mockApi 的录制数据回填真实数据缝（两条路同一形状）。 */
function stubCandidateApi(candidate: string) {
  const listed = { candidates: [{ candidate, present: true, status: 'delivered', updated_at: 1 }] }
  vi.spyOn(api, 'getStr06Candidates').mockResolvedValue(listed)
  vi.spyOn(api, 'getStr06Run').mockImplementation(() => mockApi.getStr06Run(candidate))
  vi.spyOn(api, 'getStr06Characters').mockImplementation(() => mockApi.getStr06Characters(candidate))
  vi.spyOn(api, 'getStr06Blueprint').mockImplementation(() => mockApi.getStr06Blueprint(candidate))
  vi.spyOn(api, 'getStr06Design').mockImplementation(() => mockApi.getStr06Design(candidate))
  vi.spyOn(api, 'getStr06Ir').mockImplementation(() => mockApi.getStr06Ir(candidate))
  vi.spyOn(api, 'getStr06Hook').mockImplementation(() => mockApi.getStr06Hook(candidate))
  vi.spyOn(api, 'getStr06Naming').mockImplementation(() => mockApi.getStr06Naming(candidate))
  vi.spyOn(api, 'getStr06Delivery').mockImplementation(() => mockApi.getStr06Delivery(candidate))
  return listed
}

describe('Str06MonitorScreen', () => {
  it('renders the recorded candidate root over the single data seam', async () => {
    const listed = await mockApi.getStr06Candidates()
    const candidate = listed.candidates[0].candidate
    const run = await mockApi.getStr06Run(candidate)
    const chapter = await mockApi.getStr06Chapter(candidate, 1)
    const delivery = await mockApi.getStr06Delivery(candidate)
    stubCandidateApi(candidate)
    vi.spyOn(api, 'getStr06Chapter').mockImplementation((_c, o) =>
      mockApi.getStr06Chapter(candidate, o))

    renderCandidate(candidate)

    expect(await screen.findByText('STR-06 候选链 · 只读监控')).toBeInTheDocument()
    // 「跑到哪了」是盘面事实：status 与产物节点数来自后端，前端不拼。
    expect(await screen.findByText(run.status!)).toBeInTheDocument()
    const produced = run.nodes.filter((n) => n.produced).length
    await waitFor(() =>
      expect(screen.getByTestId('str06-produced-nodes'))
        .toHaveTextContent(`产物节点 ${produced}/${run.nodes.length}`))

    // 章正文与回执：semantic 恒 unverified，机械检查名单原样呈现。
    expect(await screen.findByText(chapter.prose!)).toBeInTheDocument()
    const receipt = screen.getByTestId('str06-chapter-receipt')
    expect(receipt).toHaveTextContent('unverified')
    expect(receipt).toHaveTextContent(chapter.receipt!.mechanical_checks.join('、'))

    // 交付 manifest：成书身份与全书偏差读数是盘面事实。
    expect(screen.getByText(delivery.delivery_identity!.slice(0, 12))).toBeInTheDocument()
    expect(screen.getByText(String(delivery.length_reading!.whole_book_deviation_ratio)))
      .toBeInTheDocument()
  })

  it('keeps a missing candidate as an error, never as the recorded root', async () => {
    vi.spyOn(api, 'getStr06Candidates').mockResolvedValue({
      candidates: [{ candidate: 'cand', present: true, status: 'delivered', updated_at: 1 }],
    })
    vi.spyOn(api, 'getStr06Run').mockRejectedValue(new Error('没有这个候选根：not-recorded'))
    renderCandidate('not-recorded')

    // 未知候选根答错，不渲染任何盘面事实（不拿样本候选根顶替）。
    expect(await screen.findByText('没有这个候选根：not-recorded')).toBeInTheDocument()
    expect(screen.queryByText('STR-06 候选链 · 只读监控')).not.toBeInTheDocument()
  })
})
