import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import type { GlobalReviewResponse, Stage2Global } from '@/types/stages'
import { GlobalScreen } from './GlobalScreen'
import { stage2CoreFixture } from '@/api/mock'

const disclosure = {
  mode: 'display_only' as const,
  security_boundary: false as const,
  server_sends_full_data: true as const,
  hidden_paths: ['arc_secrets'],
  known_leakage_surfaces: ['network'],
  consumers: ['GlobalScreen'],
  notice: '测试说明：服务端仍全量下发；这不是安全边界。',
}

function globalView(): Stage2Global {
  const value = stage2CoreFixture().global
  const era = 'ev_' + '1'.repeat(24)
  const missing = 'ev_' + '2'.repeat(24)
  value.sid = 'book-1'
  value.setting = { era: '古代', era_evidence_refs: [era], social_context: null, social_context_evidence_refs: [] }
  value.evidence_anchors[era] = { chapter_ordinal: 3, start: 10, end: 14, text_sha256: '1'.repeat(64) }
  value.evidence_anchors[missing] = { chapter_ordinal: 8, start: 10, end: 14, text_sha256: '2'.repeat(64) }
  value.evidence_snippets[era] = '古代证据'
  value.evidence_snippets[missing] = null
  value.relations[0] = { ...value.relations[0], evidence_refs: [missing],
    effective_from: 2, effective_until: 3, revealed_from: 4 }
  value.arc_secrets = [{ secret_id: 'secret_01', fact: null,
    fact_ref: { kind: 'relation', relation_id: value.relations[0].relation_id },
    evidence_refs: [], setup_chapters: [4], revealed_from: 4 }]
  return value
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('GlobalScreen disclosure', () => {
  it('renders the server-provided blueprint continuation while global review is pending', async () => {
    const blueprint = {
      kind: 'stage2.blueprint',
      label: '生成 stage2 逐章骨架；可与全局人工复核并行',
      extra: null,
    }
    const approvals: ApprovalDoc = {
      v: 7,
      sid: 'book-1',
      gate: { s0: true, s1: true, s2: false, s4: false, s5: false, s6: false, s7: false },
      seen: { read: false, global: true },
      writing: false,
      design_review: {
        state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
        current_decision: null, history: [], binding_infeasibility: null,
        preflight: null, repair_capability: null,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      writing_approval: {
        available: false, current_subject_sha256: null, current_decision: null, history: [],
      },
      closure: {
        state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
      },
      spawns: { s1: [blueprint] },
    }
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals)
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())
    vi.spyOn(api, 'getGlobalReview').mockRejectedValue(
      new ApiError('nosample', '/global-review', { scope: 'book' }),
    )
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      nonbody_kinds: [],
      time_layers: [],
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      job_kinds: { 'stage2.blueprint': { label: blueprint.label, paid: true } },
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
        ...blueprint,
        paid: true,
        free_flag: null,
        writes_disk: true,
      }],
      spoiler_disclosure: disclosure,
    })

    renderRoute(<GlobalScreen />, '/n/book-1/global')

    const continuation = await screen.findByRole('group', { name: '接受全局后生成逐章骨架' })
    expect(continuation).toHaveTextContent('全局接受后生成逐章骨架')
    expect(continuation).toHaveTextContent('两个节点接受后才进入 Stage4')
    expect(screen.getByRole('button', { name: '继续下一步' })).toBeVisible()
  })

  it('is present while stage2 is loading', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      nonbody_kinds: [],
      time_layers: [],
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      job_kinds: {},
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
      actions: [],
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
      nonbody_kinds: [],
      time_layers: [],
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      job_kinds: {},
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
      actions: [],
      spoiler_disclosure: disclosure,
    })
    vi.spyOn(api, 'getGlobal').mockRejectedValue(error)
    renderRoute(<GlobalScreen />)
    expect(await screen.findByText(disclosure.notice)).toBeVisible()
    expect(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ })).toBeVisible()
  })

  it('展示独立时间轴、精确事实引用、合法空背景和不可验证证据', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      nonbody_kinds: [],
      time_layers: [],
      style_words: [],
      invent: { allow: [], forbid: [], principle: '' },
      job_kinds: {},
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
      actions: [],
      spoiler_disclosure: disclosure,
    })
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())

    renderRoute(<GlobalScreen />, '/n/book-1/global')
    expect(await screen.findByText('证据 ch3 · “古代证据”')).toBeVisible()
    expect(screen.getByText('源文未建立')).toBeVisible()
    expect(screen.getByText('共享首抽')).toBeVisible()
    expect(screen.getAllByRole('tab')).toHaveLength(4)
    expect(screen.getByRole('tab', { name: /^世界规则/ })).toBeVisible()
    expect(screen.queryByText('源文案与开篇')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /^长期关系/ }))
    expect(screen.getByText(/至第 3 章（含）/)).toHaveTextContent('读者第 4 章起可见')
    expect(screen.getByText(/证据不可验证 · 第 8 章/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ }))
    fireEvent.click(await screen.findByRole('tab', { name: /^跨章秘密/ }))
    expect(await screen.findByText(/引用关系 rel_01/)).toBeVisible()
    expect(screen.getByText('读者第 4 章首次揭示')).toBeVisible()
    expect(screen.getByText(/同章情节先后由审阅核实/)).toBeVisible()
  })
})

function safeReview(token: string | null): GlobalReviewResponse {
  return {
    source_sha256: 'd'.repeat(64),
    current_subject_sha256: 'd'.repeat(64),
    current: true,
    approved: true,
    generation_safe: true,
    quarantined_groups: [],
    review_token: token,
    effect_counts: {
      blocking: { pending: 0, PASS: 1, FAIL: 0, UNVER: 0 },
      disclosure: { pending: 0, PASS: 0, FAIL: 0, UNVER: 0 },
    },
    items: [],
  }
}

function unseenApprovals(): ApprovalDoc {
  return {
    v: 7,
    sid: 'book-1',
    gate: { s0: true, s1: true, s2: false, s4: false, s5: false, s6: false, s7: false },
    seen: { read: false, global: false },
    writing: false,
    design_review: {
      state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
      current_decision: null, history: [], binding_infeasibility: null,
      preflight: null, repair_capability: null,
    },
    structure: {
      state: 'unavailable', current_subject_sha256: null,
      current_decision: null, history: [], binding_concerns: [],
    },
    writing_approval: {
      available: false, current_subject_sha256: null, current_decision: null, history: [],
    },
    closure: {
      state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
    },
    spawns: {},
  }
}

describe('GlobalScreen seen.global 显式确认', () => {
  it('打开页面不再自动标记已读；填确认人后才用服务端 token 提交', async () => {
    vi.spyOn(api, 'getApprovals').mockResolvedValue(unseenApprovals())
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue(safeReview('s2gr1_' + '5'.repeat(64)))
    vi.spyOn(api, 'getEnums').mockRejectedValue(new Error('enums 不参与本测试'))
    const markSeen = vi.spyOn(api, 'markSeen').mockResolvedValue({
      ...unseenApprovals(), seen: { read: false, global: true },
    })

    renderRoute(<GlobalScreen />, '/n/book-1/global')

    const group = await screen.findByRole('group', { name: '全局复核通读确认' })
    expect(group).toHaveTextContent('通读当前全局复核后再确认')
    const button = await screen.findByRole('button', { name: '确认已通读当前全局复核' })
    await waitFor(() => expect(button).toBeDisabled())
    expect(markSeen).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('确认人'), { target: { value: '人工审阅者' } })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)

    await waitFor(() => expect(markSeen).toHaveBeenCalledWith('book-1', 'global', {
      review_token: 's2gr1_' + '5'.repeat(64),
      actor: '人工审阅者',
    }))
    expect(await screen.findByText('已记录：当前全局复核已通读确认')).toBeVisible()
  })

  it('review 不 current 或阻断面未安全时不提供确认动作', async () => {
    vi.spyOn(api, 'getApprovals').mockResolvedValue(unseenApprovals())
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())
    vi.spyOn(api, 'getGlobalReview').mockResolvedValue({
      ...safeReview(null), generation_safe: false,
    })
    vi.spyOn(api, 'getEnums').mockRejectedValue(new Error('enums 不参与本测试'))
    const markSeen = vi.spyOn(api, 'markSeen')

    renderRoute(<GlobalScreen />, '/n/book-1/global')

    const group = await screen.findByRole('group', { name: '全局复核通读确认' })
    expect(group).toHaveTextContent('暂不能确认')
    fireEvent.change(screen.getByLabelText('确认人'), { target: { value: '人工审阅者' } })
    expect(screen.getByRole('button', { name: '确认已通读当前全局复核' })).toBeDisabled()
    expect(markSeen).not.toHaveBeenCalled()
  })
})
