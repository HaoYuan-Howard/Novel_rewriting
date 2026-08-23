import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import type { ApprovalDoc } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { GateBar } from './GateBar'

/**
 * 闸门按钮的动作分类。
 *
 * ⭐ 主守卫是「s3 这种没有 spawn 的闸门**不许**画成『未标注』」：
 * 「未标注」在这套词汇里的意思是**不知道它会不会写产物盘**（U-08），
 * 而没有 spawn 的确认我们完全知道——它只写 `_service/` 下的人工审批记录，
 * 产物盘归 CLI 独占（`R-11.3`）。把一个已知免费、不动产物的动作说成「不知道」，
 * 稀释的正是这个信号本身要保护的东西。
 */

const doc: ApprovalDoc = {
  v: 1,
  sid: 'book-1',
  gate: { s0: true, s1: true, s2: true, s3: false, s4: false, s5: false, s6: false, s7: false },
  seen: { read: true, global: true },
  deep: [],
  writing: false,
  spawns: {
    s4: [{ kind: 'stage5.write', label: '写作', extra: null, mode: null }],
  },
}

const enums = {
  hinge_kinds: [],
  nonbody_kinds: [],
  time_layers: [],
  hinge_groups: {},
  style_words: [],
  invent: { allow: [], forbid: [], principle: '' },
  voice_keys: [],
  hook_keys: [],
  job_kinds: {},
  actions: [{
    kind: 'stage5.write',
    label: '写作',
    extra: null,
    mode: null,
    paid: true,
    free_flag: null,
    writes_disk: true,
  }],
  uncovered_min_chars: 0,
  spoiler_disclosure: {
    mode: 'display_only',
    security_boundary: false,
    server_sends_full_data: true,
    hidden_paths: [],
    known_leakage_surfaces: [],
    consumers: [],
    notice: 'test',
  },
} as unknown as EnumsResponse

function mount(screenName: 'style' | 'stage4') {
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
  renderRoute(<GateBar screen={screenName} />)
}

describe('GateBar 的动作分类', () => {
  it('没有 spawn 的闸门不画成「未标注」——它只写 _service 的审批记录', async () => {
    mount('style')
    const button = await screen.findByRole('button', { name: /确认文风/ })
    expect(button.textContent).not.toContain('未标注')
    expect(button.textContent).not.toContain('写盘')
  })

  it('有付费 spawn 的闸门照旧标成花钱', async () => {
    mount('stage4')
    const button = await screen.findByRole('button', { name: /确认计划/ })
    // 付费优先于写盘：paid 命中就不再看 writes_disk
    expect(button.textContent).not.toContain('未标注')
    expect(screen.getByText(/花钱/)).toBeVisible()
  })

  it('actionOnly 失败态展示服务端给出的恢复命令', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'approveGate').mockRejectedValue(new ApiError(
      'server',
      '/novels/book-1/approvals/gate/s3',
      {
        status: 409,
        message: '盘面需要先恢复',
        hint: 'python -m novel.cli state book-1',
      },
    ))

    renderRoute(<GateBar screen="style" actionOnly />)
    fireEvent.click(await screen.findByRole('button', { name: /确认文风/ }))

    expect(await screen.findByText('python -m novel.cli state book-1')).toBeVisible()
    expect(screen.getByText(/确认失败：盘面需要先恢复/)).toBeVisible()
  })
})
