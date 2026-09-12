import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { renderRoute } from '@/test/render'
import { stage2CoreFixture } from '@/api/mock'
import type { Job } from '@/types/jobs'
import { CharactersPanel } from './CharactersPanel'

const view = stage2CoreFixture().characters
view.sid = 'book-1'
const character = view.registry.characters.char_001
character.canonical = '沈青'
character.role = '茶馆掌柜'
character.aliases = [{ surface: '沈青', identity_revealed_from: 1,
  evidence_refs: character.first_appearance_evidence_refs },
{ surface: '掌柜', identity_revealed_from: 2, evidence_refs: character.role_evidence_refs }]
view.registry.characters = { char_001: character }
view.edit_template = { source_sha256: view.source_sha256, operations: [
  { kind: 'replace', char_id: 'char_001', character },
] }

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
  await screen.findByText('沈青')
  await user.type(screen.getByLabelText('人物订正 JSON'), '不是 JSON')
  await user.type(screen.getByLabelText('人物订正人'), '人工复核员')
  await user.type(screen.getByLabelText('人物订正依据'), '原文证据')
  await user.click(screen.getByRole('button', { name: '提交人物订正（0 模型）' }))
  expect(screen.getByRole('alert')).toHaveTextContent('不是有效 JSON')
  expect(write).not.toHaveBeenCalled()
})

it('显示称谓归属时点和实体证据，来源来自独立人物登记', async () => {
  vi.spyOn(api, 'getCharacters').mockResolvedValue(view)
  const user = userEvent.setup()
  renderRoute(<CharactersPanel sid="book-1" />)
  await user.click(screen.getByRole('button', { name: '独立人物登记与免费订正' }))
  expect(await screen.findByText('沈青')).toBeVisible()
  expect(screen.getByText('实体首次出场：第 1 章')).toBeVisible()
  expect(screen.getByText('掌柜 · 第 2 章起可绑定此实体')).toBeVisible()
  expect(screen.getByText('共享首抽')).toBeVisible()
  expect(screen.getAllByText(/^证据 ch/).length).toBeGreaterThan(0)
  expect(screen.queryByText(/by|from_chapter/)).not.toBeInTheDocument()
})
