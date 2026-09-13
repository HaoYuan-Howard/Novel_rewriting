import { useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID, PLOT_SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { Rail } from '@/components/app/Rail'
import { BookLayout } from '@/components/app/BookLayout'
import { Stage7Screen } from '@/screens/stage7/Stage7Screen'
import { RunScreen } from '@/screens/run/RunScreen'
import { ChromeProvider } from '@/hooks/useChrome'
import { useRunProgress } from './useRunProgress'
import { useAgentRuns } from './useAgentRuns'
import { useApprovals } from './useApprovals'
import { READY_PROFILES } from '@/test/agent'
import { CHECK_KINDS, type CheckReport } from '@/types/stages'
import type { AgentRunRecord } from '@/types/agent'
import type { RunProgress } from '@/types/run'
import type { ApprovalDoc } from '@/types/approvals'
import type { ReactElement } from 'react'

const SID = SAMPLE_SID

function client() {
  // 与长驻产品页同样禁止自动焦点刷新；Infinity 使测试不能靠缓存超时碰巧通过。
  return new QueryClient({ defaultOptions: {
    queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
    mutations: { retry: false },
  } })
}

function atBook(ui: ReactElement, qc = client(), sid = SID) {
  return { qc, ...render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/n/' + sid + '/run']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ChromeProvider><Routes><Route path="/n/:sid/*" element={ui} /></Routes></ChromeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  ) }
}

async function source(sid = SID) {
  const novel = await mockApi.getNovel(sid)
  const approvals = await mockApi.getApprovals(sid)
  const inbox = await mockApi.getInbox(sid)
  const sample = await mockApi.getCheck(sid, 'stage4')
  if (!sample || !sample.rules.length) throw new Error('Stage4 测试报告缺正式形状')
  const oldReport: CheckReport = {
    ...sample, badge: 'fail', passed: false,
    rules: [{ ...sample.rules[0], sev: 'hard', status: 'hit' },
      { ...sample.rules[0], sev: 'info', status: 'hit' },
      { ...sample.rules[0], sev: 'info', status: 'hit' }],
    counts: { ok: 25, hit: 3, unver: 6, skipped: 0 },
  }
  const freshReport: CheckReport = {
    ...oldReport, badge: 'amber', passed: true,
    rules: [{ ...oldReport.rules[0], status: 'ok' }, ...oldReport.rules.slice(1)],
    counts: { ok: 26, hit: 2, unver: 6, skipped: 0 },
  }
  const oldNovel = { ...novel,
    stage_done: { ...novel.stage_done, stage4: false },
    stage_reviewable: Object.fromEntries(CHECK_KINDS.map((kind) => [kind, kind === 'stage4'])),
  }
  const freshNovel = { ...oldNovel,
    stage_done: { ...oldNovel.stage_done, stage4: true },
    stage_reviewable: { ...oldNovel.stage_reviewable, stage5: true },
  }
  const oldApprovals = { ...approvals, gate: { ...approvals.gate, s1: false, s4: false } }
  const freshApprovals = { ...oldApprovals, gate: { ...oldApprovals.gate, s4: true } }
  const initial = await mockApi.getRunProgress(sid)
  let progress: RunProgress = {
    ...initial,
    stage_done: { ...initial.stage_done, stage4: false },
    steps: initial.steps.map((step) => ({ ...step,
      beacon: step.node === 'stage4.plan' ? 'running' : 'pending',
      last_ts: step.node === 'stage4.plan' ? 10 : null,
      last_exit: null, elapsed: step.node === 'stage4.plan' ? 1 : null,
    })),
    events_tail: [],
  }
  const active: AgentRunRecord = {
    run_id: 'refresh-parent', start: { sid, argv: [], prompt: '', authorized: true,
      egress_authorized: true, code_version: 'a'.repeat(40), profiles: READY_PROFILES },
    contract: null, epochs: [], active_epoch_id: null, started: 1, ended: null,
    exit_code: null, state: 'running', note: null, outcome: null,
    allowed_controls: ['pause', 'stop'],
  }
  let run = active
  let fresh = false
  let unavailable = false
  const getNovel = vi.spyOn(api, 'getNovel').mockImplementation(async () =>
    structuredClone(fresh ? freshNovel : oldNovel))
  const getApprovals = vi.spyOn(api, 'getApprovals').mockImplementation(async () => {
    if (unavailable) throw new Error('审批读取未知')
    return structuredClone(fresh ? freshApprovals : oldApprovals)
  })
  const getCheck = vi.spyOn(api, 'getCheck').mockImplementation(async (_sid, kind) => {
    if (kind !== 'stage4') return null
    if (unavailable) throw new Error('校验读取未知')
    return structuredClone(fresh ? freshReport : oldReport)
  })
  const getInbox = vi.spyOn(api, 'getInbox').mockResolvedValue(inbox)
  const getProgress = vi.spyOn(api, 'getRunProgress').mockImplementation(async (sid) =>
    structuredClone({ ...progress, sid }))
  const getRuns = vi.spyOn(api, 'getAgentRuns').mockImplementation(async () =>
    ({ runs: [structuredClone(run)] }))
  const getJobs = vi.spyOn(api, 'getJobs')
  vi.spyOn(api, 'getAgentRunLog').mockResolvedValue({ text: '', next_byte: 0 })
  return {
    oldNovel, oldApprovals, oldReport, freshApprovals,
    getNovel, getApprovals, getCheck, getInbox, getProgress, getRuns, getJobs,
    setFresh: () => { fresh = true },
    setUnavailable: () => { unavailable = true },
    advance: () => {
      const next = progress.steps.find((step) => step.node === 'stage5.write')!
      progress = { ...progress,
        stage_done: { ...progress.stage_done, stage4: true },
        resume_frontier: { ...progress.resume_frontier, state: 'ready', node: next.node,
          seq: next.seq, label: next.label, waiting_on: null, next_handler: 'executor' },
        steps: progress.steps.map((step) => step.node === 'stage4.plan'
          ? { ...step, beacon: 'ok', last_exit: 0, last_ts: 12, elapsed: null }
          : step.node === 'stage5.write'
            ? { ...step, beacon: 'running', last_ts: 13, elapsed: 1 } : step),
        events_tail: [{ ts: 12, sid, run_id: active.run_id, execution_id: 'plan-complete',
          node: 'stage4.plan', phase: 'end', exit_code: 0, note: null, beacon: 'ok' }],
      }
    },
    tick: () => {
      progress = { ...progress, resume_frontier: { ...progress.resume_frontier, why: '计时更新' },
        steps: progress.steps.map((step) => ({ ...step,
          elapsed: step.elapsed === null ? null : step.elapsed + 5 })) }
      if (run.outcome) run = { ...run, outcome: { ...run.outcome, checked_at: 999 } }
    },
    end: (state: 'ok' | 'unknown') => {
      run = { ...active, state, ended: 20, exit_code: state === 'ok' ? 0 : 70,
        outcome: { state: state === 'ok' ? 'ready_with_disclosures' : 'unknown',
          reason: '服务端独立终验', checked_at: 20 }, allowed_controls: [] }
    },
  }
}

function ScreenAndRail({ manual = false }: { manual?: boolean }) {
  return <><Rail sid={SID} screen="run" ordinal={1} collapsed={false} onToggle={() => {}} />
    <RunScreen />{manual ? <ApproveButton /> : null}</>
}

function ApproveButton() {
  const { approveStructure } = useApprovals()
  return <button onClick={() => approveStructure.mutate({
    actor: '测试审阅者', review_token: 's4sr1_' + 'a'.repeat(64), reason: '已读完整结构',
    chapters: [{ output_ordinal: 1, decision: 'accept', reason: '明确接受' }],
  })}>提交结构审批</button>
}

async function settled(qc: QueryClient) {
  await waitFor(() => expect(qc.isFetching()).toBe(0))
}

async function refetch(qc: QueryClient, key: readonly unknown[]) {
  await act(async () => { await qc.refetchQueries({ queryKey: key, exact: true }) })
  await settled(qc)
}

describe('Agent 进展驱动书级读模型刷新', () => {
  it('长驻 Stage7 页也挂载进度与终态观察，自动完成和拒绝即时刷新下载事实', async () => {
    const sid = PLOT_SAMPLE_SID
    const live = await source(sid)
    const view = await mockApi.getStage7(sid)
    const getStage7 = vi.spyOn(api, 'getStage7').mockResolvedValue({ ...view,
      delivery: { state: 'stopped', reason: '尚待自动装配', checked_at: 1 },
    })
    const { qc } = atBook(<><BookLayout /><Stage7Screen /></>, client(), sid)
    expect(await screen.findByText(/尚待自动装配/)).toBeVisible()
    await settled(qc)
    expect(live.getProgress).toHaveBeenCalledWith(sid)
    expect(live.getRuns).toHaveBeenCalledWith(sid)

    live.advance()
    getStage7.mockResolvedValue({ ...view,
      delivery: { state: 'ready_with_disclosures', reason: '当前交付已闭合', checked_at: 2 },
    })
    await refetch(qc, qk.run(sid))
    expect(await screen.findByRole('button', { name: '下载 ' + view.book_filename })).toBeEnabled()

    live.end('unknown')
    getStage7.mockResolvedValue({ ...view,
      delivery: { state: 'unknown', reason: '当前来源无法复验', checked_at: 3 },
    })
    await refetch(qc, qk.agentRuns(sid))
    expect(await screen.findByText(/当前来源无法复验/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '下载 ' + view.book_filename })).not.toBeInTheDocument()
  })

  it('真实进度轮询使同页旧 hard badge 与侧栏审批同步，计时轮询不重复全书检查', async () => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    expect(await screen.findByText('hard 1 · info 2 · unver 6')).toBeVisible()
    expect(await screen.findByRole('button', { name: '计划↔上下文，需处理' })).toBeVisible()
    await settled(qc)
    expect(live.getCheck).not.toHaveBeenCalledWith(SID, 'stage5')
    live.setFresh()
    live.advance()
    // 不手工失效 check，也不重挂页面；只等 useRunProgress 的现役 2.5 秒轮询。
    expect(await screen.findByText('hard 0 · info 2 · unver 6', {}, { timeout: 4500 })).toBeVisible()
    expect(await screen.findByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
    await waitFor(() => expect(live.getCheck).toHaveBeenCalledWith(SID, 'stage5'))
    expect(live.getJobs).not.toHaveBeenCalled()
    await settled(qc)
    const checks = live.getCheck.mock.calls.length
    const novels = live.getNovel.mock.calls.length
    const approvals = live.getApprovals.mock.calls.length
    live.tick()
    await refetch(qc, qk.run(SID))
    await refetch(qc, qk.agentRuns(SID))
    expect(live.getCheck).toHaveBeenCalledTimes(checks)
    expect(live.getNovel).toHaveBeenCalledTimes(novels)
    expect(live.getApprovals).toHaveBeenCalledTimes(approvals)
  })

  it('进度变化后读取失败保持校验和批准未知，不用新节点推算通过', async () => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    expect(await screen.findByText('hard 1 · info 2 · unver 6')).toBeVisible()
    await settled(qc)
    live.setUnavailable()
    live.advance()
    await refetch(qc, qk.run(SID))
    expect(await screen.findByText('? 校验未知')).toBeVisible()
    expect(await screen.findByRole('button', { name: '计划↔上下文，确认状态未知' })).toBeVisible()
    expect(screen.queryByText('hard 1 · info 2 · unver 6')).not.toBeInTheDocument()
  })

  it.each(['ok', 'unknown'] as const)('AgentRun 到 %s 后也刷新，即使节点快照没有变化', async (state) => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    expect(await screen.findByText('hard 1 · info 2 · unver 6')).toBeVisible()
    await settled(qc)
    live.setFresh()
    live.end(state)
    await refetch(qc, qk.agentRuns(SID))
    expect(await screen.findByText('hard 0 · info 2 · unver 6')).toBeVisible()
    expect(await screen.findByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
    const reads = live.getCheck.mock.calls.length
    live.tick()
    await refetch(qc, qk.agentRuns(SID))
    expect(live.getCheck).toHaveBeenCalledTimes(reads)
  })

  it('手动结构审批复用同一失效口，无需等运行轮询或 API Job', async () => {
    const live = await source()
    const approve = vi.spyOn(api, 'approveStructure').mockImplementation(async () => {
      live.setFresh()
      return live.freshApprovals
    })
    const { qc } = atBook(<ScreenAndRail manual />)
    expect(await screen.findByText('hard 1 · info 2 · unver 6')).toBeVisible()
    await settled(qc)
    const reads = live.getApprovals.mock.calls.length
    const polls = live.getProgress.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '提交结构审批' }))
    expect(await screen.findByText('hard 0 · info 2 · unver 6')).toBeVisible()
    expect(await screen.findByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
    expect(approve).toHaveBeenCalledOnce()
    expect(live.getApprovals).toHaveBeenCalledTimes(reads)
    expect(live.getProgress).toHaveBeenCalledTimes(polls)
    expect(live.getJobs).not.toHaveBeenCalled()
  })

  it('同 QueryClient 的多个观察者只刷新一次，别书和轮询自身不失效', async () => {
    const live = await source()
    function Observer() { useRunProgress(); return null }
    function Readout() {
      const q = useQuery({ queryKey: qk.check(SID, 'stage4'), queryFn: () => api.getCheck(SID, 'stage4') })
      return <span>{q.data?.badge}</span>
    }
    const qc = client()
    qc.setQueryData(qk.check('other-book', 'stage4'), live.oldReport)
    qc.setQueryData(qk.jobPreview({ kind: 'stage4', sid: SID, label: '测试预览', extra: null }), { marker: '动作预览' })
    const { unmount } = atBook(<><Observer /><Observer /><Readout /></>, qc)
    expect(await screen.findByText('fail')).toBeVisible()
    await settled(qc)
    const checks = live.getCheck.mock.calls.length
    const polls = live.getProgress.mock.calls.length
    live.setFresh()
    live.advance()
    await refetch(qc, qk.run(SID))
    expect(await screen.findByText('amber')).toBeVisible()
    expect(live.getCheck).toHaveBeenCalledTimes(checks + 1)
    expect(live.getProgress).toHaveBeenCalledTimes(polls + 1)
    expect(qc.getQueryState(qk.check('other-book', 'stage4'))?.isInvalidated).toBe(false)
    expect(qc.getQueryState(qk.jobPreview({ kind: 'stage4', sid: SID, label: '测试预览', extra: null }))?.isInvalidated).toBe(false)
    unmount()
  })

  it('同毫秒重新挂载也先实际读取进度并复验缓存，不能沿用无限期旧批准', async () => {
    const live = await source()
    vi.spyOn(Date, 'now').mockReturnValue(1788000000000)
    function Observer() { useRunProgress(); useAgentRuns(); return null }
    function Host() {
      const [watching, setWatching] = useState(true)
      return <><button onClick={() => setWatching(!watching)}>切换观察</button>
        {watching ? <Observer /> : null}
        <Rail sid={SID} screen="run" ordinal={1} collapsed={false} onToggle={() => {}} /></>
    }
    const { qc } = atBook(<Host />)
    expect(await screen.findByRole('button', { name: '计划↔上下文，需处理' })).toBeVisible()
    await settled(qc)
    fireEvent.click(screen.getByRole('button', { name: '切换观察' }))
    live.setFresh()
    const polls = live.getProgress.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '切换观察' }))
    expect(await screen.findByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
    expect(live.getProgress).toHaveBeenCalledTimes(polls + 1)
  })

  it.each(['before', 'after'] as const)(
    '首挂旧审批在首次进度观察 %s 返回时，都必须复验且晚回包不能盖回',
    async (when) => {
      const live = await source()
      const stableProgress = await api.getRunProgress(SID)
      live.getProgress.mockClear()
      let finishProgress!: (value: RunProgress) => void
      let finishOldApproval!: (value: ApprovalDoc) => void
      live.getProgress.mockImplementationOnce(() => new Promise((resolve) => {
        finishProgress = resolve
      })).mockResolvedValue(stableProgress)
      live.getApprovals.mockImplementationOnce(() => new Promise((resolve) => {
        finishOldApproval = resolve
      }))
      function Observer() { useRunProgress(); return null }
      const { qc } = atBook(<><Observer />
        <Rail sid={SID} screen="run" ordinal={1} collapsed={false} onToggle={() => {}} /></>)
      await waitFor(() => expect(live.getApprovals).toHaveBeenCalledOnce())
      if (when === 'before') {
        await act(async () => finishOldApproval(live.oldApprovals))
        expect(await screen.findByRole('button', { name: '计划↔上下文，需处理' })).toBeVisible()
      }
      live.setFresh()
      await act(async () => finishProgress(stableProgress))
      expect(await screen.findByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
      if (when === 'after') await act(async () => finishOldApproval(live.oldApprovals))
      await settled(qc)
      expect(screen.getByRole('button', { name: '计划↔上下文，已确认' })).toBeVisible()
      expect(live.getApprovals).toHaveBeenCalledTimes(2)
      await refetch(qc, qk.run(SID))
      expect(live.getApprovals).toHaveBeenCalledTimes(2)
    },
  )

  it('审批在切书后返回时只更新原书，当前页和别书校验缓存都保持隔离', async () => {
    const live = await source()
    const other = 'other-book'
    const otherDoc = { ...live.oldApprovals, sid: other }
    const qc = client()
    qc.setQueryData(qk.approvals(SID), live.oldApprovals)
    qc.setQueryData(qk.approvals(other), otherDoc)
    qc.setQueryData(qk.check(SID, 'stage4'), live.oldReport)
    qc.setQueryData(qk.check(other, 'stage4'), live.oldReport)
    let finishApproval!: (value: ApprovalDoc) => void
    const submit = vi.spyOn(api, 'approveStructure').mockImplementation(() => new Promise((resolve) => {
      finishApproval = resolve
    }))
    function Books() {
      const { sid = '' } = useParams()
      const navigate = useNavigate()
      return <><button onClick={() => navigate('/n/' + other + '/run')}>切换书</button>
        <ApproveButton />
        <Rail sid={sid} screen="run" ordinal={1} collapsed={false} onToggle={() => {}} /></>
    }
    atBook(<Books />, qc)
    fireEvent.click(screen.getByRole('button', { name: '提交结构审批' }))
    await waitFor(() => expect(submit).toHaveBeenCalledOnce())
    expect(submit.mock.calls[0][0]).toBe(SID)
    fireEvent.click(screen.getByRole('button', { name: '切换书' }))
    expect(await screen.findByRole('button', { name: '计划↔上下文，需处理' })).toBeVisible()
    await act(async () => finishApproval(live.freshApprovals))
    await waitFor(() => expect(qc.isMutating()).toBe(0))
    expect(qc.getQueryData(qk.approvals(SID))).toEqual(live.freshApprovals)
    expect(qc.getQueryData(qk.approvals(other))).toEqual(otherDoc)
    expect(qc.getQueryState(qk.check(SID, 'stage4'))?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.check(other, 'stage4'))?.isInvalidated).toBe(false)
    expect(screen.getByRole('button', { name: '计划↔上下文，需处理' })).toBeVisible()
  })})
