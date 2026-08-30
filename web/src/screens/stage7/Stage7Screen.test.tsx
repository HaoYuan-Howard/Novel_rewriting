import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage7Screen } from './Stage7Screen'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('Stage7Screen download', () => {
  it('uses the API adapter so mock and real modes share one path', async () => {
    const view = await mockApi.getStage7(SAMPLE_SID)
    const pack = await mockApi.getStage7ApprovalWorkpack(SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue(view)
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...pack,
      disposition: 'already_current',
      current_approval: {
        actor: view.approved!.actor,
        reason: view.approved!.reason,
        subject_sha256: pack.decision_subject_sha256,
        workpack_sha256: pack.workpack_sha256,
      },
    })
    const download = vi
      .spyOn(api, 'downloadStage7Book')
      .mockResolvedValue(new Blob(['book'], { type: 'text/plain' }))
    const createUrl = vi.fn(() => 'blob:test')
    const revokeUrl = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeUrl })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    renderRoute(<Stage7Screen />, `/n/${SAMPLE_SID}/stage7`)
    expect(await screen.findByText(/当前审批：fixture-reviewer/)).toHaveTextContent(
      '逐项核对人物决定、别称与残留候选',
    )
    // 按钮文案带成书文件名 ⇒ 期望值取**服务端下发的那个字段**，不在测试里手拼一份。
    // 那正是这条用例要钉的产品规则：另存名不由前端编（`R-11.4`）。
    expect(view.book_filename).toMatch(/_rewriting\.txt$/)
    fireEvent.click(screen.getByRole('button', { name: '下载 ' + view.book_filename }))

    await waitFor(() => expect(download).toHaveBeenCalledWith(SAMPLE_SID))
    expect(createUrl).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:test')
  })

  it('previews and submits auditable hash-bound name-map approval', async () => {
    const fixture = await mockApi.getStage7(SAMPLE_SID)
    const workpack = await mockApi.getStage7ApprovalWorkpack(SAMPLE_SID)
    const subject = 'a'.repeat(64)
    const viewOnlySubject = 'f'.repeat(64)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      approved: null,
      approval_subject_sha256: viewOnlySubject,
    })
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...workpack,
      decision_subject_sha256: subject,
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const preview = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => ({
      argv: `python -m novel.cli ${req.kind} ${req.extra?.join(' ')}`,
      paid: false,
      label: req.label,
      kind: req.kind,
      steps: [],
    }))
    const approve = vi.spyOn(api, 'approveNames').mockResolvedValue({
      id: 'stage7-approve-test',
      kind: 'stage7.approve',
      label: '人名表定稿',
      argv: 'python -m novel.cli stage7.approve',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid: SAMPLE_SID,
      ordinal: null,
    })

    renderRoute(
      <>
        <Stage7Screen />
        <ConfirmDialog />
      </>,
      `/n/${SAMPLE_SID}/stage7`,
    )
    const character = workpack.characters[0]
    expect(await screen.findByText(
      `${character.old_canonical} → ${character.new_canonical} · 实际命中 ${character.hits}`,
    )).toBeVisible()
    expect(screen.getByText(/逐章 \+ hook 单次扫描真实替换命中/)).toBeVisible()
    expect(screen.getByText(/mock明确保留1/)).toBeVisible()
    expect(screen.getAllByText(/磕下头去/).length).toBeGreaterThan(0)
    expect(screen.getByText(`decision ${subject}`)).toBeVisible()
    expect(screen.queryByText(`decision ${viewOnlySubject}`)).not.toBeInTheDocument()
    fireEvent.change(await screen.findByLabelText(/自报身份/), {
      target: { value: 'reviewer-seven' },
    })
    fireEvent.change(screen.getByLabelText(/编辑理由/), {
      target: { value: '已逐项核对别称、亲属后缀与高置信残留' },
    })
    fireEvent.click(screen.getByRole('button', { name: /定稿人物表/ }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(SAMPLE_SID, {
      actor: 'reviewer-seven',
      reason: '已逐项核对别称、亲属后缀与高置信残留',
      subject_sha256: subject,
      workpack_sha256: workpack.workpack_sha256,
    }))
  })

  it('shows already_current and disables the default Stage7 approval action', async () => {
    const fixture = await mockApi.getStage7(SAMPLE_SID)
    const current = await mockApi.getStage7ApprovalWorkpack(SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      approved: null,
    })
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...current,
      disposition: 'already_current',
      current_approval: {
        actor: 'reviewer-seven',
        reason: '已逐项核对人物表',
        subject_sha256: current.decision_subject_sha256,
        workpack_sha256: current.workpack_sha256,
      },
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const approve = vi.spyOn(api, 'approveNames')

    renderRoute(<Stage7Screen />, `/n/${SAMPLE_SID}/stage7`)

    const button = await screen.findByRole('button', { name: /当前工作包已批准 · 跳过/ })
    expect(button).toBeDisabled()
    expect(approve).not.toHaveBeenCalled()
  })

  it('previews and submits explicit high-confidence residual adoption', async () => {
    const fixture = await mockApi.getStage7(SAMPLE_SID)
    const sample = fixture.residual[0]
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      residual: [{
        ...sample,
        confidence: 'high',
        unit: '温兄',
        suggest: '顾兄',
      }],
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const preview = vi.spyOn(api, 'previewJob').mockImplementation(async (req) => ({
      argv: `python -m novel.cli ${req.kind} ${req.extra?.join(' ')}`,
      paid: false,
      label: req.label,
      kind: req.kind,
      steps: [],
    }))
    const adopt = vi.spyOn(api, 'adoptResidual').mockResolvedValue({
      id: 'stage7-adopt-test',
      kind: 'stage7.rename',
      label: '采纳 high 漏网叫法',
      argv: 'python -m novel.cli stage7.rename --adopt-residual high',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid: SAMPLE_SID,
      ordinal: null,
    })

    const { queryClient } = renderRoute(
      <>
        <Stage7Screen />
        <ConfirmDialog />
      </>,
      `/n/${SAMPLE_SID}/stage7`,
    )
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(qk.approvals(SAMPLE_SID), await mockApi.getApprovals(SAMPLE_SID))
    expect(queryClient.getQueryState(qk.approvals(SAMPLE_SID))?.isInvalidated).toBe(false)

    fireEvent.click(await screen.findByRole('button', { name: /采纳安全 high 候选/ }))
    await waitFor(() => expect(preview).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage7.rename',
      extra: ['--adopt-residual', 'high'],
    })))
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(adopt).toHaveBeenCalledWith(
      SAMPLE_SID, { confidence: 'high' },
    ))
    await waitFor(() => {
      expect(queryClient.getQueryState(qk.approvals(SAMPLE_SID))?.isInvalidated).toBe(true)
    })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: qk.finalApprovalWorkpack(SAMPLE_SID, 'stage7'),
    })
  })

  it('does not present stale already_current during refresh or after refresh failure', async () => {
    const fixture = await mockApi.getStage7(SAMPLE_SID)
    const pack = await mockApi.getStage7ApprovalWorkpack(SAMPLE_SID)
    const currentPack = {
      ...pack,
      disposition: 'already_current' as const,
      current_approval: {
        actor: 'reviewer-seven',
        reason: '已逐项核对人物表',
        subject_sha256: pack.decision_subject_sha256,
        workpack_sha256: pack.workpack_sha256,
      },
    }
    vi.spyOn(api, 'getStage7').mockResolvedValue(fixture)
    const getWorkpack = vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue(currentPack)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    const { queryClient } = renderRoute(<Stage7Screen />, `/n/${SAMPLE_SID}/stage7`)
    expect(await screen.findByText('人物表已定稿')).toBeVisible()

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.finalApprovalWorkpack(SAMPLE_SID, 'stage7'),
        type: 'active',
      })
    })
    await waitFor(() => {
      expect(screen.queryByText('人物表已定稿')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /重新定稿/ })).toBeDisabled()
    })
    expect(screen.getByRole('button', { name: /重新定稿/ }).parentElement)
      .toHaveAttribute('title', '正在读取或刷新当前终审工作包')
    await act(async () => rejectRefresh(new Error('refresh failed')))
    await waitFor(() => {
      expect(screen.queryByText('人物表已定稿')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /重新定稿/ }).parentElement)
        .toHaveAttribute(
          'title',
          '当前终审工作包读取失败，不能绑定本轮审批',
        )
    })
    expect(screen.getByRole('button', { name: /重新定稿/ })).toBeDisabled()
  })
})
