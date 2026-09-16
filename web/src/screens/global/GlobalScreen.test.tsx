import { missingPrepareState } from '@/test/prepare'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import type { Stage2Global } from '@/types/stages'
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

function emptyEnums(actions: { kind: string; label: string; extra: null }[]) {
  return {
    nonbody_kinds: [],
    time_layers: [],
    style_words: [],
    invent: { allow: [], forbid: [], principle: '' },
    job_kinds: Object.fromEntries(actions.map((action) => [action.kind, { label: action.label, paid: true }])),
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
    actions: actions.map((action) => ({ ...action, paid: true, free_flag: null, writes_disk: true })),
    spoiler_disclosure: disclosure,
  }
}

function approvals(spawns: ApprovalDoc['spawns']): ApprovalDoc {
  return {
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
      state: 'unavailable', current_subject_sha256: null,
      current_decision: null, history: [], binding_concerns: [],
    },
    writing_approval: {
      available: false, current_subject_sha256: null, current_decision: null, history: [],
    },
    closure: {
      state: 'missing', workpack: null, next_step: null, current_decision: null, history: [],
    },
    spawns,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('GlobalScreen disclosure', () => {
  it('只读监控：不渲染逐章骨架生成写动作', async () => {
    const blueprint = {
      kind: 'stage2.blueprint',
      label: '机械准入通过后生成 stage2 逐章骨架',
      extra: null,
    }
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals({ s1: [blueprint] }))
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())
    vi.spyOn(api, 'getEnums').mockResolvedValue(emptyEnums([blueprint]))

    renderRoute(<GlobalScreen />, '/n/book-1/global')

    expect(await screen.findByRole('heading', { name: /全局设定/ })).toBeVisible()
    expect(screen.queryByRole('group', { name: '机械准入通过后生成逐章骨架' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '继续下一步' })).not.toBeInTheDocument()
    expect(screen.getByText(/Stage2 语义未审：/)).toBeVisible()
  })

  it('is present while stage2 is loading', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(emptyEnums([]))
    vi.spyOn(api, 'getGlobal').mockImplementation(() => new Promise(() => undefined))
    renderRoute(<GlobalScreen />)
    expect(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ })).toBeVisible()
    expect(await screen.findByText(disclosure.notice)).toBeVisible()
    expect(screen.getByText(/读全局设定/)).toBeVisible()
    expect(screen.getByText(/Stage2 语义未审：/)).toBeVisible()
  })

  it.each([
    ['404', new ApiError('nosample', '/global', { scope: 'book' })],
    ['error', new Error('boom')],
  ])('remains present in the %s state', async (_label, error) => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(emptyEnums([]))
    vi.spyOn(api, 'getGlobal').mockRejectedValue(error)
    renderRoute(<GlobalScreen />)
    expect(await screen.findByText(disclosure.notice)).toBeVisible()
    expect(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ })).toBeVisible()
  })

  it('展示独立时间轴、精确事实引用、合法空背景和不可验证证据', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(emptyEnums([]))
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
    expect(screen.getByText(/同章情节先后见校验报告/)).toBeVisible()
  })
})
