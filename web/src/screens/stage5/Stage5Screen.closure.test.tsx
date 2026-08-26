import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { JobDrawer } from '@/components/job/JobDrawer'
import { ChromeProvider } from '@/hooks/useChrome'
import type { ApprovalDoc } from '@/types/approvals'
import type { Finding } from '@/types/common'
import type { Job, JobPreview, JobRequest } from '@/types/jobs'
import type { ChapterSegment, ReviewResponse, Stage5Response } from '@/types/stages'
import { Stage5Screen } from './Stage5Screen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/hooks/useSample', () => ({
  useSampleCoverage: () => ({ hasSample: () => true }),
}))

function InboxProbe() {
  useQuery({
    queryKey: qk.inbox(SAMPLE_SID),
    queryFn: () => api.getInbox(SAMPLE_SID),
  })
  return null
}

function renderScreen({ withJobDrawer = false, withInboxProbe = false } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={['/n/' + SAMPLE_SID + '/stage5/3']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <ChromeProvider>
            <Routes>
              <Route
                path="/n/:sid/stage5/:o"
                element={
                  <>
                    <Stage5Screen />
                    <ConfirmDialog />
                    {withJobDrawer ? <JobDrawer /> : null}
                    {withInboxProbe ? <InboxProbe /> : null}
                  </>
                }
              />
            </Routes>
          </ChromeProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

function finding(
  rule: string,
  item: string,
  evidenceKey: string | null,
  outputOrdinal: number | null,
  wontfix: Finding['wontfix'] = null,
): Finding {
  return {
    rule,
    sev: 'info',
    item,
    output_ordinal: outputOrdinal,
    detail: '需要人工核对的一条现役结论',
    evidence: evidenceKey ? ['证据原文'] : [],
    fix_args: {},
    evidence_key: evidenceKey,
    owner: null,
    actionable: wontfix === null,
    identity_hit: false,
    identity_dead: false,
    converge: null,
    unresolved_rounds: 1,
    wontfix,
  }
}

async function seedScreen({
  trimAudit = null,
  reviewState = 'fresh',
  chapterFindings = [],
  bookFindings = [],
  draftSegments,
}: {
  trimAudit?: Stage5Response['trim_audit']
  reviewState?: ReviewResponse['review_state']
  chapterFindings?: Finding[]
  bookFindings?: Finding[]
  draftSegments?: ChapterSegment[]
}) {
  const [stage5, review, inbox, enums] = await Promise.all([
    mockApi.getStage5(SAMPLE_SID, 3),
    mockApi.getReview(SAMPLE_SID, 3),
    mockApi.getInbox(SAMPLE_SID),
    mockApi.getEnums(),
  ])
  const subject = 'a'.repeat(64)
  const structureDecision = {
    subject_sha256: subject,
    actor: 'reviewer',
    reason: '已核对全书结构',
    chapters: [{ output_ordinal: 3, decision: 'accept', reason: '第 3 章可执行' }],
    decided_at: '2026-08-25T00:00:00+00:00',
  }
  const approvals: ApprovalDoc = {
    v: 3,
    sid: SAMPLE_SID,
    gate: { s0: true, s1: true, s2: true, s3: true, s4: true, s5: false, s6: false, s7: false },
    seen: { read: true, global: true },
    writing: true,
    structure: {
      state: 'accepted', current_subject_sha256: subject,
      current_decision: structureDecision, history: [structureDecision],
    },
    spawns: {},
  }
  const stage5Response: Stage5Response = {
    ...stage5,
    written: 1,
    missing: [],
    legacy_incompatible: false,
    legacy_paths: [],
    trim_audit: trimAudit,
    drafts: draftSegments
      ? [{
          chars: draftSegments.reduce((n, segment) => n + [...segment.text].length, 0),
          budget_chars: 1200,
          overlap_excl_verbatim: 0,
          segments: draftSegments,
          align: null,
          align_rows: [],
          freshness: 'fresh',
          reason: 'match',
          _provenance: { root_clean_sha1: '0'.repeat(40) },
        }]
      : stage5.drafts,
  }
  const reviewResponse: ReviewResponse = {
    ...review,
    review_state: reviewState,
    review_reasons: reviewState === 'fresh' ? [] : ['RULES 指纹已变化'],
    chapter: { ...review.chapter, findings: chapterFindings },
    book: bookFindings,
  }
  const stage5Spy = vi.spyOn(api, 'getStage5').mockResolvedValue(stage5Response)
  const reviewSpy = vi.spyOn(api, 'getReview').mockResolvedValue(reviewResponse)
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals)
  const inboxSpy = vi.spyOn(api, 'getInbox').mockResolvedValue(inbox)
  const jobsSpy = vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
  return { stage5Response, reviewResponse, stage5Spy, reviewSpy, inboxSpy, jobsSpy }
}

function freePreview(request: JobRequest): JobPreview {
  const flags = [...(request.extra ?? []), ...(request.args ?? [])].join(' ')
  return {
    argv: `python -m novel.cli ${request.kind} ${flags}`,
    paid: false,
    label: request.label,
    kind: request.kind,
    steps: [{
      label: request.label,
      paid: false,
      writes: 'stage5 人工审计账',
      argv: `python -m novel.cli ${request.kind} ${flags}`,
    }],
  }
}

function finishedJob(request: { kind: string; label: string }, id: string): Job {
  return {
    id,
    kind: request.kind,
    label: request.label,
    argv: 'python -m novel.cli ' + request.kind,
    state: 'ok',
    paid: false,
    started: 1,
    finished: 2,
    sid: SAMPLE_SID,
    ordinal: 3,
  }
}

describe('Stage5 人工审计闭环', () => {
  it('阅读与编辑都从并行 Stage5 Draft.segments 取得正文', async () => {
    const highlighted = {
      ...finding('draft_evidence', 'Draft 证据', 'draft-evidence', 3),
      evidence: ['来自 Draft 槽位'],
    }
    await seedScreen({
      chapterFindings: [highlighted],
      draftSegments: [
        { slot: 's01', text: '来自 Draft 槽位的第一段。' },
        { slot: 's02', text: '来自 Draft 槽位的第二段。' },
      ],
    })
    renderScreen()

    expect(await screen.findByText(/在成稿里命中 2 处/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '只看成稿' }))
    expect(screen.getAllByText('来自 Draft 槽位', { selector: 'mark' })).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByDisplayValue('来自 Draft 槽位的第一段。')).toBeVisible()
    expect(screen.getByDisplayValue('来自 Draft 槽位的第二段。')).toBeVisible()
  })

  it('展示 current trim audit，并经同源 preview + 专用 retain 路由销账', async () => {
    sessionStorage.clear()
    await seedScreen({
      trimAudit: {
        output_ordinal: 3,
        reason: '正文超出预算，但机械裁剪会删除关键交付',
        context: '当前正文 2200 字；候选会移除结尾承诺。',
      },
    })
    const previewSpy = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => freePreview(req))
    const retainSpy = vi.spyOn(api, 'retainPretrim').mockImplementation(async () =>
      finishedJob({ kind: 'stage5.review', label: '保留未裁剪正文' }, 'trim-retained'))
    const { queryClient } = renderScreen()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    expect(await screen.findByText('正文超出预算，但机械裁剪会删除关键交付')).toBeVisible()
    expect(screen.getByText('当前正文 2200 字；候选会移除结尾承诺。')).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: /自报身份 actor/ }), {
      target: { value: ' reviewer-a ' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: /理由 reason/ }), {
      target: { value: ' 已逐段核对，决定保留原稿 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /确认保留未裁剪正文/ }))

    expect(await screen.findByText(/--retain-pretrim --chapters 3 --actor reviewer-a/)).toBeVisible()
    expect(retainSpy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '发起' }))

    await waitFor(() => expect(retainSpy).toHaveBeenCalledWith(SAMPLE_SID, 3, {
      actor: 'reviewer-a',
      reason: '已逐段核对，决定保留原稿',
    }))
    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.review',
      extra: ['--retain-pretrim'],
      args: [
        '--chapters', '3',
        '--actor', 'reviewer-a',
        '--reason', '已逐段核对，决定保留原稿',
      ],
    }))
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: qk.stage5(SAMPLE_SID, 3) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: qk.inbox(SAMPLE_SID) })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: qk.jobs(SAMPLE_SID) })
  })

  it('wontfix 只在后台作业终态后重读书数据，并以服务端审计解除 submitted 占位', async () => {
    const bookFinding = finding('source_overlap_high', '全书源文重合核验', 'book-key|part-2', null)
    const seeded = await seedScreen({ bookFindings: [bookFinding] })
    const landedFinding = finding(
      'source_overlap_high',
      '全书源文重合核验',
      'book-key|part-2',
      null,
      { why: '逐项核对后接受这个读数', at: 'review_7' },
    )
    const landedReview: ReviewResponse = {
      ...seeded.reviewResponse,
      book: [landedFinding],
    }
    let phase: 'idle' | 'running' | 'terminal' = 'idle'
    seeded.reviewSpy.mockImplementation(async () =>
      phase === 'terminal' ? landedReview : seeded.reviewResponse)

    const previewSpy = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => freePreview(req))
    const startSpy = vi.spyOn(api, 'startJob').mockImplementation(async (req) => {
      phase = 'running'
      return {
        ...finishedJob(req, 'wontfix-started'),
        state: 'running',
        finished: null,
        ordinal: null,
      }
    })
    const runningJob = {
      ...finishedJob({ kind: 'stage5.review', label: 'wontfix · 全书源文重合核验' }, 'wontfix-started'),
      state: 'running' as const,
      finished: null,
      ordinal: null,
    }
    seeded.jobsSpy.mockImplementation(async () => {
      if (phase === 'idle') return { jobs: [], running: null }
      if (phase === 'running') return { jobs: [runningJob], running: runningJob }
      return {
        jobs: [{ ...runningJob, state: 'ok' as const, finished: 2 }],
        running: null,
      }
    })
    let terminal: (() => void) | null = null
    vi.spyOn(api, 'streamJobLog').mockImplementation((_id, _offset, sink) => {
      terminal = () => {
        phase = 'terminal'
        sink.onEvent({
          text: 'wontfix 已写入\n',
          next_byte: 18,
          done: true,
          state: 'ok',
        })
      }
      return vi.fn()
    })
    renderScreen({ withJobDrawer: true, withInboxProbe: true })

    fireEvent.click(await screen.findByRole('button', { name: '全书 1 条' }))
    expect(screen.getByText(/book\|source_overlap_high\|book-key\|part-2/)).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: 'wontfix 理由' }), {
      target: { value: ' 逐项核对后接受这个读数 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /记为 wontfix/ }))
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))

    await waitFor(() => expect(startSpy).toHaveBeenCalledTimes(1))
    const expectedSpec = 'book|source_overlap_high|book-key|part-2=逐项核对后接受这个读数'
    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.review',
      extra: ['--wontfix'],
      args: [expectedSpec],
    }))
    expect(startSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.review',
      extra: ['--wontfix'],
      args: [expectedSpec],
    }))
    // 发起成功只说明后台 job=running。此时 Review 仍是旧字节，界面必须继续显示等待，
    // 不能把 start 200 当成 wontfix 已经落盘。
    expect(await screen.findByText('这条 wontfix 作业已经发起，等待作业结束后刷新报告')).toBeVisible()
    expect(screen.getByRole('button', { name: /记为 wontfix/ })).toBeDisabled()
    await waitFor(() => expect(terminal).not.toBeNull())
    await waitFor(() => expect(seeded.reviewSpy.mock.calls.length).toBeGreaterThanOrEqual(2))
    const reviewCallsBeforeTerminal = seeded.reviewSpy.mock.calls.length
    const stage5CallsBeforeTerminal = seeded.stage5Spy.mock.calls.length
    const inboxCallsBeforeTerminal = seeded.inboxSpy.mock.calls.length

    await act(async () => {
      terminal?.()
    })

    expect(await screen.findByText('已有审计：逐项核对后接受这个读数（review_7）')).toBeVisible()
    expect(screen.getByText('已 wontfix：逐项核对后接受这个读数')).toBeVisible()
    expect(screen.getByText('这条 finding 已经有 wontfix 审计记录')).toBeVisible()
    expect(screen.queryByText('这条 wontfix 作业已经发起，等待作业结束后刷新报告')).not.toBeInTheDocument()
    await waitFor(() => expect(seeded.reviewSpy.mock.calls.length).toBeGreaterThan(reviewCallsBeforeTerminal))
    await waitFor(() => expect(seeded.stage5Spy.mock.calls.length).toBeGreaterThan(stage5CallsBeforeTerminal))
    await waitFor(() => expect(seeded.inboxSpy.mock.calls.length).toBeGreaterThan(inboxCallsBeforeTerminal))
  })

  it('非 fresh Review 在界面上关闭 wontfix 入口', async () => {
    await seedScreen({
      reviewState: 'stale',
      chapterFindings: [finding('length_report', '长度报告', 'ch3:length', 3)],
    })
    vi.spyOn(api, 'previewJob').mockImplementation(async (req) => freePreview(req))
    renderScreen()

    expect(await screen.findByText('只有 fresh Review 的 finding 可以提交 wontfix')).toBeVisible()
    expect(screen.getByRole('button', { name: /记为 wontfix/ })).toBeDisabled()
  })
})
