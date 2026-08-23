import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { JOB } from '@/constants/vocabulary'
import type { CheckReport } from '@/types/stages'
import { act, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { RunScreen } from './RunScreen'

describe('RunScreen check composition', () => {
  it('renders exit 1 as landed_red instead of a failed command', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    run.steps[0] = { ...run.steps[0], beacon: 'landed_red', last_exit: 1 }
    run.events_tail = [{
      ts: 1,
      sid: SAMPLE_SID,
      node: run.steps[0].node,
      phase: 'end',
      exit_code: 1,
      note: null,
    }]
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    vi.spyOn(api, 'getCheck').mockResolvedValue(null)

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    expect(await screen.findByText('landed_red 已落盘但有红项')).toBeInTheDocument()
    expect(screen.queryByText('failed 失败')).not.toBeInTheDocument()
    expect(screen.getAllByText('exit 1').some(
      (row) => row.style.color === JOB.landed_red.color,
    )).toBe(true)
  })

  it('shows authoritative badges and counts while unavailable checks stay unknown', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const base = await mockApi.getCheck(SAMPLE_SID, 'stage5')
    if (!base || base.rules.length < 2) throw new Error('stage5 mock report is incomplete')

    const report: CheckReport = {
      ...base,
      badge: 'fail',
      passed: false,
      rules: [
        { ...base.rules[0], sev: 'hard', status: 'hit' },
        { ...base.rules[1], sev: 'info', status: 'hit' },
      ],
      counts: { ok: 0, hit: 2, unver: 2, skipped: 1 },
    }
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    vi.spyOn(api, 'getCheck').mockImplementation(async (_sid, kind) =>
      kind === 'stage1' ? null : report,
    )

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    expect(await screen.findAllByText('有阻断')).toHaveLength(7)
    expect(screen.getAllByText('hard 1 · info 1 · unver 3')).toHaveLength(7)
    expect(screen.getByText('? 校验未知')).toBeInTheDocument()
    expect(screen.getByText(/badge 与计数来自各层 CheckReport/)).toBeInTheDocument()
  })

  it('keeps the previous report while the current check refreshes; failure stays unknown', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const base = await mockApi.getCheck(SAMPLE_SID, 'stage5')
    if (!base) throw new Error('stage5 mock report is missing')
    const report: CheckReport = { ...base, badge: 'pass', passed: true }
    let stage1Calls = 0
    let rejectRefresh!: (reason?: unknown) => void
    const refresh = new Promise<CheckReport | null>((_resolve, reject) => {
      rejectRefresh = reject
    })

    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    vi.spyOn(api, 'getCheck').mockImplementation(async (_sid, kind) => {
      if (kind !== 'stage1') return report
      stage1Calls += 1
      return stage1Calls === 1 ? report : refresh
    })

    const { queryClient } = renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)
    expect(await screen.findAllByText('通过')).toHaveLength(8)

    act(() => {
      void queryClient.invalidateQueries({ queryKey: qk.check(SAMPLE_SID, 'stage1') })
    })
    // refetch 期间 placeholderData 沿用上一份——不闪成「校验未知」（U-00：未知 ≠ 没问题）
    await waitFor(() => {
      expect(queryClient.getQueryState(qk.check(SAMPLE_SID, 'stage1'))?.fetchStatus).toBe(
        'fetching',
      )
    })
    expect(screen.queryByText('? 校验未知')).not.toBeInTheDocument()
    expect(screen.getAllByText('通过')).toHaveLength(8)

    // 这次是真的取不到了——才显示未知
    act(() => rejectRefresh(new Error('current check unavailable')))
    await waitFor(() => {
      expect(screen.getByText('? 校验未知')).toBeInTheDocument()
      expect(screen.getAllByText('通过')).toHaveLength(7)
    })
  })
})
