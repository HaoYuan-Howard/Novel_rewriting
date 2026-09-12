import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID, plotReviewFixture } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { PlotReviewPanel } from './PlotReviewPanel'
import { emptyKnowledgeReview } from '@/test/knowledgeLinks'

afterEach(() => vi.restoreAllMocks())
const dimensions = ['剧情完整', '归因与人物立场', '因果连续', '知晓与披露', '持续状态']
async function setup() {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const submit = vi.spyOn(api, 'approveStage2Review').mockResolvedValue(doc)
  const pack = { ...plotReviewFixture().chapter, sid: SAMPLE_SID }
  pack.knowledge_links = emptyKnowledgeReview()
  return { doc, submit, pack }
}
async function fill() {
  await screen.findByRole('option', { name: '接受本范围' })
  for (const dimension of dimensions) {
    fireEvent.change(screen.getByLabelText(dimension), { target: { value: 'pass' } })
  }
  fireEvent.change(screen.getByLabelText('范围决定'), { target: { value: 'accept' } })
  fireEvent.change(screen.getByLabelText('本次批准 / 编辑理由 reason'), { target: { value: '核对本章完整原文、归因和知识边界' } })
}
it('知识时点默认保持未决，明确选择已有节点并提交同一范围和 token', async () => {
  const { pack, submit } = await setup()
  const id = 'secret_reveal:secret_01:1'
  pack.knowledge_links.scope.obligations = [{
    obligation_id: id, kind: 'secret_reveal', knowledge_ref: 'secret_01',
    subject_ref: { kind: 'secret', ref: 'secret_01' }, audience: null,
    chapter_ordinal: 1, subject_sha256: 'e'.repeat(64),
    candidate_subject_refs: [{ kind: 'beat', ref: 'b01_01' }], entry_subject_refs: [],
  }]
  pack.knowledge_links.pending_obligation_ids = [id]
  renderRoute(<PlotReviewPanel sid={SAMPLE_SID} pack={pack} />, '/n/' + SAMPLE_SID + '/read/1')
  const input = screen.getByLabelText('知识时点决定（JSON）')
  expect((input as HTMLTextAreaElement).value).toContain('unresolved')
  expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeDisabled()
  const decisions = [{ obligation_id: id, expected_subject_sha256: 'e'.repeat(64),
    resolution: { kind: 'at_beats', subject_refs: [{ kind: 'beat', ref: 'b01_01' }], trigger_beat_ref: 'b01_01' } }]
  fireEvent.change(input, { target: { value: JSON.stringify(decisions) } })
  await fill()
  fireEvent.click(screen.getByRole('button', { name: '提交范围审阅决定' }))
  await waitFor(() => expect(submit).toHaveBeenCalledWith(SAMPLE_SID, expect.objectContaining({
    node: 'stage2.blueprint', scope: 'chapter:1', review_token: pack.review_token, review: null,
    plot_review: expect.objectContaining({ decision: 'accept', new_issues: [], dispositions: [] }),
    knowledge_links: expect.objectContaining({ decisions }),
  })))
})
it('过期拒因可见，已读不替代五维判断，失败后保留重试入口', async () => {
  const { doc, pack, submit } = await setup()
  vi.mocked(api.getApprovals).mockResolvedValue({ ...doc, seen: { read: true, global: true } })
  submit.mockRejectedValue(new Error('工作包已经过期'))
  renderRoute(<PlotReviewPanel sid={SAMPLE_SID} pack={pack} />, '/n/' + SAMPLE_SID + '/read/1')
  expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeDisabled()
  await fill()
  fireEvent.click(screen.getByRole('button', { name: '提交范围审阅决定' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('工作包已经过期')
  expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeEnabled()
})
it('保存期间防止重复提交，不把空问题数组自动当作接受', async () => {
  const { pack, submit } = await setup()
  submit.mockImplementation(() => new Promise(() => {}))
  renderRoute(<PlotReviewPanel sid={SAMPLE_SID} pack={pack} />, '/n/' + SAMPLE_SID + '/read/1')
  await screen.findByRole('option', { name: '接受本范围' })
  fireEvent.change(screen.getByLabelText('范围决定'), { target: { value: 'accept' } })
  expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeDisabled()
  await fill()
  fireEvent.click(screen.getByRole('button', { name: '提交范围审阅决定' }))
  expect(await screen.findByRole('button', { name: '提交中…' })).toBeDisabled()
  expect(submit).toHaveBeenCalledOnce()
})
it('无法判定和不适用须说明，实际问题与处置原样送后端验证', async () => {
  const { pack, submit } = await setup()
  renderRoute(<PlotReviewPanel sid={SAMPLE_SID} pack={pack} />, '/n/' + SAMPLE_SID + '/read/1')
  await fill()
  fireEvent.change(screen.getByLabelText('剧情完整'), { target: { value: 'unver' } })
  fireEvent.change(screen.getByLabelText('范围决定'), { target: { value: 'uncertain' } })
  expect(screen.getByRole('button', { name: '提交范围审阅决定' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('剧情完整说明'), { target: { value: '原文叙述不足以判断' } })
  fireEvent.click(screen.getByRole('button', { name: '提交范围审阅决定' }))
  await waitFor(() => expect(submit).toHaveBeenCalledWith(SAMPLE_SID, expect.objectContaining({
    decision: 'uncertain', plot_review: expect.objectContaining({
      decision: 'unver', coverage: expect.objectContaining({
        completeness: { state: 'unver', reason: '原文叙述不足以判断', issue_refs: [] },
      }),
    }),
  })))
})
