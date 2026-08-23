import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InboxScreen } from './InboxScreen'

describe('InboxScreen verification copy', () => {
  it('routes verification work across Stage2, Stage5, and Stage6', async () => {
    const base = await mockApi.getInbox(SAMPLE_SID)
    vi.spyOn(api, 'getInbox').mockResolvedValue({
      ...base,
      items: [
        ...base.items,
        {
          kind: 'verification',
          stage: 'stage6',
          chapter: null,
          rule: 'hook_semantic_review',
          what: '语义复核尚未闭合',
          how: '回到文案屏补决定与证据',
          attention: 'pending',
          delivery_effect: 'disclosure',
          decision: null,
        },
      ],
      counts: { ...base.counts, verification: 1 },
      total: base.total + 1,
      attention_counts: {
        ...base.attention_counts,
        pending: base.attention_counts.pending + 1,
      },
      delivery_counts: {
        ...base.delivery_counts,
        disclosure: base.delivery_counts.disclosure + 1,
      },
    })
    vi.spyOn(api, 'getCheck').mockImplementation((_sid, kind) =>
      mockApi.getCheck(SAMPLE_SID, kind),
    )
    vi.spyOn(api, 'getSampleCoverage').mockResolvedValue({
      sid: SAMPLE_SID,
      ordinals: [3],
    })

    renderRoute(<InboxScreen />, `/n/${SAMPLE_SID}/inbox`)

    expect(await screen.findByText('人工复核未闭合')).toBeInTheDocument()
    expect(screen.getByText(/Stage2 \/ Stage5 \/ Stage6/)).toHaveTextContent(
      '回到所属阶段补决定与证据',
    )
  })
})
