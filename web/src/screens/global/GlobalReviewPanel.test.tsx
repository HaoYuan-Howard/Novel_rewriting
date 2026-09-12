import { api } from '@/api/endpoints'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import type { EnumsResponse } from '@/types/enums'
import type { GlobalReviewResponse } from '@/types/stages'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { GlobalReviewPanel } from './GlobalReviewPanel'

const hashes = ['a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64)]

const review: GlobalReviewResponse = {
  source_sha256: 'd'.repeat(64),
  current_subject_sha256: 'd'.repeat(64),
  current: true,
  approved: false,
  generation_safe: false,
  quarantined_groups: [],
  review_token: null,
  effect_counts: {
    blocking: { pending: 2, PASS: 0, FAIL: 1, UNVER: 1 },
    disclosure: { pending: 0, PASS: 0, FAIL: 0, UNVER: 0 },
  },
  items: [
    {
      item_id: 'rules',
      kind: 'world_rule_set_complete',
      subject_path: 'world_rules',
      subject_sha256: hashes[0],
      state: 'pending',
      resolution_required: false,
      evidence: [],
      actor: null,
      decided_at: null,
      reason: null,
      generation_disposition: null,
      effect: 'blocking',
      group_key: 'set:world_rules',
    },
    {
      item_id: 'secrets',
      kind: 'secret_set_complete',
      subject_path: 'arc_secrets',
      subject_sha256: hashes[1],
      state: 'FAIL',
      resolution_required: false,
      evidence: [],
      actor: 'reviewer-old',
      decided_at: '2026-08-16T00:00:00Z',
      reason: '疑似漏抽',
      generation_disposition: null,
      effect: 'blocking',
      group_key: 'set:secrets',
    },
    {
      item_id: 'reveal',
      kind: 'reveal',
      subject_path: 'arc_secrets.secret_01.revealed_from',
      subject_sha256: hashes[2],
      state: 'UNVER',
      resolution_required: false,
      evidence: [],
      actor: 'reviewer-old',
      decided_at: '2026-08-16T00:00:00Z',
      reason: '证据不足',
      generation_disposition: null,
      effect: 'blocking',
      group_key: 'secret:secret_01',
    },
    {
      // 当前 Characters v2 的身份义务与长期知识一起阻断。
      item_id: 'identity',
      kind: 'character_identity',
      subject_path: 'characters.char_001',
      subject_sha256: hashes[2],
      state: 'pending',
      resolution_required: false,
      evidence: [],
      actor: null,
      decided_at: null,
      reason: null,
      generation_disposition: null,
      effect: 'blocking',
      group_key: 'character:char_001',
    },
  ],
}

const enums: EnumsResponse = {
  nonbody_kinds: [],
  time_layers: [],
  style_words: [],
  invent: { allow: [], forbid: [], principle: '' },
  job_kinds: { 'stage2.global': { label: 'stage2', paid: true } },
  structure_modes: [],
  structure_change_kinds: [],
  structure_approval_decisions: [],
  structure_approval_states: [],
  design_strategy_decisions: [],
  design_chapter_decisions: [],
  design_reviewer_kinds: [],
  design_repair_codes: [],
  design_constraint_operation_kinds: [],
  design_repair_constraint_arity: {},
  design_review_states: [],
  design_preflight_statuses: [],
  stage5_must_appear_rulings: [],
  stage5_closure_groups: [],
  stage5_closure_states: [],
  binding_concern_codes: [],
  binding_infeasibility_codes: [],
  approval_workpack_groups: [],
  approval_workpack_kinds: [],
  approval_workpack_actions: [],
  approval_workpack_effects: [],
  approval_workpack_group_effects: {},
  approval_workpack_dispositions: [],
  final_approval_workpack_kinds: [],
  actions: [{
    kind: 'stage2.global',
    label: '提交全局复核',
    extra: ['--review-decisions'],
    paid: false,
    free_flag: '--review-decisions',
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
}

function Harness() {
  return (
    <>
      <GlobalReviewPanel sid="book-1" />
      <ConfirmDialog />
    </>
  )
}

describe('GlobalReviewPanel', () => {
  it('keeps quarantined UNVER visible while showing that generation may continue', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue({
      ...review,
      generation_safe: true,
      quarantined_groups: ['secret:secret_01'],
      items: review.items.map((item) => item.item_id === 'reveal'
        ? {
            ...item,
            generation_disposition: {
              action: 'quarantine_group' as const,
              reason: '证据不足，整组退出写作输入',
            },
          }
        : item),
    })
    renderRoute(<GlobalReviewPanel sid="book-1" />)

    expect(await screen.findByText('可继续：隔离 UNVER 后安全')).toBeVisible()
    expect(screen.getByText('secret:secret_01')).toBeVisible()
    expect(screen.getByText(/写作处置：隔离完整事实组/)).toBeVisible()
    expect(screen.getAllByText('UNVER').some((element) => element.tagName === 'SPAN')).toBe(true)
    expect(screen.queryByText('阻断面：全部 PASS')).not.toBeInTheDocument()
  })

  it('loads every unresolved state and submits an optimistic hash-bound decision with evidence', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue(review)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'previewJob').mockResolvedValue({
      argv: 'python -m novel.cli run stage2.global book-1 --review-decisions private.json',
      paid: false,
      label: '提交 1 项 stage2 全局复核',
      kind: 'stage2.global',
      steps: [{
        label: '提交逐项复核',
        paid: false,
        writes: 'stage2/global_review.json',
        argv: 'python -m novel.cli run stage2.global book-1 --review-decisions private.json',
      }],
    })
    const submit = vi.spyOn(api, 'decideGlobalReview').mockResolvedValue({
      id: 'job-review',
      kind: 'stage2.global',
      label: 'stage2 全局复核决策',
      argv: 'python -m novel.cli run stage2.global',
      state: 'landed_red',
      paid: false,
      started: 1,
      finished: 2,
      sid: 'book-1',
      ordinal: null,
    })
    renderRoute(<Harness />)

    expect(await screen.findByLabelText('选择 world_rules')).toBeVisible()
    expect(screen.getByText('arc_secrets')).toBeVisible()
    expect(screen.getByText('arc_secrets.secret_01.revealed_from')).toBeVisible()
    expect(screen.getAllByText('pending').length).toBeGreaterThan(0)
    expect(screen.getAllByText('FAIL').some((element) => element.tagName === 'SPAN')).toBe(true)
    expect(screen.getAllByText('UNVER').some((element) => element.tagName === 'SPAN')).toBe(true)
    expect(screen.getByText('Stage4 保持关闭')).toBeVisible()

    fireEvent.change(screen.getByLabelText(/自报身份/), {
      target: { value: 'reviewer-a' },
    })
    fireEvent.click(screen.getByLabelText('选择 world_rules'))
    fireEvent.change(screen.getByLabelText('world_rules 结论'), {
      target: { value: 'PASS' },
    })
    fireEvent.change(screen.getByLabelText('world_rules 理由'), {
      target: { value: '已逐项对照来源' },
    })
    fireEvent.change(screen.getByLabelText('world_rules evidence artifact'), {
      target: { value: 'private/source.txt' },
    })
    fireEvent.change(screen.getByLabelText('world_rules evidence sha256'), {
      target: { value: 'e'.repeat(64) },
    })
    fireEvent.change(screen.getByLabelText('world_rules evidence start'), {
      target: { value: '10' },
    })
    fireEvent.change(screen.getByLabelText('world_rules evidence end'), {
      target: { value: '20' },
    })

    fireEvent.click(screen.getByRole('button', { name: /提交 1 项复核/ }))
    expect(await screen.findByText(/--review-decisions private\.json/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '发起' }))

    await waitFor(() => expect(submit).toHaveBeenCalledWith('book-1', {
      actor: 'reviewer-a',
      decisions: [{
        item_id: 'rules',
        expected_subject_sha256: hashes[0],
        state: 'PASS',
        reason: '已逐项对照来源',
        evidence: [{
          artifact: 'private/source.txt',
          subject_sha256: 'e'.repeat(64),
          start: 10,
          end: 20,
        }],
      }],
    }))
  })

  it('disables decisions when the read model is not bound to current GlobalGrounding', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue({ ...review, current: false })
    renderRoute(<GlobalReviewPanel sid="book-1" />)
    expect(await screen.findByText(/盘上复核没有绑定当前人物与全局知识/)).toBeVisible()
    expect(screen.getByRole('button', { name: /提交 0 项复核/ })).toBeDisabled()
  })
})

describe('Stage2 复核卡片', () => {
  it('当前人物与长期知识按主体成卡，完整呈现服务端阻断义务', async () => {
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue(review)
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    renderRoute(<Harness />)

    const cards = await screen.findAllByLabelText(/^复核卡 /)
    // 当前所有义务均阻断，按稳定主体类别排序。
    expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual([
      '复核卡 set:secrets',
      '复核卡 set:world_rules',
      '复核卡 character:char_001',
      '复核卡 secret:secret_01',
    ])
    expect(screen.getByText('跨章秘密')).toBeVisible()
    expect(screen.getByText('人物')).toBeVisible()
    // 逐项徽标直接转录服务端的 effect，不在这里重算一份分类。
    expect(screen.queryByText('只披露')).not.toBeInTheDocument()
    expect(screen.getAllByText('阻断 Stage4')).toHaveLength(4)
    expect(screen.queryByText(/披露未决/)).not.toBeInTheDocument()
  })

  it('卡片全选展开成逐项选择，仍允许单项撤销', async () => {
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue(review)
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    renderRoute(<Harness />)

    await screen.findAllByLabelText(/^复核卡 /)
    fireEvent.click(screen.getByLabelText('全选 secret:secret_01'))
    const single = screen.getByLabelText(
      '选择 arc_secrets.secret_01.revealed_from') as HTMLInputElement
    expect(single.checked).toBe(true)

    fireEvent.click(single)
    expect(single.checked).toBe(false)
    expect((screen.getByLabelText('全选 secret:secret_01') as HTMLInputElement).checked)
      .toBe(false)
  })
})
