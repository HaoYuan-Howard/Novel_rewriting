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

/** 在合法只读响应中替换认知条目；录制 fixture 保留生产原样。 */
async function stubCognition(records: Record<string, unknown>[]) {
  const candidate = 'cand'
  const recorded = await mockApi.getStr06Blueprint(candidate)
  const firstChapter = recorded.chapters[0]
  const firstBeat = firstChapter.beats[0]
  stubCandidateApi(candidate)
  vi.spyOn(api, 'getStr06Blueprint').mockResolvedValue({
    ...recorded,
    chapters: [{ ...firstChapter, beats: [{ ...firstBeat, cognition: records }, ...firstChapter.beats.slice(1)] },
      ...recorded.chapters.slice(1)],
  })
  vi.spyOn(api, 'getStr06Chapter').mockImplementation((_c, o) => mockApi.getStr06Chapter(candidate, o))
  return { candidate, beatId: firstBeat.beat_id }
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

    // 集合核对报告（收尾批）：五态原样呈现，空态是「无」，不是 unknown。
    const report = await screen.findByTestId('str06-assembly-report')
    expect(report).toHaveTextContent('Stage7 集合核对')
    expect(report).toHaveTextContent('assembly ready')
    expect(report).toHaveTextContent('有效')
    expect(report).toHaveTextContent('1、2')
    // 成功 run 不渲染失败块（失败形状缺席是 null，不是空对象）。
    expect(screen.queryByTestId('str06-run-failure')).not.toBeInTheDocument()
    // 逐章留证（D3/W2）是「最近一次完成执行」的合法空清单：两块并排呈现，
    // 无失败章——与「没执行过」的缺席 null 是两件事。
    const bodyFailures = screen.getByTestId('str06-body-failures')
    expect(bodyFailures).toHaveTextContent('正文 · 逐章失败留证')
    expect(bodyFailures).toHaveTextContent('最近一次完成执行')
    expect(bodyFailures).toHaveTextContent('无失败章')
    const observationFailures = screen.getByTestId('str06-chapter-observation-failures')
    expect(observationFailures).toHaveTextContent('章观察 · 逐章失败留证')
    expect(observationFailures).toHaveTextContent('无失败章')
    // 干净链从没起过修订轮：回派账缺席是 null，不渲染空账本。
    expect(screen.queryByTestId('str06-owner-issues')).not.toBeInTheDocument()
  })

  it('distinguishes believing a proposition, believing its negation, and withdrawing acceptance', async () => {
    const proposition = { ref: 'p_arrival', statement: '阿姐送来夫君' }
    const basis = [{ segment: 'e01_01', quote: '我以为是阿姐送来的。' }]
    const belief = {
      proposition, holder: 'yun_niang', op: 'set', mode: 'believes', polarity: 'positive',
      support: 'inference', reader_visible: false, basis,
    }
    const { candidate, beatId } = await stubCognition([
      belief,
      { ...belief, polarity: 'negative' },
      { ...belief, op: 'unset', mode: null, polarity: null, support: 'confirmed', reader_visible: true,
        basis: [{ segment: 'e01_01', quote: '我这才知道自己认错了。' }] },
    ])
    renderCandidate(candidate)

    const positive = await screen.findByTestId(`str06-cognition-${beatId}-0`)
    const negative = screen.getByTestId(`str06-cognition-${beatId}-1`)
    const withdrawn = screen.getByTestId(`str06-cognition-${beatId}-2`)
    expect(positive).toHaveTextContent('yun_niang 相信「阿姐送来夫君」')
    expect(negative).toHaveTextContent('yun_niang 相信非「阿姐送来夫君」')
    expect(withdrawn).toHaveTextContent('yun_niang 已撤回对「阿姐送来夫君」的接受')
    expect(withdrawn).not.toHaveTextContent('确知非')
    expect(withdrawn).not.toHaveTextContent('null')
    expect(positive).toHaveTextContent('证据性质：推测')
  })

  it('shows claim support and reader visibility independently from the cognition mode', async () => {
    const shared = { proposition: { ref: 'p_arrival', statement: '阿姐送来夫君' }, holder: 'yun_niang',
      op: 'set', polarity: 'negative', basis: [{ segment: 'e01_01', quote: '阿姐说不是她送来的。' }] }
    const { candidate, beatId } = await stubCognition([
      { ...shared, mode: 'hears_claim', support: 'claim', reader_visible: true },
      { ...shared, mode: 'learns', support: 'confirmed', reader_visible: false },
    ])
    renderCandidate(candidate)

    const claim = await screen.findByTestId(`str06-cognition-${beatId}-0`)
    const hiddenKnowledge = screen.getByTestId(`str06-cognition-${beatId}-1`)
    expect(claim).toHaveTextContent('听到说法非「阿姐送来夫君」')
    expect(claim).toHaveTextContent('证据性质：仅说法 · 读者可见')
    expect(claim).not.toHaveTextContent('确知')
    expect(hiddenKnowledge).toHaveTextContent('确知非「阿姐送来夫君」')
    expect(hiddenKnowledge).toHaveTextContent('证据性质：已确认 · 未向读者展示')
  })

  it('shows anonymous identity evidence and the holder and polarity of initial knowledge', async () => {
    const candidate = 'cand'
    const recorded = await mockApi.getStr06Characters(candidate)
    const character = recorded.characters[0]
    stubCandidateApi(candidate)
    vi.spyOn(api, 'getStr06Characters').mockResolvedValue({
      ...recorded,
      characters: [{ ...character, identity_bindings: [{
        description: '襁褓中背有印记的婴儿', appearance_refs: ['e07_02', 'e08_03'], identity_refs: ['e10_04'],
      }] }, ...recorded.characters.slice(1)],
      initial_knowledge: [{
        knowledge_id: 'k0_01', holder: character.char_id,
        proposition: { ref: 'p_arrival', statement: '阿姐送来夫君' }, polarity: 'negative',
        basis: [{ segment: 'e01_01', quote: '他一直知道人不是阿姐送来的。' }],
      }],
    })
    vi.spyOn(api, 'getStr06Chapter').mockImplementation((_c, o) => mockApi.getStr06Chapter(candidate, o))
    renderCandidate(candidate)

    expect(await screen.findByText('身份指称：襁褓中背有印记的婴儿')).toBeInTheDocument()
    expect(screen.getByText('e07_02、e08_03').parentElement).toHaveTextContent('出场证据')
    expect(screen.getByText('e10_04').parentElement).toHaveTextContent('同一性证据')
    const initialKnowledge = screen.getByText('开场已确认知识').parentElement!.parentElement!
    expect(initialKnowledge).toHaveTextContent(`持有者 ${character.char_id}`)
    expect(initialKnowledge).toHaveTextContent('确知非「阿姐送来夫君」 · 负命题')
  })

  it('renders the failure shape and chapter failures straight from the run view', async () => {
    const candidate = 'cand'
    const recorded = await mockApi.getStr06Run(candidate)
    // 失败形状是服务端直投影（workflow_status.json + body_failures.json）；
    // 前端不发明读数，只呈现类别、code/phase/item 定位与逐章留证。
    const failedRun = {
      ...recorded,
      status: 'failed',
      failed_node: 'str06.stage5.body',
      accepted_body: false,
      failure_kind: 'ExecutionError',
      detail: '第2章截断即停（finish_reason=length），不重写、不进候选',
      error_code: 'truncated',
      error_stage: '',
      error_node: 'str06.stage5.body',
      error_item: 'chapter:2',
      error_phase: 'accept',
      error_context: { owner: 'body_ch02' },
      body_failures: {
        present: true,
        requested: [1, 2],
        attempted: [1, 2],
        chapters: [
          { chapter_ordinal: 2, failure_kind: 'ExecutionError', detail: '截断的开头。' },
        ],
        recorded_at: 1790269950,
      },
      assembly_report: null,
    }
    stubCandidateApi(candidate)
    vi.spyOn(api, 'getStr06Run').mockResolvedValue(failedRun)
    vi.spyOn(api, 'getStr06Chapter').mockImplementation((_c, o) =>
      mockApi.getStr06Chapter(candidate, o))

    renderCandidate(candidate)

    expect(await screen.findByText('STR-06 候选链 · 只读监控')).toBeInTheDocument()
    expect(await screen.findByText('failed')).toBeInTheDocument()
    const failure = screen.getByTestId('str06-run-failure')
    expect(failure).toHaveTextContent('ExecutionError')
    expect(failure).toHaveTextContent('truncated')
    expect(failure).toHaveTextContent('chapter:2')
    expect(failure).toHaveTextContent('body_ch02')
    const failures = screen.getByTestId('str06-body-failures')
    expect(failures).toHaveTextContent('第 2 章')
    expect(failures).toHaveTextContent('ExecutionError')
    // 请求范围与实际执行章是「最近一次完成执行」的事实，原样呈现。
    expect(failures).toHaveTextContent('请求')
    expect(failures).toHaveTextContent('实际执行')
    // Stage7 未跑：报告缺席（null）不渲染，不造「核对过」的空面。
    expect(screen.queryByTestId('str06-assembly-report')).not.toBeInTheDocument()
  })

  it('renders revision rounds next to the stop reason straight from the run view', async () => {
    const candidate = 'cand'
    const recorded = await mockApi.getStr06Run(candidate)
    // R3 监控面：修订轮（runtime/issues 回派账投影）与停止原因（失败形状直投影）
    // 并排呈现——轮数上限 rounds_cap 由后端投影，前端不抄契约常量；status、
    // code、item 都是后端事实，前端不发明读数。
    const revisedRun = {
      ...recorded,
      status: 'failed',
      failed_node: 'str06.stage4.compile',
      failure_kind: 'OwnerRevisionStop',
      detail:
        'str06.stage4.compile 的修订要求没有进展：design 修订后同一问题集原样重现，' +
        '按 item 停（同一问题集重复即停）：must_keep_unbound[ob01]：必保义务 ob01 尚未承载',
      owner_issues: {
        present: true,
        rounds_cap: 2,
        records: [
          {
            owner: 'design',
            node: 'str06.stage4.body_design',
            raised_by: 'str06.stage4.compile',
            status: 'open',
            rounds_total: 1,
            fingerprint: 'a'.repeat(64),
            issues: [
              {
                code: 'must_keep_unbound',
                item: 'ob01',
                message: '必保义务 ob01 尚未承载（owner=design；目标场景缺承载）',
                context: { unbound_owner: 'design' },
              },
            ],
            updated_at: 1790269950,
          },
        ],
      },
    }
    stubCandidateApi(candidate)
    vi.spyOn(api, 'getStr06Run').mockResolvedValue(revisedRun)
    vi.spyOn(api, 'getStr06Chapter').mockImplementation((_c, o) =>
      mockApi.getStr06Chapter(candidate, o))

    renderCandidate(candidate)

    expect(await screen.findByText('STR-06 候选链 · 只读监控')).toBeInTheDocument()
    const rounds = await screen.findByTestId('str06-owner-issues')
    expect(rounds).toHaveTextContent('修订轮 · 跨节点回派')
    expect(rounds).toHaveTextContent('design')
    expect(rounds).toHaveTextContent('open')
    expect(rounds).toHaveTextContent('must_keep_unbound')
    expect(rounds).toHaveTextContent('ob01')
    expect(rounds).toHaveTextContent('必保义务 ob01 尚未承载')
    expect(rounds).toHaveTextContent('str06.stage4.body_design')
    expect(rounds).toHaveTextContent('str06.stage4.compile')
    // 修订轮 1／2：分子来自盘上回派账，分母是后端投影的契约常量。
    expect(rounds).toHaveTextContent('1')
    expect(rounds).toHaveTextContent('2')
    // 停止原因与修订轮并排：失败形状直投影（含按 item 的 typed 问题清单）。
    const failure = screen.getByTestId('str06-run-failure')
    expect(failure).toHaveTextContent('OwnerRevisionStop')
    expect(failure).toHaveTextContent('同一问题集重复即停')
    expect(failure).toHaveTextContent('must_keep_unbound[ob01]')
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
