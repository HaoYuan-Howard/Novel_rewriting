import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { renderRoute } from '@/test/render'
import type { AnalyzeDraftView } from '@/types/analyzeDraft'
import type { Job } from '@/types/jobs'
import { AnalyzeDraftPanel } from './AnalyzeDraftPanel'

const view: AnalyzeDraftView = {
  sid: 'book-1', owner: 'stage2.characters', source_state: 'draft_invalid',
  template: { owner: 'stage2.characters', source_kind: 'analyze_owner_draft',
    source_receipt_sha256: 'a'.repeat(64), source_draft_sha256: 'b'.repeat(64),
    source_mapping_sha256: 'c'.repeat(64), character_bindings: {},
    characters: { characters: [{ character_ref: 'c1', role_evidence_refs: ['e999999'] }] }, global_draft: null },
  diagnostics: ['e999999 不在原回答的候选闭集中'],
  evidence_candidates: [{ evidence_candidate_id: 'e000001', chapter_ordinal: 1, excerpt: '阿青是茶馆掌柜。' }],
}
afterEach(() => vi.restoreAllMocks())

it('保留原候选与来源身份，人工订正只提交对应 owner 并读取实际作业结果', async () => {
  const read = vi.spyOn(api, 'getAnalyzeDraft').mockResolvedValue(view)
  const write = vi.spyOn(api, 'editAnalyzeDraft').mockResolvedValue({ state: 'landed_red' } as Job)
  const user = userEvent.setup()
  renderRoute(<AnalyzeDraftPanel sid="book-1" />)
  expect(read).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '已购联合草稿与免费修复' }))
  expect(await screen.findByText(view.diagnostics[0]!)).toBeInTheDocument()
  expect(screen.getByText(/候选尚未获统一审阅接受/)).toBeVisible()
  await user.click(screen.getByText('原文证据候选（1 段）'))
  expect(screen.getByText(/阿青是茶馆掌柜/)).toBeVisible()
  await user.click(screen.getByRole('button', { name: '载入原草稿订正模板' }))
  await user.type(screen.getByLabelText('草稿订正人'), '人工复核员')
  await user.type(screen.getByLabelText('草稿订正依据'), '依据原文候选订正')
  await user.click(screen.getByRole('button', { name: '提交草稿订正（0 模型）' }))
  expect(write).toHaveBeenCalledWith('book-1', { actor: '人工复核员', reason: '依据原文候选订正', edit: view.template })
  expect(await screen.findByText(/草稿订正作业：landed_red/)).toBeVisible()
})

it('prepared 只显示冻结修订与补尾入口，不允许重写已保存请求', async () => {
  vi.spyOn(api, 'getAnalyzeDraft').mockResolvedValue({ ...view, source_state: 'prepared' })
  const write = vi.spyOn(api, 'editAnalyzeDraft')
  const user = userEvent.setup()
  renderRoute(<AnalyzeDraftPanel sid="book-1" />)
  await user.click(screen.getByRole('button', { name: '已购联合草稿与免费修复' }))
  expect(await screen.findByText(/已保存冻结的修订/)).toBeVisible()
  expect(screen.queryByLabelText('草稿订正 JSON')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '提交草稿订正（0 模型）' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /免费补完草稿提交/ })).toBeInTheDocument()
  expect(write).not.toHaveBeenCalled()
})
