import { missingPrepareState } from '@/test/prepare'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { ChromeProvider } from '@/hooks/useChrome'
import type {
  ApprovalDoc,
  Stage5ClosureTemplate,
  Stage5ClosureWorkpack,
} from '@/types/approvals'
import type { Job, JobPreview, JobRequest } from '@/types/jobs'
import { Stage5ClosurePanel } from './Stage5ClosurePanel'

const SID = 'closure-book'
const sha = (char: string) => char.repeat(64)

function workpack({ trim = false, mustAppear = false } = {}): Stage5ClosureWorkpack {
  return {
    review_seq: 9,
    review_rel: 'stage5/review_9.json',
    review_sha256: sha('9'),
    planned_chapters: [1, 2],
    missing_chapters: [],
    chapter_subjects: [
      { output_ordinal: 1, chapter_sha256: sha('1') },
      { output_ordinal: 2, chapter_sha256: sha('2') },
    ],
    invalid_chapters: [],
    hard_findings: [],
    unresolved_trim_chapters: trim ? [1, 2] : [],
    unresolved_trim_items: trim ? [
      { output_ordinal: 1, audit_sha256: sha('a'), chapter_sha256: sha('1') },
      { output_ordinal: 2, audit_sha256: sha('b'), chapter_sha256: sha('2') },
    ] : [],
    trim_subjects: trim ? [
      { output_ordinal: 1, audit_sha256: sha('a'), chapter_sha256: sha('1'), resolution_sha256: null },
      { output_ordinal: 2, audit_sha256: sha('b'), chapter_sha256: sha('2'), resolution_sha256: null },
    ] : [],
    wontfix_sha256: null,
    must_appear: mustAppear ? [{
      chapter_ordinal: 1,
      evidence_key: 'ch1:must-appear',
      detail: '这一项必须由 Commander 逐项核对正文承载。',
      finding_sha256: sha('f'),
      chapter_sha256: sha('1'),
    }] : [],
    fact_group: mustAppear ? {
      group: 'fact_landing',
      findings_sha256: sha('6'),
      items: 1,
      rules: ['fact_landing_unver'],
    } : null,
    unver_group: null,
    disclosure: mustAppear ? { fact_landing_unver: 1 } : {},
    resolved: {},
    workpack_sha256: sha('e'),
    review_token: 's5cr1_' + sha('e'),
  }
}

function approvalDoc({
  state,
  pack,
  nextStep = null,
}: {
  state: string
  pack: Stage5ClosureWorkpack | null
  nextStep?: string | null
}): ApprovalDoc {
  return {
    v: 8, prepare: missingPrepareState(),
    sid: SID,
    gate: { s0: true, s1: true, s2: true, s4: true, s5: false, s6: false, s7: false },
    seen: { read: true, global: true },
    writing: true,
    design_review: {
      state: 'accepted',
      candidate_sha256: sha('c'),
      aggregate_subject_sha256: sha('d'),
      current_decision: null,
      history: [],
      binding_infeasibility: null,
      preflight: null,
      repair_capability: null,
    },
    structure: {
      state: 'accepted',
      current_subject_sha256: sha('5'),
      current_decision: null,
      history: [],
      binding_concerns: [],
    },
    writing_approval: {
      available: true,
      current_subject_sha256: sha('5'),
      current_decision: null,
      history: [],
    },
    closure: {
      state,
      workpack: pack,
      next_step: nextStep,
      current_decision: null,
      history: [],
    },
    spawns: {},
  }
}

function finishedJob(id: string): Job {
  return {
    id,
    kind: 'stage5.review',
    label: 'Stage5 人工闭合动作',
    argv: 'python -m novel.cli stage5.review',
    state: 'ok',
    paid: false,
    started: 1,
    finished: 2,
    sid: SID,
    ordinal: null,
  }
}

function freePreview(request: JobRequest): JobPreview {
  const flags = [...(request.extra ?? []), ...(request.args ?? [])].join(' ')
  const argv = ['python -m novel.cli', request.kind, flags].filter(Boolean).join(' ')
  return {
    argv,
    paid: false,
    label: request.label,
    kind: request.kind,
    steps: [{
      label: request.label,
      paid: false,
      writes: '',
      argv,
    }],
  }
}

function renderPanel({ confirm = false } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={['/n/' + SID + '/stage5/1']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <ChromeProvider>
            <Routes>
              <Route
                path="/n/:sid/stage5/:o"
                element={
                  <>
                    <Stage5ClosurePanel sid={SID} onJumpChapter={vi.fn()} />
                    {confirm ? <ConfirmDialog /> : null}
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

beforeEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('Stage5 Commander 闭合面', () => {
  it('显式呈现 review_required，只给一次最终 Review，不拿旧工作包冒充 ready', async () => {
    const doc = approvalDoc({
      state: 'review_required',
      pack: null,
      nextStep: '当前 Review 已 stale；完成多次编辑后只跑一次最终 Review。',
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    const templateSpy = vi.spyOn(api, 'getStage5ClosureTemplate')
    const previewSpy = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => freePreview(req))
    renderPanel({ confirm: true })

    expect(await screen.findByText('当前没有可用于闭合的 fresh 最终 Review')).toBeVisible()
    expect(screen.getByText(/当前 Review 已 stale/)).toBeVisible()
    expect(screen.queryByText(/workpack [a-z0-9]{64}/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /生成 Commander 回执模板/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /多次改稿完成，跑一次最终 Review/ }))

    await waitFor(() => expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.review',
      extra: null,
    })))
    expect(templateSpy).not.toHaveBeenCalled()
  })

  it('批量 trim 只复制 workpack 的两枚 SHA，并提交共享与单项理由', async () => {
    const pack = workpack({ trim: true })
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approvalDoc({ state: 'blocked', pack }))
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    const previewSpy = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => freePreview(req))
    const batchSpy = vi.spyOn(api, 'retainPretrimBatch').mockResolvedValue(finishedJob('trim-batch'))
    renderPanel({ confirm: true })

    expect(await screen.findByText('批量销账 current trim audit')).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: /自报身份 actor/ }), {
      target: { value: ' commander-b ' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: /理由 reason/ }), {
      target: { value: ' 全书逐章核对后保留 pretrim ' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: '第 2 章 trim 单项理由' }), {
      target: { value: ' 第 2 章另行核对结尾承诺 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /批量确认保留 pretrim/ }))

    expect(await screen.findByText(/--retain-pretrim-batch \(服务端一次性 manifest\)/)).toBeVisible()
    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.review',
      extra: ['--retain-pretrim-batch'],
      args: ['(服务端一次性 manifest)'],
    }))
    expect(batchSpy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '发起' }))

    await waitFor(() => expect(batchSpy).toHaveBeenCalledWith(SID, {
      actor: 'commander-b',
      reason: '全书逐章核对后保留 pretrim',
      items: [
        { output_ordinal: 1, audit_sha256: sha('a'), chapter_sha256: sha('1'), reason: '' },
        {
          output_ordinal: 2,
          audit_sha256: sha('b'),
          chapter_sha256: sha('2'),
          reason: '第 2 章另行核对结尾承诺',
        },
      ],
    }))
  })

  it('真实读取关闭式模板，并原样提交逐项 ruling、整组 ack 与工作包身份', async () => {
    const pack = workpack({ mustAppear: true })
    const doc = approvalDoc({ state: 'ready', pack })
    const template: Stage5ClosureTemplate = {
      actor: '',
      reason: '',
      review_token: pack.review_token,
      workpack_sha256: pack.workpack_sha256,
      review_sha256: pack.review_sha256,
      must_appear: [{
        chapter_ordinal: 1,
        evidence_key: 'ch1:must-appear',
        finding_sha256: sha('f'),
        chapter_sha256: sha('1'),
        ruling: 'accepted_exception',
        reason: '关闭式模板默认值，不可直接提交',
        placeholder: true,
        template_token: sha('7'),
      }],
      group_acks: [{
        group: 'fact_landing',
        findings_sha256: sha('6'),
        items: 1,
        placeholder: true,
        template_token: sha('8'),
      }],
    }
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    const templateSpy = vi.spyOn(api, 'getStage5ClosureTemplate').mockResolvedValue(template)
    const submitSpy = vi.spyOn(api, 'approveStage5Closure').mockResolvedValue(doc)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /生成 Commander 回执模板/ }))
    await waitFor(() => expect(templateSpy).toHaveBeenCalledWith(SID))
    fireEvent.change(await screen.findByRole('combobox', { name: '第 1 章 must_appear 裁决' }), {
      target: { value: 'confirmed_present' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /已核对第 1 章 ch1:must-appear/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: '确认整组 fact_landing' }))
    fireEvent.change(screen.getByRole('textbox', { name: /自报身份 actor/ }), {
      target: { value: ' commander-c ' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: /理由 reason/ }), {
      target: { value: ' 已完成全书 Commander 闭合核对 ' },
    })
    const submit = screen.getByRole('button', { name: /提交 Stage5 Commander 回执/ })
    expect(submit).toHaveAccessibleName(/提交 Stage5 Commander 回执.*写盘/)
    fireEvent.click(submit)

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith(SID, {
      actor: 'commander-c',
      reason: '已完成全书 Commander 闭合核对',
      review_token: 's5cr1_' + sha('e'),
      must_appear: [{
        chapter_ordinal: 1,
        evidence_key: 'ch1:must-appear',
        ruling: 'confirmed_present',
        reason: '',
      }],
      group_acks: [{
        group: 'fact_landing',
        acknowledged: true,
      }],
    }))
  })
})
