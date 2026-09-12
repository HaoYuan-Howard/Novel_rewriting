import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { renderRoute } from '@/test/render'
import type { PlotEdit, PlotEditPreview } from '@/types/plot.generated'
import { BlueprintEditPanel } from './BlueprintEditPanel'

const template: PlotEdit = {
  actor: '待填', reason: '待填', review_token: 'bpe2_' + 'a'.repeat(64),
  chapters: [{
    chapter: { chapter_ordinal: 1, chapter_function: '施救的原因',
      beats: [{ beat_key: 'b1', content: '她因对方富有且好看而施救。',
        participants: [], evidence: [{ start_para: 1, end_para: 1 }], source_contexts: [] }],
      states: [], constraints: [], entry_state_refs: [], carry_out_refs: [],
      inherited_state_endings: [], narration: [], actor_bindings: [], relation_uses: [],
    }, identities: [{ collection: 'beats', kind: 'preserve', old_refs: ['b01_01'], new_keys: ['b1'] }],
  }],
}
const preview: PlotEditPreview = {
  review_token: template.review_token, result_subject_sha256: 'b'.repeat(64),
  changed_chapters: [1], changes: [], tombstones: [], hard_hits: [], would_apply: true,
}
afterEach(() => vi.restoreAllMocks())

it('显式读取、精确预检后写回；改动草稿立即使旧预检不能使用', async () => {
  const load = vi.spyOn(api, 'getBlueprintEditTemplate').mockResolvedValue(template)
  const check = vi.spyOn(api, 'previewBlueprintEdit').mockResolvedValue(preview)
  const save = vi.spyOn(api, 'applyBlueprintEdit').mockResolvedValue({
    id: 'job-edit', kind: 'stage2.blueprint', label: '蓝图订正', argv: '', state: 'ok',
    paid: false, started: 1, finished: 2, sid: 'book-1', ordinal: null,
  })
  renderRoute(<BlueprintEditPanel sid="book-1" ordinal={1} />)
  expect(load).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('修订本章剧情蓝图'))
  fireEvent.click(screen.getByRole('button', { name: '读取本章编辑基线' }))
  fireEvent.change(await screen.findByLabelText('蓝图编辑理由'), { target: { value: '依据首句修订' } })
  fireEvent.change(screen.getByLabelText('编辑剧情节点 1'), { target: { value: '她回忆因对方富有且好看而施救。' } })
  const submit = screen.getByRole('button', { name: '写回本次修订（不调用模型）' })
  expect(submit).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '预检本次修订' }))
  await waitFor(() => expect(submit).toBeEnabled())
  fireEvent.change(screen.getByLabelText('编辑剧情节点 1'), { target: { value: '当初她因对方富有且好看而施救。' } })
  expect(submit).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '预检本次修订' }))
  await waitFor(() => expect(submit).toBeEnabled())
  fireEvent.click(submit)
  await screen.findByText('修订已写回，请读取当前审阅材料。')
  expect(save.mock.calls[0][1]).toEqual(check.mock.calls[1][1])
  expect(save.mock.calls[0][1].review_token).toBe(template.review_token)
  expect(save).toHaveBeenCalledTimes(1)
})

it('拒绝的预检不能写回，JSON 出错不继续使用上次合法草稿', async () => {
  vi.spyOn(api, 'getBlueprintEditTemplate').mockResolvedValue(template)
  vi.spyOn(api, 'previewBlueprintEdit').mockResolvedValue({ ...preview, would_apply: false, hard_hits: ['状态依赖断开'] })
  const save = vi.spyOn(api, 'applyBlueprintEdit')
  renderRoute(<BlueprintEditPanel sid="book-1" ordinal={1} />)
  fireEvent.click(screen.getByText('修订本章剧情蓝图'))
  fireEvent.click(screen.getByRole('button', { name: '读取本章编辑基线' }))
  fireEvent.change(await screen.findByLabelText('蓝图编辑理由'), { target: { value: '依据原文修订' } })
  fireEvent.click(screen.getByRole('button', { name: '预检本次修订' }))
  await screen.findByText('状态依赖断开')
  expect(screen.getByRole('button', { name: '写回本次修订（不调用模型）' })).toBeDisabled()
  fireEvent.click(screen.getByText('高级编辑：证据、状态、约束与节点拆合'))
  fireEvent.change(screen.getByLabelText('完整章节编辑数据'), { target: { value: '{' } })
  expect(screen.getByRole('button', { name: '预检本次修订' })).toBeDisabled()
  expect(save).not.toHaveBeenCalled()
})
