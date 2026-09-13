import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { plotFlowFixture, PLOT_SAMPLE_SID } from '@/api/mock'
import type { ChapterCharacter } from '@/types/plot.generated'
import { Stage4Screen } from './Stage4Screen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock('@/hooks/useSample', () => ({
  useSampleCoverage: () => ({ hasSample: () => true }),
}))
vi.mock('./DesignReviewPanel', () => ({ DesignReviewPanel: () => null }))
vi.mock('./StructureApprovalPanel', () => ({ StructureApprovalPanel: () => null }))

afterEach(() => vi.restoreAllMocks())

function publicActor(): ChapterCharacter {
  return {
    actor_ref: 'actor_' + 'a'.repeat(24),
    display_name: '娘子',
    surfaces: ['娘子', '苏姑娘'],
    surface_allowed_slots: { 娘子: ['c01s01', 'c01s02'], 苏姑娘: ['c01s02'] },
    role: '经营绣坊的女子',
    role_allowed_slots: ['c01s02'],
    allowed_slots: ['c01s01', 'c01s02'],
  }
}

async function renderMaterials(characters: ChapterCharacter[]) {
  const response = plotFlowFixture()['http:stage4/1']
  expect(response.ctx_chapter).not.toBeNull()
  response.ctx_chapter!.characters = characters
  const load = vi.spyOn(api, 'getStage4').mockResolvedValue(response)
  render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })}>
      <MemoryRouter initialEntries={['/n/' + PLOT_SAMPLE_SID + '/stage4/1']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes><Route path="/n/:sid/stage4/:o" element={<Stage4Screen />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const roster = await screen.findByRole('region', { name: '人物名册' })
  expect(load).toHaveBeenCalledWith(PLOT_SAMPLE_SID, 1)
  return within(roster)
}

describe('Stage4 写手人物材料', () => {
  it('显示每个称谓与身份描述的独立场景许可，并保留 opaque 标识', async () => {
    const actor = publicActor()
    const roster = await renderMaterials([actor])
    const row = roster.getByRole('listitem')
    expect(row).toHaveTextContent('娘子 · ' + actor.actor_ref)
    expect(row).toHaveTextContent('人物可出现场景：c01s01、c01s02')
    expect(row).toHaveTextContent('称谓「娘子」可用场景：c01s01、c01s02')
    expect(row).toHaveTextContent('称谓「苏姑娘」可用场景：c01s02')
    expect(row).not.toHaveTextContent('称谓「苏姑娘」可用场景：c01s01')
    expect(row).toHaveTextContent('公开身份：经营绣坊的女子')
    expect(row).toHaveTextContent('身份描述可用场景：c01s02')
    expect(row).toHaveClass('whitespace-pre-wrap')
    expect(row).not.toHaveTextContent('char_')
  })

  it('独立公开呈现不按显示称谓合并，空身份保持明确缺席', async () => {
    const first = publicActor()
    const second: ChapterCharacter = {
      actor_ref: 'actor_' + 'b'.repeat(24), display_name: '娘子', surfaces: ['娘子'],
      surface_allowed_slots: { 娘子: ['c01s02'] }, allowed_slots: ['c01s02'],
      role: null, role_allowed_slots: [],
    }
    const roster = await renderMaterials([first, second])
    expect(roster.getByText('2 个公开呈现')).toBeInTheDocument()
    const rows = roster.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[1]).toHaveTextContent(second.actor_ref)
    expect(rows[1]).toHaveTextContent('公开身份：未提供公开身份描述')
    expect(rows[1]).toHaveTextContent('身份描述可用场景：无许可')
    expect(rows[1]).not.toHaveTextContent(first.role!)
  })

  it('缺失称谓许可明确展示缺失，不沿用人物总场景', async () => {
    const actor = publicActor()
    delete actor.surface_allowed_slots['苏姑娘']
    const roster = await renderMaterials([actor])
    expect(roster.getByRole('listitem')).toHaveTextContent('称谓「苏姑娘」可用场景：许可缺失')
    expect(roster.getByRole('listitem')).not.toHaveTextContent('称谓「苏姑娘」可用场景：c01s01')
  })
})
