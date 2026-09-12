import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { renderRoute } from '@/test/render'
import { BlueprintRefinementPanel } from './BlueprintRefinementPanel'

afterEach(() => vi.restoreAllMocks())
beforeEach(async () => {
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
})

it('人工明确描述疑点后提交当前来源 token，只刷新书级接受状态', async () => {
  const doc = await mockApi.getApprovals('book-1')
  const load = vi.spyOn(api, 'getBlueprintRefinementCatalog').mockResolvedValue({
    origin: 'design', review_token: 'design-current', subjects_by_chapter: { 1: ['chapter'] },
  })
  const save = vi.spyOn(api, 'submitBlueprintRefinement').mockResolvedValue({
    ...doc, gate: { ...doc.gate, s2: false },
  })
  renderRoute(<BlueprintRefinementPanel sid="book-1" ordinal={1} />)
  expect(load).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('蓝图疑点核查 · 第 1 章'))
  fireEvent.click(screen.getByRole('button', { name: '读取当前核查入口' }))
  const button = await screen.findByRole('button', { name: '提交疑点（不调用模型）' })
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByLabelText('疑点维度'), { target: { value: 'attribution' } })
  fireEvent.change(screen.getByLabelText('蓝图疑点'), { target: { value: '原文是人物自称，不应写成客观亲子关系。' } })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  await screen.findByRole('status')
  expect(save).toHaveBeenCalledWith('book-1', {
    origin: 'design', review_token: 'design-current', actor: 'human-ui',
    chapter_ordinal: 1, dimension: 'attribution', subject_refs: ['chapter'],
    reason: '原文是人物自称，不应写成客观亲子关系。',
  })
  expect(screen.getByRole('link', { name: '本章蓝图审阅' })).toHaveAttribute('href', '/n/book-1/read/1')
})

it('切换审阅阶段不沿用旧 token，当前章缺席或入口读取失败不能提交', async () => {
  const load = vi.spyOn(api, 'getBlueprintRefinementCatalog')
    .mockResolvedValueOnce({ origin: 'design', review_token: 'old', subjects_by_chapter: { 1: ['chapter'] } })
    .mockRejectedValueOnce(new Error('当前结构入口不可用'))
  const save = vi.spyOn(api, 'submitBlueprintRefinement')
  renderRoute(<BlueprintRefinementPanel sid="book-1" ordinal={1} />)
  fireEvent.click(screen.getByText('蓝图疑点核查 · 第 1 章'))
  fireEvent.click(screen.getByRole('button', { name: '读取当前核查入口' }))
  await screen.findByLabelText('蓝图疑点')
  fireEvent.change(screen.getByLabelText('疑点审阅阶段'), { target: { value: 'structure' } })
  await screen.findByRole('alert')
  expect(load).toHaveBeenLastCalledWith('book-1', 'structure')
  expect(screen.queryByRole('button', { name: '提交疑点（不调用模型）' })).not.toBeInTheDocument()
  expect(save).not.toHaveBeenCalled()
})
