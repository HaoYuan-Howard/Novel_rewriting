import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { SEV } from '@/constants/vocabulary'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage6Screen } from './Stage6Screen'

describe('Stage6Screen audited approval', () => {
  it('shows every contradiction field as an UNVER clue the human must read', async () => {
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
                  reason: '判官读到的关系与前文相反',
                },
              ],
            }
          : { ...row, approved: false },
      ),
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    const label = await screen.findByText('判官线索（UNVER）：人物动机与前文冲突')
    expect(label).toBeVisible()
    expect(screen.getByText('证据：第二章的明示选择')).toBeVisible()
    expect(screen.getByText('理由：判官读到的关系与前文相反')).toBeVisible()
    // ⚠ 这里曾有第二条用例，断言 `semantic_state === 'contradiction'` 的候选画成 hard 红并
    //   不可定稿。服务端的自动放行门下线后**产不出那个状态**，那条用例只能靠手搓一个
    //   不可能的响应活着——那是「断言一道产品明说不存在的闸」。
    //   换成这一条：矛盾线索恒是 UNVER 色，且它**不阻断**——按钮仍然可按。
    expect(label.parentElement).toHaveStyle({
      color: SEV.unver.color,
      background: SEV.unver.bg,
    })
  })

  it('previews and sends actor, reason, and the exact candidate SHA-256', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
    const candidate = fixture.candidates[0]
    vi.spyOn(api, 'getStage6').mockResolvedValue({
      ...fixture,
      approved: null,
      candidates: fixture.candidates.map((row) => ({ ...row, approved: false })),
    })
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

  it('keeps the approved slot visible when two candidates share one approach', async () => {
    const fixture = await mockApi.getStage6(SAMPLE_SID)
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
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })

    renderRoute(<Stage6Screen />, `/n/${SAMPLE_SID}/stage6`)

    expect(
      await screen.findByText('已定稿：候选 3 · scene_punchline（人工核验）'),
    ).toBeVisible()
  })
})
