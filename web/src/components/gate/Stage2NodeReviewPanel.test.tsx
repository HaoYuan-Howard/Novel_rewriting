import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { Stage2NodeReviewPanel } from './Stage2NodeReviewPanel'
import { emptyKnowledgeReview } from '@/test/knowledgeLinks'

afterEach(() => vi.restoreAllMocks())

it('知识关联默认保持未决，只提交用户选择且绑定当前主体的事实', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const submit = vi.spyOn(api, 'approveStage2Review').mockResolvedValue(doc)
  const knowledge = emptyKnowledgeReview()
  const id = 'secret_reveal:secret_01:1'
  knowledge.scope.obligations = [{ obligation_id: id, kind: 'secret_reveal', knowledge_ref: 'secret_01',
    chapter_ordinal: 1, subject_sha256: 'e'.repeat(64), claim: '信由母亲写下', evidence: [],
    candidate_fact_refs: ['ch01_f001'], entry_fact_refs: [] }]
  knowledge.pending_obligation_ids = [id]
  renderRoute(<Stage2NodeReviewPanel sid={SAMPLE_SID} node="stage2.blueprint" token="current" knowledge={knowledge} />,
    `/n/${SAMPLE_SID}/read/1`)
  await screen.findByRole('option', { name: '接受' })
  const input = screen.getByLabelText('知识关联决定（JSON）')
  expect((input as HTMLTextAreaElement).value).toContain('unresolved')
  const decisions = [{ obligation_id: id, expected_subject_sha256: 'e'.repeat(64),
    resolution: { kind: 'at_facts', fact_refs: ['ch01_f001'], trigger_fact_ref: 'ch01_f001' } }]
  fireEvent.change(input, { target: { value: JSON.stringify(decisions) } })
  fireEvent.change(screen.getByLabelText('stage2.blueprint 审阅决定'), { target: { value: 'accept' } })
  fireEvent.change(screen.getByLabelText('自报身份 actor'), { target: { value: 'reviewer' } })
  fireEvent.change(screen.getByLabelText('本次批准 / 编辑理由 reason'), { target: { value: '核对信的揭示时点' } })
  fireEvent.click(screen.getByRole('button', { name: '提交节点审阅决定' }))
  await waitFor(() => expect(submit).toHaveBeenCalledWith(SAMPLE_SID, expect.objectContaining({
    knowledge_links: { scope_sha256: knowledge.scope.scope_sha256, actor: 'reviewer',
      reason: '核对信的揭示时点', decisions },
  })))
})

it('节点决定原样提交当前 token，已读不代替接受，过期拒因可见', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  const enums = await mockApi.getEnums()
  vi.spyOn(api, 'getApprovals').mockResolvedValue({ ...doc, seen: { read: true, global: true } })
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  const submit = vi.spyOn(api, 'approveStage2Review').mockRejectedValue(new Error('工作包已经过期'))
  const knowledge = emptyKnowledgeReview()
  renderRoute(<Stage2NodeReviewPanel sid={SAMPLE_SID} node="stage2.blueprint" token="bpr4_current" knowledge={knowledge} />,
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
    review: null,
    knowledge_links: { scope_sha256: knowledge.scope.scope_sha256, actor: 'reviewer-b',
      reason: '逐章核对完整正文和事实', decisions: [] },
  }))
  expect(await screen.findByRole('alert')).toHaveTextContent('工作包已经过期')
})

it('展示后端复审义务，部分接受和原问题例外绑定当前范围提交', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const submit = vi.spyOn(api, 'approveStage2Review').mockResolvedValue(doc)
  const obligation = { obligation_id: 'rule:bad', subject_sha256: 'a'.repeat(64), owner: 'stage2.global',
    chapter_ordinals: [1], subject_path: 'canon.world_rules.rule_bad', kind: 'removed_problem',
    context_refs: ['world_rule_set_complete'], problem_subject_sha256: 'b'.repeat(64) }
  const coverage = { scope: { contract: 'stage2-review-obligations-v1' as const, node: 'stage2.global',
    scope_sha256: 'c'.repeat(64), obligations: [obligation] }, inherited: [],
    pending_obligation_ids: ['rule:bad'], prior_problems: [{ obligation, decision: 'rework',
      actor: 'original-reviewer', reason: '原文没有这条事实', source_decision_sha256: null }] }
  renderRoute(<Stage2NodeReviewPanel sid={SAMPLE_SID} node="stage2.global" token="current" coverage={coverage} />,
    `/n/${SAMPLE_SID}/global`)
  expect(await screen.findByText('审阅义务：待审 1 项，继承 0 项')).toBeInTheDocument()
  expect(screen.getByText(/原文没有这条事实/)).toBeInTheDocument()
  await screen.findByRole('option', { name: '定向返工' })
  fireEvent.change(screen.getByLabelText('stage2.global 审阅决定'), { target: { value: 'rework' } })
  fireEvent.change(screen.getByLabelText('返工属主'), { target: { value: 'stage2.global' } })
  fireEvent.change(screen.getByLabelText('自报身份 actor'), { target: { value: 'reviewer' } })
  fireEvent.change(screen.getByLabelText('本次批准 / 编辑理由 reason'), { target: { value: '删除后仍缺必要知识' } })
  fireEvent.change(screen.getByLabelText('其余待审项决定'), { target: { value: 'accept' } })
  fireEvent.click(screen.getByRole('button', { name: '提交节点审阅决定' }))
  await waitFor(() => expect(submit).toHaveBeenCalledWith(SAMPLE_SID, expect.objectContaining({
    decision: 'rework', owner: 'stage2.global', review: {
      scope_sha256: coverage.scope.scope_sha256, default_decision: 'accept',
      exceptions: [{ obligation_id: 'rule:bad', decision: 'rework', reason: '删除后仍缺必要知识' }],
    },
  })))
})
