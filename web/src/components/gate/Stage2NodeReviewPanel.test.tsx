import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { Stage2NodeReviewPanel } from './Stage2NodeReviewPanel'

afterEach(() => vi.restoreAllMocks())

it('展示后端复审义务，部分接受和原问题例外绑定当前范围提交', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  const submit = vi.spyOn(api, 'approveStage2Review').mockResolvedValue(doc)
  const obligation = { obligation_id: 'rule:bad', subject_sha256: 'a'.repeat(64), owner: 'stage2.global',
    chapter_ordinals: [1], subject_path: 'world_rules.wr_01', kind: 'removed_problem',
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

it('原双侧批次展示冻结请求与预检身份，不把保存请求显示为接受', async () => {
  const doc = await mockApi.getApprovals(SAMPLE_SID)
  const batch = { expected_characters_sha256: 'a'.repeat(64), expected_global_sha256: 'b'.repeat(64),
    characters: null, global: { arc_secrets: [{ secret_id: 'secret_01', revealed_from: 3 }] } }
  doc.stage2_reviews = { 'stage2.global': { state: 'rework', history: [], current_decision: {
    node: 'stage2.global', review_token: 'frozen', decision: 'rework', actor: '复核员', reason: '纠正揭示章',
    chapter_ordinals: [], owner: null, knowledge_links: null, core_edits: batch,
    core_edit_prepared_sha256: 'c'.repeat(64), review_subject_sha256: 'd'.repeat(64),
    review_contract_version: 'stage2-global-review-v3', decided_at: '2026-09-12T00:00:00Z',
  } } }
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  renderRoute(<Stage2NodeReviewPanel sid={SAMPLE_SID} node="stage2.global" token={null} />,
    `/n/${SAMPLE_SID}/global`)
  expect(await screen.findByText('已保存免费 Core 修订批次')).toBeVisible()
  expect(screen.getByText(/新产生的待审义务仍需复核/)).toBeInTheDocument()
  expect(screen.getByText('预检身份：' + 'c'.repeat(64))).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '提交节点审阅决定' })).toBeDisabled()
})
