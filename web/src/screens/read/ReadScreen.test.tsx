import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, PLOT_SAMPLE_SID } from '@/api/mock'
import type { BlueprintReadingWorkpack } from '@/types/stages'
import { ReadScreen } from './ReadScreen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
const SID = 'book-1'
afterEach(() => vi.restoreAllMocks())
let cachedWorkpack: BlueprintReadingWorkpack | null = null
let cachedFirstReading: import('@/types/stages').ReadingResponse | null = null
async function workpack(): Promise<BlueprintReadingWorkpack> {
  cachedWorkpack ??= { ...(await mockApi.getBlueprintReadingWorkpack(PLOT_SAMPLE_SID, 'chapter:1')), sid: SID }
  return cachedWorkpack
}
async function stubApi({ missing = false } = {}) {
  const [chapterPack, crossPack, firstReading] = await Promise.all([
    mockApi.getBlueprintReadingWorkpack(PLOT_SAMPLE_SID, 'chapter:1'),
    mockApi.getBlueprintReadingWorkpack(PLOT_SAMPLE_SID, 'cross_chapter'),
    mockApi.getReading(PLOT_SAMPLE_SID, 1),
  ]) as [BlueprintReadingWorkpack, BlueprintReadingWorkpack, import('@/types/stages').ReadingResponse]
  cachedFirstReading ??= firstReading
  const getReading = vi.spyOn(api, 'getReading').mockImplementation(async (_sid, ordinal) => ({
    ...firstReading, sid: SID, chapter_ordinal: ordinal,
    text: crossPack.material.sources.find((s) => s.chapter_ordinal === ordinal)!.text,
    blueprint: missing ? null : crossPack.material.blueprints.find((s) => s.chapter_ordinal === ordinal)!,
  }))
  vi.spyOn(api, 'getNovel').mockResolvedValue({
    sid: SID, title: '完整剧情测试', dir: SID, dirname: SID, chapters: 3, stages: {},
    stage_done: {}, stage_reviewable: {}, chapters_done: {},
    summary: { verdict: 'pending', text: '', detail: '', artifact_count: 0, latest_mtime: null },
  })
  vi.spyOn(api, 'getApprovals').mockResolvedValue(await mockApi.getApprovals(SID))
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const getPack = vi.spyOn(api, 'getBlueprintReadingWorkpack').mockImplementation(async (_sid, scope) => {
    if (scope === 'cross_chapter') return { ...crossPack, sid: SID }
    if (scope !== 'chapter:1') throw new Error('该章测试材料尚未录制')
    return { ...chapterPack, sid: SID }
  })
  return { getReading, getPack }
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
function firstReadingText(): string {
  return cachedFirstReading?.text ?? ''
}

describe('Blueprint 章级与跨章阅读', () => {
  it('进入和换章只读取对应范围的阅读材料，不提供任何审阅提交', async () => {
    const { getReading, getPack } = await stubApi()
    renderRead()
    await screen.findByText('剧情节点 1')
    expect(getPack).toHaveBeenCalledWith(SID, 'chapter:1')
    expect(screen.queryByRole('button', { name: '提交范围审阅决定' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一章' }))
    await waitFor(() => expect(getReading).toHaveBeenCalledWith(SID, 2))
    await waitFor(() => expect(getPack).toHaveBeenCalledWith(SID, 'chapter:2'))
  })
  it('同一工作包覆盖独立 reading 的旧正文和旧节点', async () => {
    const { getReading } = await stubApi()
    const first = cachedFirstReading!
    getReading.mockResolvedValue({ ...first, text: '旧正文不应被签署',
      blueprint: { ...first.blueprint!, beats: [{ ...first.blueprint!.beats[0], content: '旧节点不应被签署' }] } })
    renderRead()
    const pack = await workpack()
    expect(await screen.findByText(pack.material.blueprints[0].beats[0].content)).toBeVisible()
    expect(screen.getByText(pack.material.sources[0].text, { normalizer: (text) => text })).toBeVisible()
    expect(screen.queryByText('旧正文不应被签署')).not.toBeInTheDocument()
    expect(screen.queryByText('旧节点不应被签署')).not.toBeInTheDocument()
  })
  it('明确选择跨章范围后展示全书原文和节点，进度只读后端', async () => {
    const { getPack } = await stubApi()
    renderRead()
    await screen.findByText('剧情节点 1')
    fireEvent.change(screen.getByLabelText('蓝图阅读范围'), { target: { value: 'cross_chapter' } })
    expect(await screen.findByText(/已生成 3 \/ 3 章/)).toBeVisible()
    expect(getPack).toHaveBeenCalledWith(SID, 'cross_chapter')
    const crossPack = await mockApi.getBlueprintReadingWorkpack(PLOT_SAMPLE_SID, 'cross_chapter')
    for (const chapter of crossPack.material.blueprints) {
      expect(screen.getByText(chapter.beats[0].content)).toBeVisible()
    }
    expect(screen.queryByRole('button', { name: '提交范围审阅决定' })).not.toBeInTheDocument()
  })
  it('纯阅读面：只有阅读范围一个选择器，不出现逐维裁决或回执', async () => {
    await stubApi()
    renderRead()
    expect(await screen.findByLabelText('蓝图阅读范围')).toHaveValue('chapter')
    expect(screen.getAllByRole('combobox').length).toBe(1)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByText(/没有漏下的原文|loose 0/)).not.toBeInTheDocument()
    expect(screen.getAllByText((await workpack()).material.blueprints[0].beats[0].content)).toHaveLength(1)
  })
  it('蓝图缺席仍显示完整原文，不取阅读材料', async () => {
    const { getPack } = await stubApi({ missing: true })
    renderRead()
    expect(await screen.findByText(/蓝图尚不可读/)).toBeVisible()
    expect(screen.getByText(firstReadingText(), { normalizer: (text) => text })).toBeVisible()
    expect(getPack).not.toHaveBeenCalled()
  })
  it('阅读材料失败明确显示原因和重试，保留原文只读', async () => {
    const { getPack } = await stubApi()
    getPack.mockRejectedValue(new Error('材料在读取期间发生变化'))
    renderRead()
    expect(await screen.findByRole('alert')).toHaveTextContent('材料在读取期间发生变化')
    expect(screen.getByRole('button', { name: '重新读取' })).toBeEnabled()
  })
  it.each([false, true])('保留叙述身份与实际状态，明确区分合法空列表（空状态：%s）', async (empty) => {
    const { getPack } = await stubApi()
    const pack = await workpack()
    if (empty) {
      pack.material.blueprints[0].states = []
      pack.material.blueprints[0].entry_state_refs = []
      pack.material.blueprints[0].carry_out_refs = []
    }
    getPack.mockResolvedValue(pack)
    renderRead()
    await screen.findByText('剧情节点 1')
    fireEvent.click(screen.getByText(/持续状态（/))
    expect(screen.getByText(/"focalizer": \{/)).toBeVisible()
    if (empty) {
      await screen.findByText('没有登记新的持续状态。')
    } else {
      for (const state of pack.material.blueprints[0].states) {
        expect(screen.getByRole('article', { name: '持续状态 ' + state.state_id })).toHaveTextContent(state.content ?? state.global_ref!.ref)
      }
      expect(screen.queryByText('没有登记新的持续状态。')).not.toBeInTheDocument()
    }
  })
  it('持续状态按工作包展示成立、结束、替代和证据，不靠正文猜生命周期', async () => {
    const { getPack } = await stubApi()
    const pack = await workpack()
    pack.material.blueprints[0].states = [{
      state_id: 'st01_01', content: '李宝珠相信父亲已经回城。', global_ref: null,
      modality: 'belief', holder: { char_id: 'char_001', presentation_ref: null },
      established_at: { kind: 'at_entry', beat_ref: null },
      ended_at: { kind: 'at_beat', beat_ref: 'b01_02' },
      supersedes_state_refs: [], evidence_refs: ['e01_01'],
    }]
    pack.material.blueprints[0].entry_state_refs = ['st01_01']
    getPack.mockResolvedValue(pack)
    renderRead()
    await screen.findByText('剧情节点 1')
    fireEvent.click(screen.getByText(/持续状态（/))
    await screen.findByText(/成立：章首/)
    const article = screen.getByRole('article', { name: '持续状态 st01_01' })
    const state = within(article)
    expect(article).toHaveTextContent('成立：章首')
    expect(article).toHaveTextContent('结束：节点 b01_02')
    expect(state.getByText('取代状态：无登记项')).toBeVisible()
    expect(state.getByText('原文证据：第 3–4 段')).toBeVisible()
  })
})

describe('mock 阅读工作包与补录契约', () => {
  it('离线样本不伪造阅读工作包，补录只覆盖 s0/s1 两个人工闸', async () => {
    await mockApi.resetBook(SID)
    await expect(mockApi.getBlueprintReadingWorkpack(SID)).rejects.toMatchObject({ status: 501 })
    const filled = await mockApi.backfillApprovals(SID)
    // 历史示例没有当前 stage2 产物，不能套用新合成书的材料。
    expect(filled.backfilled).toEqual(['s0', 's1'])
    expect(filled.approvals.gate).toMatchObject({ s0: true, s1: true, s4: false })
    expect(filled.approvals.v).toBe(9)
    expect(filled.approvals).not.toHaveProperty('seen')
    expect(filled.approvals).not.toHaveProperty('stage2_reviews')
  })
})
