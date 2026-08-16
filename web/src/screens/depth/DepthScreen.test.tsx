import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_ORDINAL, SAMPLE_SID } from '@/api/mock'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { DepthScreen } from './DepthScreen'

describe('DepthScreen chapter goals', () => {
  it('shows the server-provided goal on every depth-selection row', async () => {
    const stage4 = await mockApi.getStage4(SAMPLE_SID, SAMPLE_ORDINAL)
    vi.spyOn(api, 'getSampleCoverage').mockResolvedValue(null)
    vi.spyOn(api, 'getStage4').mockResolvedValue(stage4)
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      v: 1,
      sid: SAMPLE_SID,
      gate: { s0: true, s1: true, s2: true, s3: true, s4: true, s5: false, s6: false, s7: false },
      seen: { read: true, global: true },
      deep: [1, 2, 3],
      writing: false,
      spawns: {},
    })
    renderRoute(<DepthScreen />, `/n/${SAMPLE_SID}/depth`)

    for (const row of stage4.index) {
      expect(await screen.findByText(row.chapter_goal)).toBeVisible()
    }
  })
})
