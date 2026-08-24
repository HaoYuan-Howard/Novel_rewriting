import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { cleanup, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { StyleScreen } from './StyleScreen'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function mount() {
  const [style, approvals, enums] = await Promise.all([
    mockApi.getStyle(SAMPLE_SID),
    mockApi.getApprovals(SAMPLE_SID),
    mockApi.getEnums(),
  ])
  vi.spyOn(api, 'getStyle').mockResolvedValue(style)
  vi.spyOn(api, 'getCheck').mockResolvedValue(null)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals)
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
  renderRoute(<StyleScreen />, `/n/${SAMPLE_SID}/style`)
  return style
}

describe('Stage3 v2 机械基线屏', () => {
  it('只呈现存量/当前机械测量，不复活定性文风字段', async () => {
    const style = await mount()

    expect(await screen.findByRole('heading', { name: '机械基线 · stage3' })).toBeVisible()
    expect(screen.getByText(/Stage3 v2 不再调用模型抽取文风/)).toBeVisible()
    expect(screen.getByText('当前重算')).toBeVisible()
    expect(screen.getByText('存量 Stage3 v2 机械档位')).toBeVisible()
    expect(screen.getByText('存量与当前测量一致')).toBeVisible()

    expect(style).not.toHaveProperty('voice')
    expect(style).not.toHaveProperty('surface_habits')
    expect(style).not.toHaveProperty('hook_style')
    expect(screen.queryByText('声音画像')).not.toBeInTheDocument()
    expect(screen.queryByText('表层习惯')).not.toBeInTheDocument()
    expect(screen.queryByText('开篇文案风格')).not.toBeInTheDocument()
  })
})
