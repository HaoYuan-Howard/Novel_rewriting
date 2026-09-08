import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { Stage2NodeReviewPanel } from './Stage2NodeReviewPanel'

afterEach(() => vi.restoreAllMocks())

it('节点决定原样提交当前 token，已读不代替接受，过期拒因可见', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  const enums = await mockApi.getEnums()
  vi.spyOn(api, 'getApprovals').mockResolvedValue({ ...doc, seen: { read: true, global: true } })
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  const submit = vi.spyOn(api, 'approveStage2Review').mockRejectedValue(new Error('工作包已经过期'))
  renderRoute(<Stage2NodeReviewPanel sid={SAMPLE_SID} node="stage2.blueprint" token="bpr4_current" />,
    `/n/${SAMPLE_SID}/read/1`)
  const button = await screen.findByRole('button', { name: '提交节点审阅决定' })
  expect(button).toBeDisabled()
  await screen.findByRole('option', { name: '接受' })
  fireEvent.change(screen.getByLabelText('stage2.blueprint 审阅决定'), { target: { value: 'accept' } })
  fireEvent.change(screen.getByLabelText('自报身份 actor'), { target: { value: 'reviewer-b' } })
  fireEvent.change(screen.getByLabelText('本次批准 / 编辑理由 reason'), { target: { value: '逐章核对完整正文和事实' } })
  fireEvent.click(button)
  await waitFor(() => expect(submit).toHaveBeenCalledWith(SAMPLE_SID, {
    node: 'stage2.blueprint', review_token: 'bpr4_current', actor: 'reviewer-b',
    reason: '逐章核对完整正文和事实', decision: 'accept', owner: null, chapter_ordinals: [],
  }))
  expect(await screen.findByRole('alert')).toHaveTextContent('工作包已经过期')
})
