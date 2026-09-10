import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { renderRoute } from '@/test/render'
import type { CharacterRegistryView } from '@/types/characters'
import type { Job } from '@/types/jobs'
import { CharactersPanel } from './CharactersPanel'

const character = {
  canonical: '沈青', name_distinctiveness: 'high', role: '茶馆掌柜',
  role_evidence: { chapter_ordinal: 1, locator: '沈青守着茶馆' },
  address: [{ surface: '沈青', by: 'narration', context: 'reference', from_chapter: 1 }],
}
const view = {
  sid: 'book-1', source_sha256: 'a'.repeat(64),
  artifact: { invalid: false, freshness: 'fresh' },
  registry: { characters: { char_001: character } },
  edit_template: { source_sha256: 'a'.repeat(64), operations: [
    { kind: 'replace', char_id: 'char_001', character },
  ] },
  template_unavailable_reason: null,
} as unknown as CharacterRegistryView

afterEach(() => vi.restoreAllMocks())

it('保留后端来源绑定，提交免费人物输入后显示实际作业状态', async () => {
  const read = vi.spyOn(api, 'getCharacters').mockResolvedValue(view)
  const write = vi.spyOn(api, 'editCharacters').mockResolvedValue({ state: 'landed_red' } as Job)
  const user = userEvent.setup()
  renderRoute(<CharactersPanel sid="book-1" />)
  expect(read).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '独立人物登记与免费订正' }))
  await user.click(await screen.findByRole('button', { name: '载入当前订正模板' }))
  await user.type(screen.getByLabelText('人物订正人'), '人工复核员')
  await user.type(screen.getByLabelText('人物订正依据'), '原文明确称作掌柜')
  await user.click(screen.getByRole('button', { name: '提交人物订正（0 模型）' }))
  expect(write).toHaveBeenCalledWith('book-1', {
    actor: '人工复核员', reason: '原文明确称作掌柜', edit: view.edit_template,
  })
  expect(await screen.findByText(/订正作业状态：landed_red/)).toBeInTheDocument()
})

it('无效 JSON 留在本地，不发送写回请求', async () => {
  vi.spyOn(api, 'getCharacters').mockResolvedValue(view)
  const write = vi.spyOn(api, 'editCharacters')
  const user = userEvent.setup()
  renderRoute(<CharactersPanel sid="book-1" />)
  await user.click(screen.getByRole('button', { name: '独立人物登记与免费订正' }))
  await screen.findByText(/沈青/)
  await user.type(screen.getByLabelText('人物订正 JSON'), '不是 JSON')
  await user.type(screen.getByLabelText('人物订正人'), '人工复核员')
  await user.type(screen.getByLabelText('人物订正依据'), '原文证据')
  await user.click(screen.getByRole('button', { name: '提交人物订正（0 模型）' }))
  expect(screen.getByRole('alert')).toHaveTextContent('不是有效 JSON')
  expect(write).not.toHaveBeenCalled()
})
