import { missingPrepareState } from '@/test/prepare'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import type { ApprovalDoc } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { GateBar } from './GateBar'
import { useApprovals } from '@/hooks/useApprovals'

/**
 * 闸门按钮的动作分类。
 *
 * ⭐ 主守卫是「s4 这种没有 spawn 的闸门**不许**画成『未标注』」：
 * 「未标注」在这套词汇里的意思是**不知道它会不会写产物盘**（U-08），
 * 而没有 spawn 的确认我们完全知道——它只写 `_service/` 下的人工审批记录，
 * 产物盘归 CLI 独占（`R-11.3`）。把一个已知免费、不动产物的动作说成「不知道」，
 * 稀释的正是这个信号本身要保护的东西。
 */

const doc: ApprovalDoc = {
  v: 9, prepare: missingPrepareState(),
  sid: 'book-1',
  gate: { s0: true, s1: true, s4: false, s5: false, s6: false, s7: false },
  writing: false,
  design_review: {
    state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
    current_decision: null, history: [], binding_infeasibility: null,
    preflight: null, repair_capability: null,
  },
  structure: {
    state: 'missing',
    current_subject_sha256: 'a'.repeat(64),
    current_decision: null,
    history: [],
    binding_concerns: [],
  },
  writing_approval: {
    available: false, current_subject_sha256: null, current_decision: null, history: [],
  },
  closure: {
    state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
  },
  spawns: {
    s4: [{ kind: 'stage5.write', label: '写作', extra: null }],
  },
}

const enums = {
  nonbody_kinds: [],
  time_layers: [],
  style_words: [],
  invent: { allow: [], forbid: [], principle: '' },
  job_kinds: {},
  actions: [{
    kind: 'stage5.write',
    label: '写作',
    extra: null,
    paid: true,
    free_flag: null,
    writes_disk: true,
  }],
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

function mount(screenName: 'stage5' | 'stage4' | 'global', approval: ApprovalDoc = doc) {
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(approval)
  vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
  renderRoute(<GateBar screen={screenName} />)
}

describe('GateBar 的动作分类', () => {
  it.each(['stage6', 'stage7'] as const)('%s 使用专用审批面，不再由通用闸门宣称下一步已执行', async (stage) => {
    const approved = { ...doc, gate: { ...doc.gate, s5: true, s6: true, s7: true } }
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approved)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    function Loaded() {
      const { doc: current } = useApprovals()
      return current ? <span>审批已读回</span> : null
    }
    const { container } = renderRoute(<><GateBar screen={stage} /><Loaded /></>)
    await screen.findByText('审批已读回')
    expect(container.textContent).toBe('审批已读回')
  })

  it('global 屏挂在 s1：stage0 未确认时禁用，不发起 stage2 动作', async () => {
    const approve = vi.spyOn(api, 'approveGate')
    mount('global', { ...doc, gate: { ...doc.gate, s0: false, s1: false } })
    expect(await screen.findByRole('button', { name: /先过 stage0 剔除番外/ })).toBeDisabled()
    expect(approve).not.toHaveBeenCalled()
  })
  it('没有 spawn 的闸门不画成「未标注」——它只写 _service 的审批记录', async () => {
    mount('stage5', {
      ...doc,
      gate: { ...doc.gate, s4: true },
      writing: true,
    })
    const button = await screen.findByRole('button', { name: /确认成稿/ })
    expect(button.textContent).not.toContain('未标注')
    expect(button.textContent).not.toContain('写盘')
  })

  it('Stage4 不渲染会调用通用 gate/s4 的匿名确认按钮', async () => {
    const approve = vi.spyOn(api, 'approveGate')
    mount('stage4')
    await waitFor(() => expect(api.getApprovals).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /确认计划/ })).not.toBeInTheDocument()
    expect(approve).not.toHaveBeenCalled()
  })

  it('s1 已确认后只续跑服务端下发的下一相，不重复 approveGate', async () => {
    const next = { kind: 'stage2.blueprint', label: '机械准入通过后生成 stage2 逐章骨架', extra: null }
    const done: ApprovalDoc = {
      ...doc,
      spawns: { s1: [next] },
    }
    const liveEnums = {
      ...enums,
      job_kinds: { 'stage2.blueprint': { paid: true } },
      actions: [{
        kind: 'stage2.blueprint', label: next.label, extra: null,
        paid: true, free_flag: null, writes_disk: true,
      }],
    } as unknown as EnumsResponse
    vi.spyOn(api, 'getEnums').mockResolvedValue(liveEnums)
    vi.spyOn(api, 'getApprovals').mockResolvedValue(done)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const approve = vi.spyOn(api, 'approveGate')
    vi.spyOn(api, 'previewJob').mockResolvedValue({
      argv: 'python -m novel.cli run book-1 stage2.blueprint --yes',
      paid: true,
      label: next.label,
      kind: next.kind,
      steps: [{
        label: next.label,
        paid: true,
        writes: 'stage2/chapter_blueprints.json',
        argv: 'python -m novel.cli run book-1 stage2.blueprint --yes',
      }],
    })
    const start = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'stage2b-job', kind: next.kind, label: next.label,
      argv: 'python -m novel.cli run book-1 stage2.blueprint --yes',
      state: 'running', paid: true, started: 1, finished: null,
      sid: 'book-1', ordinal: null,
    })

    renderRoute(<><GateBar screen="global" /><ConfirmDialog /></>)
    fireEvent.click(await screen.findByRole('button', { name: '继续下一步' }))
    expect(await screen.findByText(/stage2\.blueprint --yes/)).toBeVisible()
    expect(approve).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认发起（花钱）' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith({
      ...next, required_inputs: undefined, sid: 'book-1', ordinal: null,
    }))
    expect(approve).not.toHaveBeenCalled()
  })

  it('actionOnly 失败态展示服务端给出的恢复命令', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...doc,
      gate: { ...doc.gate, s4: true },
      writing: true,
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'approveGate').mockRejectedValue(new ApiError(
      'server',
      '/novels/book-1/approvals/gate/s5',
      {
        status: 409,
        message: '盘面需要先恢复',
        hint: 'python -m novel.cli state book-1',
      },
    ))

    renderRoute(<GateBar screen="stage5" actionOnly />)
    fireEvent.click(await screen.findByRole('button', { name: /确认成稿/ }))

    expect(await screen.findByText('python -m novel.cli state book-1')).toBeVisible()
    expect(screen.getByText(/确认失败：盘面需要先恢复/)).toBeVisible()
  })
})
