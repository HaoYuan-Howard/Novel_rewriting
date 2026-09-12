import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { MOCK_GLOBAL_SEEN_TOKEN, mockApi, plotReviewFixture } from '@/api/mock'
import type { BlueprintReviewWorkpack } from '@/types/stages'
import { ReadScreen } from './ReadScreen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
const SID = 'book-1'
afterEach(() => vi.restoreAllMocks())
function workpack(): BlueprintReviewWorkpack {
  return { ...plotReviewFixture().chapter, sid: SID }
}
async function stubApi({ missing = false } = {}) {
  const fixture = plotReviewFixture()
  const getReading = vi.spyOn(api, 'getReading').mockImplementation(async (_sid, ordinal) => ({
    ...fixture.reading, sid: SID, chapter_ordinal: ordinal,
    text: fixture.cross.material.sources.find((s) => s.chapter_ordinal === ordinal)!.text,
    blueprint: missing ? null : fixture.cross.material.blueprints.find((s) => s.chapter_ordinal === ordinal)!,
  }))
  vi.spyOn(api, 'getNovel').mockResolvedValue({
    sid: SID, title: '完整剧情测试', dir: SID, dirname: SID, chapters: 3, stages: {},
    stage_done: {}, stage_reviewable: {}, chapters_done: {},
    summary: { verdict: 'pending', text: '', detail: '', artifact_count: 0, latest_mtime: null },
  })
  vi.spyOn(api, 'getApprovals').mockResolvedValue(await mockApi.getApprovals(SID))
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const getPack = vi.spyOn(api, 'getBlueprintReviewWorkpack').mockImplementation(async (_sid, scope) => {
    if (scope === 'cross_chapter') return { ...fixture.cross, sid: SID }
    if (scope !== 'chapter:1') throw new Error('该章测试材料尚未录制')
    return workpack()
  })
  const markSeen = vi.spyOn(api, 'markSeen')
  const submit = vi.spyOn(api, 'approveStage2Review')
  return { getReading, getPack, markSeen, submit }
}
function renderRead() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={queryClient}>
    <MemoryRouter initialEntries={['/n/' + SID + '/read/1']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes><Route path="/n/:sid/read/:o" element={<ReadScreen />} /></Routes>
    </MemoryRouter>
  </QueryClientProvider>)
}
describe('Blueprint 章级与跨章阅读', () => {
  it('进入和换章只读取对应范围，不自动确认已读或接受', async () => {
    const { getReading, getPack, markSeen, submit } = await stubApi()
    renderRead()
    await screen.findByText('章节审阅决定')
    expect(getPack).toHaveBeenCalledWith(SID, 'chapter:1')
    expect(markSeen).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '下一章' }))
    await waitFor(() => expect(getReading).toHaveBeenCalledWith(SID, 2))
    await waitFor(() => expect(getPack).toHaveBeenCalledWith(SID, 'chapter:2'))
    expect(markSeen).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })
  it('同一工作包覆盖独立 reading 的旧正文和旧节点', async () => {
    const { getReading } = await stubApi()
    const first = plotReviewFixture().reading
    getReading.mockResolvedValue({ ...first, text: '旧正文不应被签署',
      blueprint: { ...first.blueprint!, beats: [{ ...first.blueprint!.beats[0], content: '旧节点不应被签署' }] } })
    renderRead()
    const pack = workpack()
    expect(await screen.findByText(pack.material.blueprints[0].beats[0].content)).toBeVisible()
    expect(screen.getByText(pack.material.sources[0].text, { normalizer: (text) => text })).toBeVisible()
    expect(screen.queryByText('旧正文不应被签署')).not.toBeInTheDocument()
    expect(screen.queryByText('旧节点不应被签署')).not.toBeInTheDocument()
  })
  it('明确选择跨章范围后展示全书原文和节点，接受数只读后端', async () => {
    const { getPack, submit } = await stubApi()
    renderRead()
    await screen.findByText('章节审阅决定')
    fireEvent.change(screen.getByLabelText('蓝图审阅范围'), { target: { value: 'cross_chapter' } })
    await screen.findByText('跨章复核决定')
    expect(getPack).toHaveBeenCalledWith(SID, 'cross_chapter')
    expect(screen.getByText(/有效接受 3 章/)).toBeVisible()
    for (const chapter of plotReviewFixture().cross.material.blueprints) {
      expect(screen.getByText(chapter.beats[0].content)).toBeVisible()
    }
    expect(submit).not.toHaveBeenCalled()
  })
  it('仅五维选择，不创建逐节点通过回执或覆盖率绿灯', async () => {
    await stubApi()
    renderRead()
    expect(await screen.findByLabelText('剧情完整')).toHaveValue('')
    expect(screen.getAllByRole('combobox').length).toBe(7)
    expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeDisabled()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByText(/没有漏下的原文|loose 0/)).not.toBeInTheDocument()
    expect(screen.getAllByText(workpack().material.blueprints[0].beats[0].content)).toHaveLength(1)
  })
  it('蓝图缺席仍显示完整原文，不取审阅包也不显示可提交表单', async () => {
    const { getPack } = await stubApi({ missing: true })
    renderRead()
    expect(await screen.findByText(/蓝图尚不可读/)).toBeVisible()
    expect(screen.getByText(plotReviewFixture().reading.text, { normalizer: (text) => text })).toBeVisible()
    expect(getPack).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '提交范围审阅决定' })).not.toBeInTheDocument()
  })
  it('工作包失败明确显示原因和重试，保留原文只读但无提交凭证', async () => {
    const { getPack, submit } = await stubApi()
    getPack.mockRejectedValue(new Error('材料在读取期间发生变化'))
    renderRead()
    expect(await screen.findByRole('alert')).toHaveTextContent('材料在读取期间发生变化')
    expect(screen.getByRole('button', { name: '重新读取' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '提交范围审阅决定' })).not.toBeInTheDocument()
    expect(submit).not.toHaveBeenCalled()
  })
  it('保留全部叙述身份、状态和约束供对照，不另造事实正文', async () => {
    await stubApi()
    renderRead()
    await screen.findByText('章节审阅决定')
    fireEvent.click(screen.getByText(/持续状态（/))
    expect(screen.getByText(/"focalizer_char_id": "char_001"/)).toBeVisible()
    expect(screen.getByText('没有登记新的持续状态。')).toBeVisible()
  })
  it('持续状态按工作包展示成立、结束、替代和证据，不靠正文猜生命周期', async () => {
    const { getPack } = await stubApi()
    const pack = workpack()
    pack.material.blueprints[0].states = [{
      state_id: 'st01_01', content: '李宝珠相信父亲已经回城。', global_ref: null,
      modality: 'belief', holder_char_id: 'char_001',
      established_at: { kind: 'at_entry', beat_ref: null },
      ended_at: { kind: 'at_beat', beat_ref: 'b01_02' },
      supersedes_state_refs: [], evidence_refs: ['e01_01'],
    }]
    pack.material.blueprints[0].entry_state_refs = ['st01_01']
    getPack.mockResolvedValue(pack)
    renderRead()
    await screen.findByText('章节审阅决定')
    fireEvent.click(screen.getByText(/持续状态（/))
    const article = screen.getByRole('article', { name: '持续状态 st01_01' })
    const state = within(article)
    expect(article).toHaveTextContent('成立：章首')
    expect(article).toHaveTextContent('结束：节点 b01_02')
    expect(state.getByText('取代状态：无登记项')).toBeVisible()
    expect(state.getByText('原文证据：第 3–4 段')).toBeVisible()
  })
})

describe('mock Blueprint 审阅审批契约', () => {
  it('离线样本不伪造 Blueprint 工作包或已读，global 仍复验当前 token 与确认人', async () => {
    await mockApi.resetBook(SID)
    const pack = workpack()
    const request = {
      review_token: pack.review_token,
    }

    await expect(mockApi.markSeen(SID, 'read', { review_token: 'bpr3_' + '0'.repeat(64) }))
      .rejects.toThrow(/当前 Blueprint 工作包/)
    await expect(mockApi.getBlueprintReviewWorkpack(SID)).rejects.toMatchObject({ status: 501 })
    await expect(mockApi.markSeen(SID, 'read', request)).rejects.toMatchObject({ status: 501 })
    expect((await mockApi.getApprovals(SID)).seen.read).toBe(false)
    await expect(mockApi.markSeen(SID, 'global', request)).rejects.toThrow(
      /seen\.global 必须携带当前 review 的 review_token/,
    )
    await expect(mockApi.markSeen(SID, 'global', {
      review_token: MOCK_GLOBAL_SEEN_TOKEN, actor: '   ',
    })).rejects.toThrow(/非空确认人/)
    await expect(mockApi.markSeen(SID, 'global', {
      review_token: 's2gr1_' + '0'.repeat(64), actor: '人工审阅者',
    })).rejects.toThrow(/当前 review 的 review_token/)
    await expect(mockApi.markSeen(SID, 'global', {
      review_token: MOCK_GLOBAL_SEEN_TOKEN, actor: '人工审阅者',
    })).rejects.toThrow(/当前 review 的 review_token/)
    expect((await mockApi.getApprovals(SID)).seen).toEqual({ read: false, global: false })
    const filled = await mockApi.backfillApprovals(SID)
    // 历史示例没有当前Core，不能套用新合成书的共享首抽或接受。
    expect(filled.approvals.gate).toMatchObject({ s2: false, s4: false })
  })

  it('backfill 不制造 seen，且没有双 seen 时只补到 s1', async () => {
    await mockApi.resetBook(SID)

    const result = await mockApi.backfillApprovals(SID)

    expect(result.approvals.seen).toEqual({ read: false, global: false })
    expect(result.backfilled).toEqual(['s0', 's1'])
    expect(result.approvals.gate).toMatchObject({
      s0: true,
      s1: true,
      s2: false,
      s4: false,
    })
  })
})
