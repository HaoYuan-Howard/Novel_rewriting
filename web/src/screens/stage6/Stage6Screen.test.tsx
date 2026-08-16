import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage6Screen } from './Stage6Screen'

describe('Stage6Screen audited approval', () => {
  it('previews and sends actor, reason, and the exact candidate SHA-256', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const candidate = fixture.candidates[0]
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row) => ({ ...row, approved: false })),
    })
    vi.spyOn(api, 'getStage1HookCopy').mockResolvedValue(
      await mockApi.getStage1HookCopy(SAMPLE_SID),
    )
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
    fireEvent.change(await screen.findByPlaceholderText('例：editor-a'), {
      target: { value: 'reviewer-a' },
    })
    fireEvent.change(screen.getByPlaceholderText('说明人工核验与选择依据'), {
      target: { value: '逐项核对人物关系与关键因果' },
    })
    fireEvent.click(screen.getByRole('button', { name: '人工核验后定稿' }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: '发起' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(SAMPLE_SID, {
      pick: candidate.index,
      actor: 'reviewer-a',
      reason: '逐项核对人物关系与关键因果',
      subject_sha256: candidate.subject_sha256,
    }))
  })
})
