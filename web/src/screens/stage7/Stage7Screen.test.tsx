import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { mockApi, PLOT_SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage7Screen } from './Stage7Screen'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('Stage7Screen download', () => {
  it('uses the API adapter so mock and real modes share one path', async () => {
    const view = await mockApi.getStage7(PLOT_SAMPLE_SID)
    const pack = await mockApi.getStage7ApprovalWorkpack(PLOT_SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue({ ...view,
      delivery: { state: 'ready', reason: '当前合成交付证明', checked_at: 1 },
    })
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...pack,
      disposition: 'already_current',
      current_approval: {
        actor: view.approved!.actor,
        reason: view.approved!.reason,
        subject_sha256: pack.decision_subject_sha256,
        workpack_sha256: pack.workpack_sha256,
        review_identity_sha256: pack.review_identity_sha256,
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

    renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    expect(await screen.findByText((text) => text.startsWith('当前定稿：' + view.approved!.actor))).toHaveTextContent(
      view.approved!.reason,
    )
    // 按钮文案带成书文件名 ⇒ 期望值取**服务端下发的那个字段**，不在测试里手拼一份。
    // 那正是这条用例要钉的产品规则：另存名不由前端编（`R-11.4`）。
    expect(view.book_filename).toMatch(/_rewriting\.txt$/)
    fireEvent.click(screen.getByRole('button', { name: '下载 ' + view.book_filename }))

    await waitFor(() => expect(download).toHaveBeenCalledWith(PLOT_SAMPLE_SID))
    expect(createUrl).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:test')
  })

  it.each(['unknown', 'stopped'] as const)('文件存在但交付为 %s 时不开放下载', async (state) => {
    const view = await mockApi.getStage7(PLOT_SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue({ ...view, book_txt_on_disk: true,
      delivery: { state, reason: '当前证据未闭合', checked_at: 1 },
    })
    renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    expect(await screen.findByText(/当前证据未闭合/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '下载 ' + view.book_filename })).not.toBeInTheDocument()
  })

  it('主区直接下载被拒后显示后端原因和恢复提示，并重取交付状态', async () => {
    const view = await mockApi.getStage7(PLOT_SAMPLE_SID)
    vi.spyOn(api, 'getStage7')
      .mockResolvedValueOnce({ ...view, delivery: {
        state: 'ready', reason: '当前合成交付证明', checked_at: 1,
      } })
      .mockResolvedValue({ ...view, delivery: {
        state: 'stopped', reason: '批准已失效，交付关闭', checked_at: 2,
      } })
    vi.spyOn(api, 'downloadStage7Book').mockRejectedValue(new ApiError('server', '/book.txt', {
      status: 409,
      backendKind: 'UpstreamStale',
      message: '成书依据在下载前发生变化',
      hint: '请核对当前人物表后重新定稿',
    }))
    renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    fireEvent.click(await screen.findByRole('button', { name: '下载 ' + view.book_filename }))

    expect(await screen.findByText('成书依据在下载前发生变化')).toBeVisible()
    expect(screen.getByText('请核对当前人物表后重新定稿')).toBeVisible()
    expect(await screen.findByText(/批准已失效，交付关闭/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '下载 ' + view.book_filename })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '下载' })).not.toBeInTheDocument()
  })

  it('交付重验和阻塞时关闭已打开抽屉的下载，并隐藏旧成书', async () => {
    const view = await mockApi.getStage7(PLOT_SAMPLE_SID)
    const ready = { ...view, delivery: {
      state: 'ready' as const, reason: '当前合成交付证明', checked_at: 1,
    } }
    const getStage7 = vi.spyOn(api, 'getStage7').mockResolvedValue(ready)
    vi.spyOn(api, 'getStage7Book').mockResolvedValue('旧成书正文')
    const download = vi.spyOn(api, 'downloadStage7Book')
    const { queryClient } = renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    fireEvent.click(await screen.findByRole('button', { name: '预览成书' }))
    expect(await screen.findByText('旧成书正文')).toBeVisible()
    expect(screen.getByRole('button', { name: '下载' })).toBeEnabled()

    let finish!: (value: Awaited<ReturnType<typeof api.getStage7>>) => void
    getStage7.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    await act(async () => {
      void queryClient.refetchQueries({ queryKey: qk.stage7(PLOT_SAMPLE_SID), exact: true })
    })
    await waitFor(() => expect(screen.getByRole('button', { name: '下载' })).toBeDisabled())
    expect(screen.queryByText('旧成书正文')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下载' }))
    expect(download).not.toHaveBeenCalled()

    await act(async () => finish({ ...ready, delivery: {
      state: 'stopped', reason: '当前批准已失效', checked_at: 2,
    } }))
    await waitFor(() => expect(screen.getAllByText(/当前批准已失效/).length).toBeGreaterThan(0))
    expect(screen.getByRole('button', { name: '下载' })).toBeDisabled()
    expect(screen.queryByText('旧成书正文')).not.toBeInTheDocument()
  })

  it('shows readonly auto-seal messaging and keeps workpack evidence without approve UI', async () => {
    const fixture = await mockApi.getStage7(PLOT_SAMPLE_SID)
    const workpack = await mockApi.getStage7ApprovalWorkpack(PLOT_SAMPLE_SID)
    const subject = 'a'.repeat(64)
    const viewOnlySubject = 'f'.repeat(64)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      approved: null,
      approval_subject_sha256: viewOnlySubject,
    })
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...workpack,
      disposition: 'review_required', current_approval: null,
      decision_subject_sha256: subject,
      residual: [{ stem: '李', where: 'chapter:1', hit: '李管家', context: '李管家躬身离去。',
        confidence: 'low', unit: '李管家', suggest: null }],
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    const character = workpack.characters[0]
    expect(await screen.findByText(
      `${character.old_canonical} → ${character.new_canonical} · 实际命中 ${character.hits}`,
    )).toBeVisible()
    expect(screen.getAllByText(/system:stage7-auto/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /定稿人物表/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/自报身份/)).not.toBeInTheDocument()
    expect(workpack.kept.length).toBeGreaterThan(0)
    const evidence = within(screen.getByText(/hash-bound 人名终审包/).closest('details')!)
    expect(evidence.getByText(workpack.kept.join('、'))).toBeVisible()
    expect(screen.getByText('李管家躬身离去。')).toBeVisible()
    expect(screen.getByText(`decision ${subject}`)).toBeVisible()
    expect(screen.queryByText(`decision ${viewOnlySubject}`)).not.toBeInTheDocument()
  })

  it('shows already_current auto seal and keeps residual adoption as the write surface', async () => {
    const fixture = await mockApi.getStage7(PLOT_SAMPLE_SID)
    const current = await mockApi.getStage7ApprovalWorkpack(PLOT_SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      approved: null,
    })
    vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue({
      ...current,
      disposition: 'already_current',
      current_approval: {
        actor: 'system:stage7-auto',
        reason: '机械校验通过后自动定稿',
        subject_sha256: current.decision_subject_sha256,
        workpack_sha256: current.workpack_sha256,
        review_identity_sha256: current.review_identity_sha256,
      },
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)

    expect(await screen.findByText('人物表已自动定稿')).toBeVisible()
    expect(screen.getAllByText(/system:stage7-auto/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /定稿人物表/ })).not.toBeInTheDocument()
    expect(current.check_basis).toBe('name_map_without_approval_or_delivery')
    const evidence = within(screen.getByText(/hash-bound 人名终审包/).closest('details')!)
    expect(evidence.getByText(/机械核验以未附审批和交付记录的人物表为基准/)).toBeVisible()
  })

  it('previews and submits explicit high-confidence residual adoption', async () => {
    const fixture = await mockApi.getStage7(PLOT_SAMPLE_SID)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      residual: [{
        stem: '温', where: 'chapter:1', hit: '温兄', context: '她向温兄道谢。',
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
      sid: PLOT_SAMPLE_SID,
      ordinal: null,
    })

    const { queryClient } = renderRoute(
      <>
        <Stage7Screen />
        <ConfirmDialog />
      </>,
      `/n/${PLOT_SAMPLE_SID}/stage7`,
    )
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(qk.approvals(PLOT_SAMPLE_SID), await mockApi.getApprovals(PLOT_SAMPLE_SID))
    expect(queryClient.getQueryState(qk.approvals(PLOT_SAMPLE_SID))?.isInvalidated).toBe(false)

    fireEvent.click(await screen.findByRole('button', { name: /采纳安全 high 候选/ }))
    await waitFor(() => expect(preview).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage7.rename',
      extra: ['--adopt-residual', 'high'],
    })))
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(adopt).toHaveBeenCalledWith(
      PLOT_SAMPLE_SID, { confidence: 'high' },
    ))
    await waitFor(() => {
      expect(queryClient.getQueryState(qk.approvals(PLOT_SAMPLE_SID))?.isInvalidated).toBe(true)
    })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: qk.finalApprovalWorkpack(PLOT_SAMPLE_SID, 'stage7'),
    })
  })

  it('does not present stale already_current during refresh or after refresh failure', async () => {
    const fixture = await mockApi.getStage7(PLOT_SAMPLE_SID)
    const pack = await mockApi.getStage7ApprovalWorkpack(PLOT_SAMPLE_SID)
    const currentPack = {
      ...pack,
      disposition: 'already_current' as const,
      current_approval: {
        actor: 'system:stage7-auto',
        reason: '机械校验通过后自动定稿',
        subject_sha256: pack.decision_subject_sha256,
        workpack_sha256: pack.workpack_sha256,
        review_identity_sha256: pack.review_identity_sha256,
      },
    }
    vi.spyOn(api, 'getStage7').mockResolvedValue(fixture)
    const getWorkpack = vi.spyOn(api, 'getStage7ApprovalWorkpack').mockResolvedValue(currentPack)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    const { queryClient } = renderRoute(<Stage7Screen />, `/n/${PLOT_SAMPLE_SID}/stage7`)
    expect(await screen.findByText('人物表已自动定稿')).toBeVisible()

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.finalApprovalWorkpack(PLOT_SAMPLE_SID, 'stage7'),
        type: 'active',
      })
    })
    await waitFor(() => {
      expect(screen.queryByText('人物表已自动定稿')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: /定稿人物表/ })).not.toBeInTheDocument()
    await act(async () => rejectRefresh(new Error('refresh failed')))
    await waitFor(() => {
      expect(screen.queryByText('人物表已自动定稿')).not.toBeInTheDocument()
      expect(screen.getByText(/终审工作包读取失败/)).toBeVisible()
    })
  })
})
