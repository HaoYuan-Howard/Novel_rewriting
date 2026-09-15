import { api } from '@/api/endpoints'
import { mockApi, PLOT_SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage6Screen } from './Stage6Screen'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

async function pendingWorkpack() {
  const pack = await mockApi.getStage6ApprovalWorkpack(PLOT_SAMPLE_SID)
  // 终态样本保留真实批准；仅此交互反例显式模拟当前尚未接受。
  return { ...pack, disposition: 'review_required' as const,
    current_slot: null, current_approval: null }
}

describe('Stage6Screen audited approval', () => {
  it('shows hook-only evidence and the exact rewritten body from the approval workpack', async () => {
    const fixture = await mockApi.getStage6(PLOT_SAMPLE_SID)
    const workpack = await pendingWorkpack()
    workpack.materials.source_hook = '新婚夜收到来信。'
    workpack.materials.source_blurb.facts = [{
      kind: 'hook_supported', claim_id: 'hook_001', claim: '新婚夜收到来信',
      hook_evidence: { source: { text: '收到来信', start: 3, end: 7 }, unresolved_locator: null },
    }]
    workpack.materials.rewritten_chapters = [{ chapter_ordinal: 1, text: '天亮后，她走出了院门。' }]
    vi.spyOn(api, 'getStage6').mockResolvedValue(fixture)
    vi.spyOn(api, 'getStage6ApprovalWorkpack').mockResolvedValue(workpack)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    renderRoute(<Stage6Screen />, `/n/${PLOT_SAMPLE_SID}/stage6`)
    fireEvent.click(await screen.findByText('核验材料 · 1 章正文 · 1 条钩子主张'))
    expect(screen.getByText('hook_001 · 钩子独立支持')).toBeVisible()
    expect(screen.getByText('收到来信')).toBeVisible()
    fireEvent.click(screen.getByText('第 1 章正文'))
    expect(screen.getByText('天亮后，她走出了院门。')).toBeVisible()
  })

  // ⚠ 「判官矛盾线索」用例已随 stage6.semantic 判官退役删除（2026-09-15）：
  //   服务端不再产任何判官证据，界面不再有该渲染分支。

  it('previews and sends actor, reason, pick, and the exact opaque review token', async () => {
    const fixture = await mockApi.getStage6(PLOT_SAMPLE_SID)
    const workpack = await pendingWorkpack()
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
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
      sid: PLOT_SAMPLE_SID,
      ordinal: null,
    })

    renderRoute(
      <>
        <Stage6Screen />
        <ConfirmDialog />
      </>,
      `/n/${PLOT_SAMPLE_SID}/stage6`,
    )
    // workpack 渲染屏障（原判官排序断言删除后补）：等终审包块出现再查机械核验区。
    expect(await screen.findByText('hash-bound 终审包 · review_required')).toBeVisible()
    const quoteRows = workpack.check.metrics.find((row) => row.rule === 'hook_quote_shape')!.findings
    expect(quoteRows.filter((row) => row.evidence_key === '0/0')).toHaveLength(3)
    const checkEvidence = within(screen.getByRole('region', { name: '文案机械核验材料' }))
    for (const row of quoteRows) expect(checkEvidence.getByText(row.detail)).toBeVisible()
    expect(errors.mock.calls.some((args) => args.some((value) => String(value).includes('same key')))).toBe(false)
    fireEvent.change(await screen.findByLabelText(/自报身份/), {
      target: { value: 'reviewer-a' },
    })
    fireEvent.change(screen.getByLabelText(/编辑理由/), {
      target: { value: '逐项核对人物关系与关键因果' },
    })
    fireEvent.click(screen.getByRole('button', { name: '人工核验后定稿' }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(PLOT_SAMPLE_SID, {
      pick: candidate.index,
      actor: 'reviewer-a',
      reason: '逐项核对人物关系与关键因果',
      review_token: workpack.review_token,
    }))
  })

  it('shows already_current and keeps the default approval action closed', async () => {
    const fixture = await mockApi.getStage6(PLOT_SAMPLE_SID)
    const current = await mockApi.getStage6ApprovalWorkpack(PLOT_SAMPLE_SID)
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

    renderRoute(<Stage6Screen />, `/n/${PLOT_SAMPLE_SID}/stage6`)

    expect(await screen.findByText(`已定稿：候选 1 · ${current.candidates[0].approach}（人工选择）`)).toBeVisible()
    expect(current.check_basis).toBe('candidate_without_approval')
    expect(screen.getByText(/机械核验以未附审批的候选为基准/)).toBeVisible()
    expect(screen.getByText(/A\/B 审判只看开篇前.*最终定稿工作包提供完整重写正文/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '回去重挑候选' }))
    const checks = within(screen.getByRole('region', { name: '文案机械核验材料' }))
    const unapproved = current.check.attention.find((row) => row.rule === 'hook_not_approved')!
    expect(unapproved.why).toBeTruthy()
    expect(checks.getByText(unapproved.why!)).toBeVisible()
    const buttons = await screen.findAllByRole('button', {
      name: '当前工作包已批准 · 跳过',
    })
    expect(buttons.length).toBeGreaterThan(0)
    expect(buttons.every((button) => button.hasAttribute('disabled'))).toBe(true)
    expect(approve).not.toHaveBeenCalled()
  })

  it('keeps the approved slot visible when two candidates share one approach', async () => {
    const fixture = await mockApi.getStage6(PLOT_SAMPLE_SID)
    const pack = await mockApi.getStage6ApprovalWorkpack(PLOT_SAMPLE_SID)
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

    renderRoute(<Stage6Screen />, `/n/${PLOT_SAMPLE_SID}/stage6`)

    expect(
      await screen.findByText('已定稿：候选 3 · scene_punchline（人工选择）'),
    ).toBeVisible()
  })

  it('does not present stale already_current during refresh or after refresh failure', async () => {
    const fixture = await mockApi.getStage6(PLOT_SAMPLE_SID)
    const pack = await mockApi.getStage6ApprovalWorkpack(PLOT_SAMPLE_SID)
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

    const { queryClient } = renderRoute(<Stage6Screen />, `/n/${PLOT_SAMPLE_SID}/stage6`)
    const currentLabel = `已定稿：候选 1 · ${pack.candidates[0].approach}（人工选择）`
    expect(await screen.findByText(currentLabel)).toBeVisible()

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.finalApprovalWorkpack(PLOT_SAMPLE_SID, 'stage6'),
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
