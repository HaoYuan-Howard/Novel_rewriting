import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import type { GlobalFactEvidence, GlobalReviewResponse, Stage2Global } from '@/types/stages'
import { GlobalScreen } from './GlobalScreen'

const disclosure = {
  mode: 'display_only' as const,
  security_boundary: false as const,
  server_sends_full_data: true as const,
  hidden_paths: ['characters[].fate', 'secrets', 'source_blurb.facts', 'source_blurb.opening'],
  known_leakage_surfaces: ['network'],
  consumers: ['GlobalScreen'],
  notice: '测试说明：服务端仍全量下发；这不是安全边界。',
}

function evidence(text: string): GlobalFactEvidence {
  return {
    chapter_ordinal: 3,
    source: { text, start: 10, end: 10 + text.length },
  }
}

function globalView(): Stage2Global {
  const name = { raw: 'char_001', display: '李宝珠', resolved: true }
  return {
    sid: 'book-1',
    title: '测试书',
    setting: {
      时代背景: '古代',
      era_evidence: evidence('古代证据'),
      世界观: '现实世界',
      worldview_evidence: evidence('世界观证据'),
    },
    characters: [
      {
        char_id: 'char_001',
        canonical: '李宝珠',
        name_distinctiveness: 'high',
        role: '主角',
        role_evidence: evidence('主角证据'),
        fate: '最终掌管怡红院',
        fate_evidence: { chapter_ordinal: 8, source: null },
        address: [],
      },
    ],
    relations: [
      {
        relation_id: 'rel_01',
        subject: name,
        predicate: '暗中结盟',
        predicate_evidence: evidence('结盟证据'),
        object: name,
        visibility: { kind: 'via_secret', secret_id: 'secret_01' },
        valid_from_chapter: 2,
        valid_through_chapter: null,
      },
    ],
    world_rules: [],
    secrets: [
      {
        secret_id: 'secret_01',
        fact: '一条秘密',
        fact_evidence: evidence('秘密证据'),
        knowledge_events: [
          {
            subject_kind: 'group',
            subject: { raw: '在场众人', display: '在场众人', resolved: true },
            known_from_chapter: 2,
            evidence: evidence('众人知情证据'),
          },
        ],
        setup_kind: 'cold',
        setup_chapters: [],
        first_reveal_chapter: 3,
      },
    ],
    source_blurb: {
      facts: [{ kind: 'supported', claim_id: 'hook_001', claim: '真实文案事实',
        evidence: evidence('真实文案事实证据') }],
      opening: { kind: 'proposed', through_chapter: 2, rationale: '开篇事件横跨前两章',
        boundary_evidence: evidence('终点证据') },
    },
    unresolved_refs: 0,
    invalid_exists: true,
    freshness: 'stale',
    reason: 'file_changed',
    ghosts: [],
    _provenance: { root_clean_sha1: '0'.repeat(40) },
  }
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

  it('shows formal evidence failures, explicit relation visibility, and timed group knowledge', async () => {
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

    const withHook = globalView()
    withHook.source_blurb.facts.push({
      kind: 'hook_supported', claim_id: 'hook_002', claim: '钩子独有的背景',
      hook_evidence: { source: { text: '仅源文案支持', start: 0, end: 6 }, unresolved_locator: null },
    })
    vi.mocked(api.getGlobal).mockResolvedValue(withHook)
    renderRoute(<GlobalScreen />, '/n/book-1/global')
    expect(await screen.findByText('证据 ch3 · “古代证据”')).toBeVisible()
    expect(screen.getByText('证据定位失败 · 第 8 章')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /^关系/ }))
    expect(screen.getByText('随 secret_01 揭示')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ }))
    fireEvent.click(await screen.findByRole('button', { name: /^秘密/ }))
    expect(await screen.findByText('在场众人（群体 · 第 2 章起）')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /^源文案与开篇/ }))
    expect(screen.getByText('原钩子独立支持；正文无需重复，审阅状态另行裁决。')).toBeVisible()
    expect(screen.getByText('文案证据 · “仅源文案支持”')).toBeVisible()
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
