import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { missingPrepareState } from '@/test/prepare'
import type { PrepareWorkpack, PrepareState } from '@/types/prepare'
import type { Job } from '@/types/jobs'
import { PreparePanel } from './PreparePanel'

const host = vi.hoisted(() => ({
  ask: vi.fn(),
  doc: { gate: { s5: true }, spawns: { s5: [] as { kind: string; label: string; extra: string[] | null }[] } },
}))
vi.mock('@/hooks/useApprovals', () => ({ useApprovals: () => ({ doc: host.doc }) }))
vi.mock('@/hooks/useChrome', () => ({ useAskJob: () => host.ask }))
vi.mock('@/hooks/useEnums', () => ({ useEnums: () => ({ data: {
  actions: [{ kind: 'stage6.prepare', extra: ['--recover-edit'], paid: false, writes_disk: true }],
  job_kinds: {},
} }) }))

const SID = 'prepare-book'
const hash = (c: string) => c.repeat(64)

function pack(): PrepareWorkpack {
  const source_blurb: PrepareWorkpack['edit_template']['source_blurb'] = {
    facts: [
      { kind: 'supported', claim_id: 'f1', claim: '她搬离旧宅', evidence: { chapter_ordinal: 1, start_para: 1, end_para: 1 } },
      { kind: 'hook_supported', claim_id: 'f2', claim: '她在旧宅住了三年', hook_evidence: { start_para: 1, end_para: 1 } },
      { kind: 'unresolved', claim_id: 'f3', claim: '宅子归谁所有', reason: '原钩子与正文没有明确归属' },
    ],
    opening: { kind: 'unresolved', reason: '需要核对开篇情节的结束位置' },
  }
  return {
    sid: SID, title: '准备测试', review_contract: 'stage6-prepare-review-v1',
    main_text: [{ chapter_ordinal: 1, text: '她收拾行李，搬离旧宅。' }], hook_copy: { text: '三年后，她终于走出旧宅。' },
    state: {
      state: 'pending', reason: '尚无准备接受', subject_sha256: hash('a'), review_token: 's6p1_' + hash('b'),
      review: null, pending_commit: false,
      prepare: { main_text_sha256: hash('c'), hook_copy_sha256: hash('d'),
        _provenance: { root_clean_sha1: '0'.repeat(40) },
        source_blurb: {
          facts: [
            { kind: 'supported', claim_id: 'f1', claim: '她搬离旧宅', evidence: { chapter_ordinal: 1, source: { start: 0, end: 5, text: '正文证据片段' } } },
            { kind: 'hook_supported', claim_id: 'f2', claim: '她在旧宅住了三年', hook_evidence: { source: { start: 0, end: 3, text: '原钩子证据片段' }, unresolved_locator: null } },
            { kind: 'unresolved', claim_id: 'f3', claim: '宅子归谁所有', reason: '原钩子与正文没有明确归属' },
          ], opening: { kind: 'unresolved', reason: '需要核对开篇情节的结束位置' },
        },
      },
    },
    edit_template: { actor: '待填', reason: '待填', review_token: 's6p1_' + hash('b'), source_sha256: hash('a'),
      main_text_sha256: hash('c'), hook_copy_sha256: hash('d'), source_blurb },
    decision_template: { actor: '待填', reason: '待填', source_sha256: hash('a'), review_token: 's6p1_' + hash('b'),
      decision: 'uncertain', edit: null },
  }
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const result = render(<QueryClientProvider client={qc}><PreparePanel sid={SID} /></QueryClientProvider>)
  return { ...result, qc }
}

function job(): Job {
  return { id: 'prepare-edit', sid: SID, kind: 'stage6.prepare', label: '准备修订', ordinal: null,
    argv: 'opaque backend command', paid: false, state: 'ok', started: 1, finished: 2 }
}

beforeEach(() => {
  vi.restoreAllMocks()
  host.ask.mockClear()
  host.doc.gate.s5 = true
  host.doc.spawns.s5 = []
  const value = pack()
  vi.spyOn(api, 'getStage6Prepare').mockResolvedValue({ sid: SID, title: value.title, status: value.state })
  vi.spyOn(api, 'getStage6PrepareWorkpack').mockResolvedValue(value)
})

describe('独立文案准备', () => {
  it('无 Hook 时仍区分三类支持与未决开篇，不默认接受', async () => {
    renderPanel()
    expect(await screen.findByText('待接受')).toBeInTheDocument()
    expect(screen.getByText('f1 · 正文支持')).toBeInTheDocument()
    expect(screen.getByText('f2 · 原钩子独立支持')).toBeInTheDocument()
    expect(screen.getByText('f3 · 未决事实')).toBeInTheDocument()
    expect(screen.getByText(/范围未决：需要核对/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '提交准备决定（不调用模型）' })).toBeDisabled()
    expect(api.getStage6PrepareWorkpack).toHaveBeenCalledWith(SID)
    expect(host.ask).not.toHaveBeenCalled()
  })

  it.each(['missing', 'unknown'] as const)('%s 没有材料时不造审阅模板', async (state) => {
    vi.mocked(api.getStage6Prepare).mockResolvedValue({ sid: SID, title: '准备测试', status: {
      ...missingPrepareState(), state, reason: state === 'missing' ? '尚未生成' : '原来源无法验证',
    } })
    renderPanel()
    expect(await screen.findByText(state === 'missing' ? '尚未准备' : '准备状态无法验证')).toBeInTheDocument()
    expect(api.getStage6PrepareWorkpack).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('准备决定')).not.toBeInTheDocument()
  })

  it('只提交同包 opaque token 与显式决定，HTTP 成功后仍显示后端待接受', async () => {
    const review = vi.spyOn(api, 'reviewStage6Prepare').mockResolvedValue(job())
    renderPanel()
    fireEvent.change(await screen.findByLabelText('准备审阅理由'), { target: { value: '已逐条核对原文' } })
    fireEvent.change(screen.getByLabelText('准备决定'), { target: { value: 'accept' } })
    fireEvent.click(screen.getByRole('button', { name: '提交准备决定（不调用模型）' }))
    await waitFor(() => expect(review).toHaveBeenCalledWith(SID, {
      review_token: pack().state.review_token, actor: 'human-ui', reason: '已逐条核对原文', decision: 'accept', edit: null,
    }))
    await waitFor(() => expect(api.getStage6Prepare).toHaveBeenCalledTimes(2))
    expect(screen.getByText('待接受')).toBeInTheDocument()
    expect(screen.queryByText('准备已接受')).not.toBeInTheDocument()
    expect(host.ask).not.toHaveBeenCalled()
  })

  it('预检后改字会禁用写回，再预检原样提交快照且刷新材料', async () => {
    const preview = vi.spyOn(api, 'previewStage6PrepareEdit').mockResolvedValue({ would_apply: true, findings: [], source_blurb: pack().state.prepare!.source_blurb })
    const apply = vi.spyOn(api, 'applyStage6PrepareEdit').mockResolvedValue(job())
    renderPanel()
    fireEvent.change(await screen.findByLabelText('准备审阅理由'), { target: { value: '订正主张表述' } })
    fireEvent.click(screen.getByRole('button', { name: '修订事实支持或开篇范围' }))
    const write = screen.getByRole('button', { name: '写回准备修订（不调用模型）' })
    expect(write).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '免费预检准备修订' }))
    await waitFor(() => expect(write).toBeEnabled())
    fireEvent.change(screen.getByLabelText('准备主张 f1'), { target: { value: '她独自搬离旧宅' } })
    expect(write).toBeDisabled()
    expect(screen.getByText('内容已改变，请重新预检。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '免费预检准备修订' }))
    await waitFor(() => expect(write).toBeEnabled())
    fireEvent.click(write)
    await waitFor(() => expect(apply).toHaveBeenCalledWith(SID, preview.mock.calls[1][1]))
    await waitFor(() => expect(api.getStage6Prepare).toHaveBeenCalledTimes(2))
  })

  it('过期 token 的 409 保留后端原因与恢复提示，不显示接受', async () => {
    vi.spyOn(api, 'reviewStage6Prepare').mockRejectedValue(new ApiError('server', '/prepare/review', {
      status: 409, message: '准备材料或决定已变化', hint: '重新读取当前工作包', backendKind: 'SourceConflict',
    }))
    renderPanel()
    fireEvent.change(await screen.findByLabelText('准备审阅理由'), { target: { value: '核对旧材料' } })
    fireEvent.change(screen.getByLabelText('准备决定'), { target: { value: 'accept' } })
    fireEvent.click(screen.getByRole('button', { name: '提交准备决定（不调用模型）' }))
    expect(await screen.findByText('准备材料或决定已变化')).toBeInTheDocument()
    expect(screen.getByText('重新读取当前工作包')).toBeInTheDocument()
    expect(screen.queryByText('准备已接受')).not.toBeInTheDocument()
  })

  it('提交待补尾时隐藏缓存旧表单，仅消费后端给出的免费恢复动作', async () => {
    const { qc } = renderPanel()
    await screen.findByLabelText('准备决定')
    const status: PrepareState = { ...pack().state, pending_commit: true, reason: '等待原提交补尾' }
    vi.mocked(api.getStage6Prepare).mockResolvedValue({ sid: SID, title: '准备测试', status })
    const action = { kind: 'stage6.prepare', label: '恢复准备修订', extra: ['--recover-edit'] }
    host.doc.spawns.s5 = [action]
    await qc.invalidateQueries({ queryKey: qk.stage6Prepare(SID), exact: true })
    await waitFor(() => expect(screen.queryByLabelText('准备决定')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /恢复准备修订/ }))
    expect(host.ask).toHaveBeenCalledWith({ ...action, sid: SID, ordinal: null, required_inputs: undefined })
  })
})
