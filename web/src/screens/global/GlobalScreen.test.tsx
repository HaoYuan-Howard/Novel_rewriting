import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { renderRoute } from '@/test/render'
import { GlobalScreen } from './GlobalScreen'

const disclosure = {
  mode: 'display_only' as const,
  security_boundary: false as const,
  server_sends_full_data: true as const,
  hidden_paths: ['characters[].fate', 'secrets', 'arc.climax', 'arc.ending'],
  known_leakage_surfaces: ['network'],
  consumers: ['GlobalScreen'],
  notice: '测试说明：服务端仍全量下发；这不是安全边界。',
}

afterEach(cleanup)

describe('GlobalScreen disclosure', () => {
  it('is present while stage2 is loading', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      hinge_kinds: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      voice_keys: [],
      hook_keys: [],
      job_kinds: {},
      actions: [],
      uncovered_min_chars: 0,
      spoiler_disclosure: disclosure,
    })
    vi.spyOn(api, 'getGlobal').mockImplementation(() => new Promise(() => undefined))
    renderRoute(<GlobalScreen />)
    expect(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ })).toBeVisible()
    expect(await screen.findByText(disclosure.notice)).toBeVisible()
    expect(screen.getByText(/读全局设定/)).toBeVisible()
  })

  it.each([
    ['404', new ApiError('nosample', '/global', { scope: 'book' })],
    ['error', new Error('boom')],
  ])('remains present in the %s state', async (_label, error) => {
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      hinge_kinds: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      voice_keys: [],
      hook_keys: [],
      job_kinds: {},
      actions: [],
      uncovered_min_chars: 0,
      spoiler_disclosure: disclosure,
    })
    vi.spyOn(api, 'getGlobal').mockRejectedValue(error)
    renderRoute(<GlobalScreen />)
    expect(await screen.findByText(disclosure.notice)).toBeVisible()
    expect(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ })).toBeVisible()
  })
})
