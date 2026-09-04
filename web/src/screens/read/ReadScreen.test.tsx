import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { MOCK_GLOBAL_SEEN_TOKEN, mockApi } from '@/api/mock'
import type { ApprovalDoc } from '@/types/approvals'
import type { BlueprintReviewWorkpack, ReadingResponse } from '@/types/stages'
import { ReadScreen } from './ReadScreen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/hooks/useSample', () => ({
  useSampleCoverage: () => ({ hasSample: () => true }),
}))

const SID = 'book-1'

function approval(readSeen = false): ApprovalDoc {
  return {
    v: 6,
    sid: SID,
    gate: { s0: true, s1: true, s2: false, s4: false, s5: false, s6: false, s7: false },
    seen: { read: readSeen, global: false },
    writing: false,
    design_review: {
      state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
      current_decision: null, history: [], binding_infeasibility: null,
      preflight: null, repair_capability: null,
    },
    structure: {
      state: 'unavailable', current_subject_sha256: null,
      current_decision: null, history: [], binding_concerns: [],
    },
    writing_approval: {
      available: false, current_subject_sha256: null, current_decision: null, history: [],
    },
    closure: {
      state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
    },
    spawns: {},
  }
}

function reading(ordinal: number, blueprint = true): ReadingResponse {
  return {
    sid: SID,
    title: '测试书',
    chapter_ordinal: ordinal,
    text_chars: 4,
    blueprint,
    segments: [{
      start: 0, end: 4, chars: 4, text: '第' + ordinal + '章正文',
      beat_id: blueprint ? 'beat_' + ordinal : null,
      flagged: false,
    }],
    beats: blueprint ? [{
      beat_id: 'beat_' + ordinal,
      located: true,
      start: 0,
      end: 4,
      time_layer: 'present',
      place: 'B1 独立响应旧地点',
      present_char_ids: ['B1_在场人物'],
      fact_atoms: [{
        fact_id: 'b1_fact_' + ordinal,
        claim: 'B1 独立响应旧事实',
        kind: 'state',
        obligation: 'preserve',
        evidence_text: 'B1 独立响应旧证据',
        participant_char_ids: ['B1_事实人物'],
      }],
      hinges: [{ kind: 'b1_hinge', text: 'B1 独立响应旧铰链' }],
    }] : [],
    narration: blueprint ? [{
      grammatical_person: 'first',
      narrator_char_id: 'B1_旧叙述者',
      focalizer_char_id: 'B1_旧聚焦者',
      pronoun_surfaces: ['旧我'],
    }] : [],
    loose: blueprint ? 0 : 4,
    uncovered_min_chars: 20,
    blueprint_state: {
      rel: 'stage2/chapter_blueprints.json',
      exists: blueprint,
      bytes: blueprint ? 100 : null,
      mtime: blueprint ? 1 : null,
      freshness: blueprint ? 'fresh' : 'unknown',
      reason: blueprint ? 'fresh' : 'not_run',
      ghosts: [],
      invalid: false,
      invalid_freshness: null,
      invalid_reason: null,
      readable: blueprint,
    },
    _provenance: null,
  }
}

function workpack(): BlueprintReviewWorkpack {
  const chapter = (ordinal: number, anomalous: boolean) => ({
    chapter_ordinal: ordinal,
    anomalous,
    attention_rules: anomalous ? ['span_located'] : [],
    chapter_function: ordinal === 1 ? '建立前提' : '兑现转折',
    coverage: {
      text_chars: 4,
      covered_chars: 4,
      loose_chars: 0,
      uncovered_min_chars: 20,
      segments: [{ beat_order: 1, chars: 4, text: 'B2 工作包正文', flagged: false }],
    },
    beats: [{
      beat_order: 1,
      located: true,
      time_layer: 'present',
      place: 'B2 工作包地点',
      present_char_ids: ['B2_在场人物'],
      facts: [{
        claim: 'B2 工作包事实',
        kind: 'state',
        obligation: 'preserve',
        evidence_text: 'B2 工作包证据',
        participant_char_ids: ['B2_参与者甲', 'B2_参与者乙'],
      }],
      hinges: [{ kind: 'b2_hinge', text: 'B2 工作包铰链' }],
    }],
    causal_links: [],
    narrative_relations: ordinal === 1 ? [{
      kind: 'response_to' as const,
      enforcement: 'review' as const,
      stimulus_claim: '门外传来脚步声',
      response_claim: '甲立即回头',
    }] : [],
    entry_state_claims: ordinal === 1 ? ['前提成立'] : [],
    ending_state_claims: ordinal === 2 ? ['转折落地'] : [],
    narration: [{
      grammatical_person: 'first',
      narrator_char_id: 'B2_叙述者甲',
      focalizer_char_id: 'B2_聚焦者甲',
      pronoun_surfaces: ['我', '咱'],
    }, {
      grammatical_person: 'third',
      narrator_char_id: 'B2_叙述者乙',
      focalizer_char_id: 'B2_聚焦者乙',
      pronoun_surfaces: ['他', '她'],
    }],
  })
  return {
    sid: SID,
    title: '测试书',
    read_only: true,
    auto_decisions: false,
    review_contract_version: 'stage2-blueprint-review-v4',
    review_identity_sha256: 'e'.repeat(64),
    validation_seal_sha256: 'f'.repeat(64),
    review_token: 'bpr3_' + 'c'.repeat(64),
    subject_sha256: 'b'.repeat(64),
    binding_sha256: 'c'.repeat(64),
    binding: {
      blueprint_rel: 'stage2/chapter_blueprints.json',
      subject_kind: 'formal',
      main_text_artifact_sha256: 'a'.repeat(64),
      blueprint_artifact_sha256: 'b'.repeat(64),
      check_report_sha256: 'd'.repeat(64),
      chapter_ordinals: [1, 2],
    },
    check: {
      rules_scanned: 31,
      counts: { ok: 30, hit: 1, unver: 0, skipped: 0 },
      provenance: {
        freshness: 'fresh', reason: 'match', ghosts: [], edges: [], invalid: false,
        revalidated: null, origin: null,
      },
      attention: [],
      metrics: [],
    },
    accounting: [],
    mechanical_fields_hidden: ['blueprints[].beats[].beat_id'],
    chapters: [chapter(2, true), chapter(1, false)],
  }
}

function stubApi({
  readSeen = false,
  blueprint = true,
  subjectKind = 'formal' as 'formal' | 'invalid',
} = {}) {
  const getReading = vi.spyOn(api, 'getReading').mockImplementation(
    async (_sid, ordinal) => reading(ordinal, blueprint),
  )
  vi.spyOn(api, 'getNovel').mockResolvedValue({
    sid: SID,
    title: '测试书',
    dir: SID,
    dirname: SID,
    chapters: 2,
    stages: {},
    stage_done: {},
    stage_reviewable: {},
    chapters_done: {},
    summary: {
      verdict: 'pending', text: '', detail: '', artifact_count: 0, latest_mtime: null,
    },
  })
  vi.spyOn(api, 'getApprovals').mockResolvedValue(approval(readSeen))
  const getBlueprintReviewWorkpack = vi.spyOn(
    api, 'getBlueprintReviewWorkpack',
  ).mockImplementation(async () => {
    const pack = workpack()
    pack.binding.subject_kind = subjectKind
    return pack
  })
  const markSeen = vi.spyOn(api, 'markSeen').mockResolvedValue(approval(true))
  return { getReading, getBlueprintReviewWorkpack, markSeen }
}

function renderRead(path = '/n/' + SID + '/read/1') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[path]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/n/:sid/read/:o" element={<ReadScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ReadScreen 整本 Blueprint 审阅确认', () => {
  it('进入页面和切换章节都不会自动 POST seen.read', async () => {
    const { getReading, markSeen } = stubApi()
    renderRead()

    await screen.findByRole('button', { name: /确认已审阅本书 Blueprint/ })
    await waitFor(() => expect(
      screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ }),
    ).toBeEnabled())
    expect(markSeen).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '下一章' }))
    await waitFor(() => expect(getReading).toHaveBeenCalledWith(SID, 2))
    expect(await screen.findByText('原文 ↔ 骨架 · 第 2 章')).toBeVisible()
    expect(markSeen).not.toHaveBeenCalled()
  })

  it('明确说明确认整本而非当前章，仅点击后写 seen.read', async () => {
    const { markSeen } = stubApi()
    renderRead()

    expect(await screen.findByText(
      '这会确认你已按上方工作包审阅本书全部章节的 Blueprint，不是只确认当前第 1 章。',
    )).toBeVisible()
    await waitFor(() => expect(
      screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ }),
    ).toBeEnabled())
    const confirm = screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ })
    expect(markSeen).not.toHaveBeenCalled()

    fireEvent.click(confirm)

    await waitFor(() => expect(markSeen).toHaveBeenCalledWith(SID, 'read', {
      review_token: 'bpr3_' + 'c'.repeat(64),
    }))
    expect(await screen.findByText('已记录：本书 Blueprint 已整本审阅。')).toBeVisible()
    expect(screen.getByRole('button', { name: /已确认审阅本书 Blueprint/ })).toBeDisabled()
  })

  it('保存期间显示 pending 并防止重复提交', async () => {
    const { markSeen } = stubApi()
    let resolveMark!: (doc: ApprovalDoc) => void
    markSeen.mockImplementationOnce(() => new Promise((resolve) => {
      resolveMark = resolve
    }))
    renderRead()

    await screen.findByRole('button', { name: /确认已审阅本书 Blueprint/ })
    await waitFor(() => expect(
      screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ }),
    ).toBeEnabled())
    const confirm = screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ })
    fireEvent.click(confirm)

    expect(await screen.findByText('正在保存整本审阅确认…')).toBeVisible()
    expect(screen.getByRole('button', { name: /正在确认整本 Blueprint…/ })).toBeDisabled()
    expect(markSeen).toHaveBeenCalledOnce()

    await act(async () => resolveMark(approval(true)))
    expect(await screen.findByText('已记录：本书 Blueprint 已整本审阅。')).toBeVisible()
  })

  it('失败时显示明确错误并保留重试入口', async () => {
    const { markSeen } = stubApi()
    markSeen.mockRejectedValueOnce(new Error('审阅确认保存失败'))
    renderRead()

    await screen.findByRole('button', { name: /确认已审阅本书 Blueprint/ })
    await waitFor(() => expect(
      screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ }),
    ).toBeEnabled())
    const confirm = screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ })
    fireEvent.click(confirm)

    expect(await screen.findByText('确认失败：审阅确认保存失败')).toBeVisible()
    expect(screen.getByRole('button', { name: /确认已审阅本书 Blueprint/ })).toBeEnabled()
  })

  it('Blueprint 不存在或已 seen 时不允许再确认', async () => {
    const missing = stubApi({ blueprint: false })
    const first = renderRead()
    expect(await screen.findByRole('button', { name: /确认已审阅本书 Blueprint/ })).toBeDisabled()
    expect(screen.getByText('当前还没有可审阅的 Blueprint，不能确认。')).toBeVisible()
    expect(missing.markSeen).not.toHaveBeenCalled()
    first.unmount()

    vi.restoreAllMocks()
    const seen = stubApi({ readSeen: true })
    renderRead()
    expect(await screen.findByRole('button', { name: /已确认审阅本书 Blueprint/ })).toBeDisabled()
    expect(seen.markSeen).not.toHaveBeenCalled()
  })

  it('live INVALID 工作包只供修复阅读，不能写 formal seen proof', async () => {
    const invalid = stubApi({ subjectKind: 'invalid' })
    renderRead()

    expect(await screen.findByText(
      '尚未取得绑定当前正式 Blueprint 的完整工作包。',
    )).toBeVisible()
    expect(screen.getByRole('button', {
      name: /确认已审阅本书 Blueprint/,
    })).toBeDisabled()
    expect(invalid.markSeen).not.toHaveBeenCalled()
  })

  it('异常优先呈现且不把机械 id 当成人工审阅内容', async () => {
    stubApi()
    renderRead()

    expect(await screen.findByText('异常优先审阅工作包')).toBeVisible()
    expect(screen.getByText('异常章 1 / 2')).toBeVisible()
    expect(screen.getByRole('button', { name: '第 2 章 · span_located' })).toBeVisible()
    expect(screen.getByText('第 1 章功能：建立前提')).toBeVisible()
    expect(screen.queryByText('beat_1')).not.toBeInTheDocument()
  })

  it('完整工作包到达后只渲染同一 B2 主体，并保留全部叙述声音与事实参与者', async () => {
    stubApi()
    renderRead()

    expect(await screen.findByText('B2 工作包事实')).toBeVisible()
    expect(screen.getByText('B2 工作包正文')).toBeVisible()
    expect(screen.getByText('B2 工作包地点')).toBeVisible()
    expect(screen.getByText('B2 工作包铰链')).toBeVisible()
    expect(screen.getByText(
      '叙事关系：待复核 · 回应：甲立即回头 ← 门外传来脚步声',
    )).toBeVisible()
    expect(screen.getByText('参与人物：B2_参与者甲、B2_参与者乙')).toBeVisible()
    expect(screen.getByText(
      '声音 1 · 第一人称 · 叙述者 B2_叙述者甲 · 聚焦者 B2_聚焦者甲 · 代词「我/咱」',
    )).toBeVisible()
    expect(screen.getByText(
      '声音 2 · third · 叙述者 B2_叙述者乙 · 聚焦者 B2_聚焦者乙 · 代词「他/她」',
    )).toBeVisible()

    expect(screen.queryByText('B1 独立响应旧事实')).not.toBeInTheDocument()
    expect(screen.queryByText('B1 独立响应旧地点')).not.toBeInTheDocument()
    expect(screen.queryByText('B1 独立响应旧铰链')).not.toBeInTheDocument()
    expect(screen.queryByText(/B1_旧叙述者/)).not.toBeInTheDocument()
    expect(screen.queryByText(/B1_事实人物/)).not.toBeInTheDocument()
  })
})

describe('mock Blueprint 审阅审批契约', () => {
  it('read 只接受当前工作包的 opaque token，global 只接受当前 review token 与非空确认人', async () => {
    await mockApi.resetBook(SID)
    const pack = workpack()
    const request = {
      review_token: pack.review_token,
    }

    await expect(mockApi.markSeen(SID, 'read', { review_token: 'bpr3_' + '0'.repeat(64) }))
      .rejects.toThrow(/当前 Blueprint 工作包/)
    await Promise.all(Object.keys(request).map(async (field) => {
      await expect(mockApi.markSeen(SID, 'read', {
        ...request,
        [field]: 'mismatch',
      })).rejects.toThrow(/当前 Blueprint 工作包/)
    }))

    const read = await mockApi.markSeen(SID, 'read', request)
    expect(read.seen).toEqual({ read: true, global: false })
    await expect(mockApi.markSeen(SID, 'global', request)).rejects.toThrow(
      /seen\.global 必须携带当前 review 的 review_token/,
    )
    await expect(mockApi.markSeen(SID, 'global', {
      review_token: MOCK_GLOBAL_SEEN_TOKEN, actor: '   ',
    })).rejects.toThrow(/非空确认人/)
    await expect(mockApi.markSeen(SID, 'global', {
      review_token: 's2gr1_' + '0'.repeat(64), actor: '人工审阅者',
    })).rejects.toThrow(/当前 review 的 review_token/)
    const global = await mockApi.markSeen(SID, 'global', {
      review_token: MOCK_GLOBAL_SEEN_TOKEN, actor: '人工审阅者',
    })
    expect(global.seen).toEqual({ read: true, global: true })
    const filled = await mockApi.backfillApprovals(SID)
    expect(filled.approvals.gate).toMatchObject({ s2: true, s4: false })
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
