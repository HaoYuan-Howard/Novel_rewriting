import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { CHECK_KINDS } from '@/types/stages'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InboxScreen } from './InboxScreen'

beforeEach(() => {
  vi.spyOn(api, 'getNovel').mockImplementation(async (sid) => {
    const novel = await mockApi.getNovel(sid)
    return {
      ...novel,
      stage_reviewable: Object.fromEntries(CHECK_KINDS.map((kind) => [kind, true])),
    }
  })
})

describe('InboxScreen verification copy', () => {
  it('不为尚无可审产物的后续阶段发起校验请求', async () => {
    const inbox = await mockApi.getInbox(SAMPLE_SID)
    const novel = await mockApi.getNovel(SAMPLE_SID)
    vi.mocked(api.getNovel).mockResolvedValue({
      ...novel,
      stage_reviewable: Object.fromEntries(
        CHECK_KINDS.map((kind) => [kind, kind === 'stage0']),
      ),
    })
    vi.spyOn(api, 'getInbox').mockResolvedValue(inbox)
    vi.spyOn(api, 'getSampleCoverage').mockResolvedValue(null)
    const getCheck = vi.spyOn(api, 'getCheck').mockResolvedValue(null)

    renderRoute(<InboxScreen />, `/n/${SAMPLE_SID}/inbox`)

    await waitFor(() => expect(getCheck).toHaveBeenCalledTimes(1))
    expect(getCheck).toHaveBeenCalledWith(SAMPLE_SID, 'stage0')
    expect(getCheck).not.toHaveBeenCalledWith(SAMPLE_SID, 'stage5')
    expect(screen.getAllByText('尚不可检查')).toHaveLength(6)
  })

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
