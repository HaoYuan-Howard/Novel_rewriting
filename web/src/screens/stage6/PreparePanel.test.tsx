import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { missingPrepareState } from '@/test/prepare'
import type { PrepareWorkpack, PrepareState } from '@/types/prepare'
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
      state: 'pending', reason: '等待机械自动接受', subject_sha256: hash('a'), review_token: 's6p1_' + hash('b'),
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
  it('只读展示三类支持与未决开篇，不提供人工接受面', async () => {
    renderPanel()
    expect(await screen.findByText('待自动接受')).toBeInTheDocument()
    expect(screen.getByText('f1 · 正文支持')).toBeInTheDocument()
    expect(screen.getByText('f2 · 原钩子独立支持')).toBeInTheDocument()
    expect(screen.getByText('f3 · 未决事实')).toBeInTheDocument()
    expect(screen.getByText(/范围未决：需要核对/)).toBeInTheDocument()
    expect(screen.getByText(/机械校验通过后由系统自动接受/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /提交准备决定/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('准备决定')).not.toBeInTheDocument()
    expect(api.getStage6PrepareWorkpack).not.toHaveBeenCalled()
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

  it('已接受状态只读展示最近决定，不提供编辑写面', async () => {
    const status: PrepareState = {
      ...pack().state,
      state: 'accepted',
      reason: 'system:stage6-prepare-auto 已接受',
      review: {
        actor: 'system:stage6-prepare-auto', reason: '机械通过自动接受',
        source_sha256: hash('a'), request_sha256: hash('e'), decision: 'accept',
        at: '2026-09-15T00:00:00Z',
      },
    }
    vi.mocked(api.getStage6Prepare).mockResolvedValue({ sid: SID, title: '准备测试', status })
    renderPanel()
    expect(await screen.findByText('准备已接受')).toBeInTheDocument()
    expect(screen.getAllByText(/system:stage6-prepare-auto/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /修订事实支持/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /写回准备修订/ })).not.toBeInTheDocument()
  })

  it('提交待补尾时仅消费后端给出的免费恢复动作', async () => {
    const { qc } = renderPanel()
    await screen.findByText('待自动接受')
    const status: PrepareState = { ...pack().state, pending_commit: true, reason: '等待原提交补尾' }
    vi.mocked(api.getStage6Prepare).mockResolvedValue({ sid: SID, title: '准备测试', status })
    const action = { kind: 'stage6.prepare', label: '恢复准备修订', extra: ['--recover-edit'] }
    host.doc.spawns.s5 = [action]
    await qc.invalidateQueries({ queryKey: qk.stage6Prepare(SID), exact: true })
    await waitFor(() => expect(screen.getByText(/已有保存的修订等待恢复/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /恢复准备修订/ }))
    expect(host.ask).toHaveBeenCalledWith({ ...action, sid: SID, ordinal: null, required_inputs: undefined })
  })
})
