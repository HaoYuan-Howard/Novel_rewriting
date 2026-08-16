import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { BookLayout } from './BookLayout'
import type { ApprovalDoc } from '@/types/approvals'

const pending: ApprovalDoc = {
  v: 1,
  sid: 'book-1',
  gate: { s0: false, s1: false, s2: false, s3: false, s4: false, s5: false, s6: false, s7: false },
  seen: { read: false, global: false },
  deep: [],
  writing: false,
  spawns: {},
}

describe('BookLayout approval source', () => {
  it('labels backfilled gates as disk-derived rather than user-clicked', async () => {
    vi.spyOn(api, 'getNovel').mockResolvedValue({
      sid: 'book-1',
      title: '测试书',
      dir: 'book-1',
      dirname: 'book-1',
      chapters: 11,
      stages: {},
      stage5_modes: [],
      stage_done: { stage1: true },
      stage_reviewable: {},
      chapters_done: {},
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 0,
        latest_mtime: null,
      },
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue(pending)
    const backfill = vi.spyOn(api, 'backfillApprovals').mockResolvedValue({
      approvals: { ...pending, gate: { ...pending.gate, s0: true } },
      backfilled: ['s0'],
    })
    renderRoute(<BookLayout />)

    expect(await screen.findByText('book-1 · 11 章')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '按盘面补录验收链' }))
    await waitFor(() => expect(backfill).toHaveBeenCalledWith('book-1'))
    expect(await screen.findByText(/这些闸门来自盘面核对，不是你逐屏点击的记录/)).toBeVisible()
  })
})
