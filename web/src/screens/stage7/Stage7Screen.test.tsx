import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage7Screen } from './Stage7Screen'

describe('Stage7Screen download', () => {
  it('uses the API adapter so mock and real modes share one path', async () => {
    vi.spyOn(api, 'getStage7').mockResolvedValue(await mockApi.getStage7(SAMPLE_SID))
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
    fireEvent.click(screen.getByRole('button', { name: '下载 book.txt' }))

    await waitFor(() => expect(download).toHaveBeenCalledWith(SAMPLE_SID))
    expect(createUrl).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:test')
  })

  it('previews and submits auditable hash-bound name-map approval', async () => {
    const fixture = await mockApi.getStage7(SAMPLE_SID)
    const subject = 'a'.repeat(64)
    vi.spyOn(api, 'getStage7').mockResolvedValue({
      ...fixture,
      approved: null,
      approval_subject_sha256: subject,
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
    fireEvent.change(await screen.findByPlaceholderText('例：editor-a'), {
      target: { value: 'reviewer-seven' },
    })
    fireEvent.change(screen.getByPlaceholderText('说明逐项核对人物表与残留候选的依据'), {
      target: { value: '已逐项核对别称、亲属后缀与高置信残留' },
    })
    fireEvent.click(screen.getByRole('button', { name: /定稿人物表/ }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(SAMPLE_SID, {
      actor: 'reviewer-seven',
      reason: '已逐项核对别称、亲属后缀与高置信残留',
      subject_sha256: subject,
    }))
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
  })
})
