import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { SEV } from '@/constants/vocabulary'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage6Screen } from './Stage6Screen'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('Stage6Screen audited approval', () => {
  it('shows every contradiction field as an UNVER clue the human must read', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const workpack = await mockApi.getStage6ApprovalWorkpack(SAMPLE_SID)
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row, index) =>
        index === 0
          ? {
              ...row,
              approved: false,
              semantic_state: 'uncertain',
              contradictions: [
                {
                  assertion: '旧 view 独有、不可作为审批证据',
                  evidence: '独立 stage6 view',
                  reason: '不属于 hash-bound 工作包',
                },
              ],
            }
          : { ...row, approved: false },
      ),
    })
    vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue({
      ...workpack,
      candidates: workpack.candidates.map((row, index) => index === 0
        ? {
            ...row,
            semantic_review: {
              ...row.semantic_review!,
              contradictions: [{
                assertion: '人物动机与前文冲突',
                evidence: '第二章的明示选择',
                reason: '判官读到的关系与前文相反',
              }],
            },
          }
        : row),
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    const label = await screen.findByText('判官线索（UNVER）：人物动机与前文冲突')
    expect(label).toBeVisible()
    expect(screen.getByText('证据：第二章的明示选择')).toBeVisible()
    expect(screen.getByText('理由：判官读到的关系与前文相反')).toBeVisible()
    expect(screen.queryByText(/旧 view 独有/)).not.toBeInTheDocument()
    // ⚠ 这里曾有第二条用例，断言 `semantic_state === 'contradiction'` 的候选画成 hard 红并
    //   不可定稿。服务端的自动放行门下线后**产不出那个状态**，那条用例只能靠手搓一个
    //   不可能的响应活着——那是「断言一道产品明说不存在的闸」。
    //   换成这一条：矛盾线索恒是 UNVER 色，且它**不阻断**——按钮仍然可按。
    expect(label.parentElement).toHaveStyle({
      color: SEV.unver.color,
      background: SEV.unver.bg,
    })
  })

  it('previews and sends actor, reason, pick, and the exact opaque review token', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const workpack = await mockApi.getStage6ApprovalWorkpack(SAMPLE_SID)
    const candidate = workpack.candidates[0]
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row) => ({ ...row, approved: false })),
    })
    vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue(workpack)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const preview = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => ({
      argv: `python -m novel.cli ${req.kind} ${req.extra?.join(' ')}`,
      paid: false,
      label: req.label,
      kind: req.kind,
      steps: [],
    }))
    const approve = vi.spyOn(api, 'approveHook').mockResolvedValue({
      id: 'stage6-approve-test',
      kind: 'stage6.approve',
      label: '文案定稿',
      argv: 'python -m novel.cli stage6.approve',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid: SAMPLE_SID,
      ordinal: null,
    })

    renderRoute(
      <>
        <Stage6Screen />
        <ConfirmDialog />
      </>,
      `/n/${SAMPLE_SID}/stage6`,
    )
    expect(await screen.findByText(/A 原始排序：c1 → c2 → c3/)).toBeVisible()
    expect(screen.getAllByText('盲评 A · UNVER').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/mock 判官 A：需人工结合开篇正文核验/).length)
      .toBeGreaterThan(0)
    expect(screen.getAllByText(/证据 stage6\/hook\.json · subject/).length)
      .toBeGreaterThan(0)
    fireEvent.change(await screen.findByLabelText(/自报身份/), {
      target: { value: 'reviewer-a' },
    })
    fireEvent.change(screen.getByLabelText(/编辑理由/), {
      target: { value: '逐项核对人物关系与关键因果' },
    })
    fireEvent.click(screen.getByRole('button', { name: '人工核验后定稿' }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(SAMPLE_SID, {
      pick: candidate.index,
      actor: 'reviewer-a',
      reason: '逐项核对人物关系与关键因果',
      review_token: workpack.review_token,
    }))
  })

  it('shows already_current and keeps the default approval action closed', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const current = await mockApi.getStage6ApprovalWorkpack(SAMPLE_SID)
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row) => ({ ...row, approved: false })),
    })
    vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue({
      ...current,
      disposition: 'already_current',
      current_slot: 'c1',
      current_approval: {
        actor: 'reviewer-a',
        reason: '已逐项核对',
        subject_sha256: current.candidates[0].subject_sha256,
        workpack_sha256: current.workpack_sha256,
        review_identity_sha256: current.review_identity_sha256,
      },
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const approve = vi.spyOn(api, 'approveHook')

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    expect(await screen.findByText('已定稿：候选 1 · payoff_preview（人工核验）')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '回去重挑候选' }))
    const buttons = await screen.findAllByRole('button', {
      name: '当前工作包已批准 · 跳过',
    })
    expect(buttons.length).toBeGreaterThan(0)
    expect(buttons.every((button) => button.hasAttribute('disabled'))).toBe(true)
    expect(approve).not.toHaveBeenCalled()
  })

  it('keeps the approved slot visible when two candidates share one approach', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const pack = await mockApi.getStage6ApprovalWorkpack(SAMPLE_SID)
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: {
        ...fixture.approved!,
        slot: 'c3',
        approach: 'scene_punchline',
      },
      candidates: fixture.candidates.map((row, index) => ({
        ...row,
        approach: index === 0 ? 'payoff_preview' : 'scene_punchline',
        approved: index === 2,
      })),
    })
    const candidates = pack.candidates.map((row, index) => ({
      ...row,
      approach: index === 0 ? 'payoff_preview' as const : 'scene_punchline' as const,
    }))
    vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue({
      ...pack,
      candidates,
      disposition: 'already_current',
      current_slot: 'c3',
      current_approval: {
        actor: 'reviewer-a',
        reason: '核验第三条同策略候选',
        subject_sha256: candidates[2].subject_sha256,
        workpack_sha256: pack.workpack_sha256,
        review_identity_sha256: pack.review_identity_sha256,
      },
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    expect(
      await screen.findByText('已定稿：候选 3 · scene_punchline（人工核验）'),
    ).toBeVisible()
  })

  it('does not present stale already_current during refresh or after refresh failure', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const pack = await mockApi.getStage6ApprovalWorkpack(SAMPLE_SID)
    const currentPack = {
      ...pack,
      disposition: 'already_current' as const,
      current_slot: 'c1' as const,
      current_approval: {
        actor: 'reviewer-a',
        reason: '已逐项核对',
        subject_sha256: pack.candidates[0].subject_sha256,
        workpack_sha256: pack.workpack_sha256,
        review_identity_sha256: pack.review_identity_sha256,
      },
    }
    vi.spyOn(api, 'getStage6').mockResolvedValue(fixture)
    const getWorkpack = vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue(currentPack)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    const { queryClient } = renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)
    const currentLabel = '已定稿：候选 1 · payoff_preview（人工核验）'
    expect(await screen.findByText(currentLabel)).toBeVisible()

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.finalApprovalWorkpack(SAMPLE_SID, 'stage6'),
        type: 'active',
      })
    })
    await waitFor(() => {
      expect(screen.queryByText(currentLabel)).not.toBeInTheDocument()
      const buttons = screen.getAllByRole('button', { name: '终审工作包刷新中' })
      expect(buttons).toHaveLength(3)
      expect(buttons.every((candidateButton) => candidateButton.hasAttribute('disabled'))).toBe(true)
    })
    await act(async () => rejectRefresh(new Error('refresh failed')))
    await waitFor(() => {
      expect(screen.queryByText(currentLabel)).not.toBeInTheDocument()
      const buttons = screen.getAllByRole('button', { name: '终审工作包读取失败' })
      expect(buttons).toHaveLength(3)
      expect(buttons.every((candidateButton) => candidateButton.hasAttribute('disabled'))).toBe(true)
    })
  })
})
