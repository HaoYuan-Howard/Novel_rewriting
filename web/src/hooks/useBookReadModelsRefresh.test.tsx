import { useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { Rail } from '@/components/app/Rail'
import { BookLayout } from '@/components/app/BookLayout'
import { RunScreen } from '@/screens/run/RunScreen'
import { ChromeProvider } from '@/hooks/useChrome'
import { useRunProgress } from './useRunProgress'
import { useAgentRuns } from './useAgentRuns'
import { useMutation } from '@tanstack/react-query'
import { invalidateBookReadModels } from './bookReadModels'
import { READY_PROFILES } from '@/test/agent'
import type { CheckReport } from '@/types/stages'
import type { AgentRunRecord } from '@/types/agent'
import type { RunProgress } from '@/types/run'
import type { NovelOverview } from '@/types/novels'
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

/**
 * 现役书级读面：总览、Stage0/1 与层级复检（取值面只剩 stage0/stage1）。
 * 审批、inbox 与旧链读屏已随人工闸整族退役，不再有第二套读模型可刷。
 */
async function source(sid = SID) {
  const novel = await mockApi.getNovel(sid)
  const stage1 = await mockApi.getStage1(sid)
  const oldReport: CheckReport = {
    sid,
    node: 'stage0.remove',
    stage: 'stage0',
    provenance: {
      freshness: 'fresh', reason: '', ghosts: [], edges: [], invalid: [],
      revalidated: false, origin: null,
    },
    rules: [],
    accounting: [],
    badge: 'fail',
    passed: false,
    counts: { ok: 25, hit: 3, unver: 6, skipped: 0 },
  }
  const freshReport: CheckReport = {
    ...oldReport, badge: 'amber', passed: true, counts: { ok: 26, hit: 2, unver: 6, skipped: 0 },
  }
  const oldNovel: NovelOverview = {
    ...novel, title: '测试书', stage_done: { ...novel.stage_done, stage1: false },
  }
  const freshNovel: NovelOverview = {
    ...oldNovel, stage_done: { ...oldNovel.stage_done, stage1: true },
  }
  const initial = await mockApi.getRunProgress(sid)
  let progress: RunProgress = {
    ...initial,
    stage_done: { ...initial.stage_done, stage1: false },
    steps: initial.steps.map((step) => ({ ...step,
      beacon: step.node === 'str06.stage2.book' ? 'running' : 'pending',
      last_ts: step.node === 'str06.stage2.book' ? 10 : null,
      last_exit: null, elapsed: step.node === 'str06.stage2.book' ? 1 : null,
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
  const getNovel = vi.spyOn(api, 'getNovel').mockImplementation(async (bookSid) =>
    structuredClone(bookSid === sid
      ? (fresh ? freshNovel : oldNovel)
      : { ...oldNovel, sid: bookSid }))
  const getStage1 = vi.spyOn(api, 'getStage1').mockImplementation(async () => {
    if (unavailable) throw new Error('Stage1 读取未知')
    return structuredClone(stage1)
  })
  const getCheck = vi.spyOn(api, 'getCheck').mockImplementation(async (_sid, kind) => {
    if (kind !== 'stage0') return null
    if (unavailable) throw new Error('复检读取未知')
    return structuredClone(fresh ? freshReport : oldReport)
  })
  const getProgress = vi.spyOn(api, 'getRunProgress').mockImplementation(async (bookSid) =>
    structuredClone({ ...progress, sid: bookSid }))
  const getRuns = vi.spyOn(api, 'getAgentRuns').mockImplementation(async () =>
    ({ runs: [structuredClone(run)] }))
  const getJobs = vi.spyOn(api, 'getJobs')
  vi.spyOn(api, 'getAgentRunLog').mockResolvedValue({ text: '', next_byte: 0 })
  return {
    oldNovel, freshNovel, oldReport, freshReport,
    getNovel, getStage1, getCheck, getProgress, getRuns, getJobs,
    setFresh: () => { fresh = true },
    setUnavailable: () => { unavailable = true },
    advance: () => {
      const next = progress.steps.find((step) => step.node === 'str06.stage2.names')!
      progress = { ...progress,
        stage_done: { ...progress.stage_done, stage1: true },
        resume_frontier: { ...progress.resume_frontier, state: 'ready', node: next.node,
          seq: next.seq, label: next.label, waiting_on: null, next_handler: 'executor' },
        steps: progress.steps.map((step) => step.node === 'str06.stage2.book'
          ? { ...step, beacon: 'ok', last_exit: 0, last_ts: 12, elapsed: null }
          : step.node === 'str06.stage2.names'
            ? { ...step, beacon: 'running', last_ts: 13, elapsed: 1 } : step),
        events_tail: [{ ts: 12, sid, run_id: active.run_id, execution_id: 'book-complete',
          node: 'str06.stage2.book', phase: 'end', exit_code: 0, note: null, beacon: 'ok' }],
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

/**
 * 现役书级读面的观察者：总览由进度屏与左栏自己观察，Stage1 与层级复检
 * （取值面只剩 stage0/stage1）由 Stage0/Stage1 屏观察 —— 这里用同一个
 * QueryClient 上的等价 observer 代表那两屏，验的是失效口的覆盖面。
 */
function Readout() {
  const stage1 = useQuery({ queryKey: qk.stage1(SID), queryFn: () => api.getStage1(SID) })
  const check = useQuery({
    queryKey: qk.check(SID, 'stage0'),
    queryFn: () => api.getCheck(SID, 'stage0'),
  })
  if (stage1.isError) return <span>Stage1 读取未知</span>
  return <span>{check.data ? check.data.badge : '复检还没跑'}</span>
}

function ScreenAndRail() {
  return <><Rail sid={SID} screen="run" collapsed={false} onToggle={() => {}} />
    <RunScreen /><Readout /></>
}

function StartJobButton() {
  // 现役唯一书级写口是作业（免费复检 / 确定性重跑）；成功后走同一书级读模型失效。
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => api.startJob({
      kind: 'stage0.remove', label: 'stage0 复检', extra: ['--check-only'], sid: SID, ordinal: null,
    }),
    onSuccess: async () => {
      await invalidateBookReadModels(qc, SID)
    },
  })
  return <button onClick={() => { void m.mutateAsync() }}>发起节点级复检</button>
}

async function settled(qc: QueryClient) {
  await waitFor(() => expect(qc.isFetching()).toBe(0))
}

async function refetch(qc: QueryClient, key: readonly unknown[]) {
  await act(async () => { await qc.refetchQueries({ queryKey: key, exact: true }) })
  await settled(qc)
}

describe('Agent 进展驱动书级读模型刷新', () => {
  it('长驻进度屏挂载进度与代跑观察，跑书推进即刷新书级读模型', async () => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    expect(await screen.findByText('下一处理者：代码（0 成本）')).toBeVisible()
    expect(await screen.findByText('fail')).toBeVisible()
    await settled(qc)
    expect(live.getProgress).toHaveBeenCalledWith(SID)
    expect(live.getRuns).toHaveBeenCalledWith(SID)

    live.setFresh()
    live.advance()
    // 不手工失效，也不重挂页面；只等 useRunProgress 的现役 2.5 秒轮询。
    expect(await screen.findByText('下一节点：Stage2 源名称结构观察（家族、名、化名、称谓构词）', {}, { timeout: 4500 }))
      .toBeVisible()
    expect(qc.getQueryData(qk.novel(SID))).toEqual(live.freshNovel)
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
    expect(live.getJobs).not.toHaveBeenCalled()
  })

  it('计时轮询不重复全书检查，只比较持久进展', async () => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    await settled(qc)
    const novels = live.getNovel.mock.calls.length
    const checks = live.getCheck.mock.calls.length
    const stage1 = live.getStage1.mock.calls.length

    live.tick()
    await refetch(qc, qk.run(SID))
    await refetch(qc, qk.agentRuns(SID))
    expect(live.getNovel).toHaveBeenCalledTimes(novels)
    expect(live.getCheck).toHaveBeenCalledTimes(checks)
    expect(live.getStage1).toHaveBeenCalledTimes(stage1)
  })

  it('进度变化后读取失败保持未知，不用新节点推算通过', async () => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    await settled(qc)
    live.setUnavailable()
    live.advance()
    await refetch(qc, qk.run(SID))
    expect(live.getStage1).toHaveBeenCalled()
    await waitFor(() =>
      expect(qc.getQueryState(qk.stage1(SID))?.status).toBe('error'))
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.oldReport)
  })

  it.each(['ok', 'unknown'] as const)('AgentRun 到 %s 后也刷新，即使节点快照没有变化', async (state) => {
    const live = await source()
    const { qc } = atBook(<ScreenAndRail />)
    await settled(qc)
    live.setFresh()
    live.end(state)
    await refetch(qc, qk.agentRuns(SID))
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
    const reads = live.getCheck.mock.calls.length
    live.tick()
    await refetch(qc, qk.agentRuns(SID))
    expect(live.getCheck).toHaveBeenCalledTimes(reads)
  })

  it('作业终态复用同一失效口，无需等运行轮询', async () => {
    const live = await source()
    // 测试环境走真实数据缝（VITE_USE_MOCK=false）：这里替换的是 HTTP 响应，
    // 不是业务路径——作业成功后仍由同一个失效口刷新书级读模型。
    const start = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'job-1', kind: 'stage0.remove', label: 'stage0 复检',
      argv: 'novel stage0.remove --check-only', state: 'ok', paid: false,
      started: 1, finished: 2, sid: SID, ordinal: null,
      executor_backend: null, egress_authorized: null,
    })
    const { qc } = atBook(<><ScreenAndRail /><StartJobButton /></>)
    await settled(qc)
    const novels = live.getNovel.mock.calls.length
    const polls = live.getProgress.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '发起节点级复检' }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    await waitFor(() => expect(live.getNovel.mock.calls.length).toBeGreaterThan(novels))
    expect(live.getProgress).toHaveBeenCalledTimes(polls)
    expect(live.getJobs).not.toHaveBeenCalled()
  })

  it('同 QueryClient 的多个观察者只刷新一次，别书和轮询自身不失效', async () => {
    const live = await source()
    function Observer() { useRunProgress(); return null }
    function Readout() {
      const q = useQuery({ queryKey: qk.novel(SID), queryFn: () => api.getNovel(SID) })
      return <span>{q.data?.title ?? '取不到'}</span>
    }
    const qc = client()
    qc.setQueryData(qk.novel('other-book'), { ...live.oldNovel, sid: 'other-book' })
    qc.setQueryData(qk.jobPreview({ kind: 'stage0.remove', sid: SID, label: '测试预览', extra: null }), { marker: '动作预览' })
    const { unmount } = atBook(<><Observer /><Observer /><Readout /></>, qc)
    expect(await screen.findByText('测试书')).toBeVisible()
    await settled(qc)
    const novels = live.getNovel.mock.calls.length
    const polls = live.getProgress.mock.calls.length
    live.setFresh()
    live.advance()
    await refetch(qc, qk.run(SID))
    expect(live.getNovel).toHaveBeenCalledTimes(novels + 1)
    expect(live.getProgress).toHaveBeenCalledTimes(polls + 1)
    expect(qc.getQueryState(qk.novel('other-book'))?.isInvalidated).toBe(false)
    expect(qc.getQueryState(qk.jobPreview({ kind: 'stage0.remove', sid: SID, label: '测试预览', extra: null }))?.isInvalidated).toBe(false)
    unmount()
  })

  it('同毫秒重新挂载也先实际读取进度并复验缓存', async () => {
    const live = await source()
    vi.spyOn(Date, 'now').mockReturnValue(1788000000000)
    function Observer() { useRunProgress(); useAgentRuns(); return null }
    function Host() {
      const [watching, setWatching] = useState(true)
      return <><button onClick={() => setWatching(!watching)}>切换观察</button>
        {watching ? <Observer /> : null}
        <Readout />
        <Rail sid={SID} screen="run" collapsed={false} onToggle={() => {}} /></>
    }
    const { qc } = atBook(<Host />)
    await settled(qc)
    fireEvent.click(screen.getByRole('button', { name: '切换观察' }))
    live.setFresh()
    const polls = live.getProgress.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '切换观察' }))
    await waitFor(() => expect(live.getProgress.mock.calls.length).toBeGreaterThan(polls))
    await settled(qc)
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
  })

  it('首次真实进度快照后复验缓存读模型，同快照不重复全书检查', async () => {
    const live = await source()
    const stableProgress = await api.getRunProgress(SID)
    live.getProgress.mockClear()
    let finishProgress!: (value: RunProgress) => void
    live.getProgress.mockImplementationOnce(() => new Promise((resolve) => {
      finishProgress = resolve
    })).mockResolvedValue(stableProgress)
    function Observer() { useRunProgress(); return null }
    const { qc } = atBook(<><Observer /><Readout />
      <Rail sid={SID} screen="run" collapsed={false} onToggle={() => {}} /></>)
    await waitFor(() => expect(live.getProgress).toHaveBeenCalledOnce())
    // 挂载前的缓存读数不能冒充这次服务端观察：快照落地才复验。
    live.setFresh()
    await act(async () => { finishProgress(stableProgress) })
    await settled(qc)
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
    const reads = live.getCheck.mock.calls.length
    await refetch(qc, qk.run(SID))
    expect(live.getCheck).toHaveBeenCalledTimes(reads)
  })

  it('复验前先取消在途旧请求，晚回包不能盖回新读数', async () => {
    const live = await source()
    // 第一次读 check 握在手里不返回（旧请求在途）；推进后复验取消它并重读。
    let releaseStale!: (value: CheckReport | null) => void
    live.getCheck.mockImplementationOnce(() => new Promise((resolve) => {
      releaseStale = resolve
    }))
    const { qc } = atBook(<ScreenAndRail />)
    await waitFor(() => expect(live.getCheck).toHaveBeenCalled())

    live.setFresh()
    live.advance()
    await waitFor(() => expect(live.getCheck.mock.calls.length).toBeGreaterThan(1))
    await settled(qc)
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
    // 旧请求这时候才回来：它已经被取消，不能盖回旧读数。
    await act(async () => { releaseStale(live.oldReport) })
    await settled(qc)
    expect(qc.getQueryData(qk.check(SID, 'stage0'))).toEqual(live.freshReport)
  })

  it('切书后返回的写回只更新原书，别书读模型保持隔离', async () => {
    const live = await source()
    const other = 'other-book'
    const qc = client()
    qc.setQueryData(qk.novel(SID), live.oldNovel)
    qc.setQueryData(qk.novel(other), { ...live.oldNovel, sid: other })
    qc.setQueryData(qk.check(SID, 'stage0'), live.oldReport)
    qc.setQueryData(qk.check(other, 'stage0'), live.oldReport)
    let finishJob!: (job: Awaited<ReturnType<typeof api.startJob>>) => void
    const start = vi.spyOn(api, 'startJob').mockImplementation(() => new Promise((resolve) => {
      finishJob = resolve
    }))
    function Books() {
      const { sid = '' } = useParams()
      const navigate = useNavigate()
      const back = sid === other ? SID : other
      return <><button onClick={() => navigate('/n/' + back + '/run')}>
        {sid === other ? '切回原书' : '切换书'}</button>
        <StartJobButton />
        <BookLayout />
        <Rail sid={sid} screen="run" collapsed={false} onToggle={() => {}} /></>
    }
    atBook(<Books />, qc)
    fireEvent.click(screen.getByRole('button', { name: '发起节点级复检' }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: '切换书' }))
    await settled(qc)
    await act(async () => {
      live.setFresh()
      finishJob({
        id: 'job-late', kind: 'stage0.remove', label: 'stage0 复检',
        argv: 'novel stage0.remove --check-only', state: 'ok', paid: false,
        started: 1, finished: 2, sid: SID, ordinal: null,
      })
    })
    // 原书的读模型被标为待复验；当前书（别书）的读数不被原书的失效口改写。
    await waitFor(() =>
      expect(qc.getQueryState(qk.novel(SID))?.isInvalidated).toBe(true))
    const otherNovel = qc.getQueryData(qk.novel(other))
    expect(otherNovel).toEqual({ ...live.oldNovel, sid: other })
    // 切回原书才真正重读：晚回包不会替别书改数，也不会替原书猜结论。
    fireEvent.click(screen.getByRole('button', { name: '切回原书' }))
    await waitFor(() => expect(qc.getQueryData(qk.novel(SID))).toEqual(live.freshNovel))
    expect(qc.getQueryData(qk.novel(other))).toEqual(otherNovel)
  })
})
