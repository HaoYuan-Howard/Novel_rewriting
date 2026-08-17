import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { SEV } from '@/constants/vocabulary'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage6Screen } from './Stage6Screen'

describe('Stage6Screen audited approval', () => {
  it('shows every advisory contradiction field as an uncalibrated UNVER clue', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
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
                  assertion: '人物动机与前文冲突',
                  evidence: '第二章的明示选择',
                  reason: '当前 evaluator 未校准，只能交由人审',
                },
              ],
            }
          : { ...row, approved: false },
      ),
    })
    vi.spyOn(api, 'getStage1HookCopy').mockResolvedValue(
      await mockApi.getStage1HookCopy(SAMPLE_SID),
    )
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    expect(await screen.findByText('未校准线索（UNVER）：人物动机与前文冲突')).toBeVisible()
    expect(screen.getByText('证据：第二章的明示选择')).toBeVisible()
    expect(screen.getByText('理由：当前 evaluator 未校准，只能交由人审')).toBeVisible()
  })

  it('shows calibrated contradictions as blocking failures instead of UNVER clues', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row, index) =>
        index === 0
          ? {
              ...row,
              approved: false,
              semantic_state: 'contradiction',
              semantic_reason: '校准 evaluator 判定存在实质矛盾',
              selectable: false,
              human_resolvable: false,
              contradictions: [
                {
                  assertion: '人物动机与前文冲突',
                  evidence: '第二章的明示选择',
                  reason: '校准双盲复核为 FAIL',
                },
              ],
            }
          : { ...row, approved: false },
      ),
    })
    vi.spyOn(api, 'getStage1HookCopy').mockResolvedValue(
      await mockApi.getStage1HookCopy(SAMPLE_SID),
    )
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    const label = await screen.findByText('校准双盲 FAIL（实质矛盾）：人物动机与前文冲突')
    expect(label).toBeVisible()
    expect(label.parentElement).toHaveStyle({
      color: SEV.hard.color,
      background: SEV.hard.bg,
    })
    expect(screen.queryByText('未校准线索（UNVER）：人物动机与前文冲突')).not.toBeInTheDocument()
  })

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
      subject_sha256: candidate.subject_sha256,
    }))
  })
})
