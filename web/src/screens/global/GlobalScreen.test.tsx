import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import type { GlobalFactEvidence, Stage2Global } from '@/types/stages'
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
    arc: {
      conflict_stages: [{ stage: '反击', chapters: [1, 2, 3] }],
      climax: '高潮',
      ending: '结局',
      arc_tracks: [
        {
          subject: name,
          trajectory: '从受困到反击',
          trajectory_evidence: evidence('弧线证据'),
        },
      ],
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
      v: 5,
      sid: 'book-1',
      gate: { s0: true, s1: true, s2: false, s4: false, s5: false, s6: false, s7: false },
      seen: { read: false, global: true },
      writing: false,
      design_review: {
        state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
        current_decision: null, history: [], binding_infeasibility: null, repair_capability: null,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: { s1: [blueprint] },
    }
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals)
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())
    vi.spyOn(api, 'getGlobalReview').mockRejectedValue(
      new ApiError('nosample', '/global-review', { scope: 'book' }),
    )
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      hinge_kinds: [],
      fact_atom_kinds: [],
      fact_atom_obligations: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
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
      design_repair_constraint_arity: {},
      design_review_states: [],
      binding_concern_codes: [],
      binding_infeasibility_codes: [],
      actions: [{
        ...blueprint,
        paid: true,
        free_flag: null,
        writes_disk: true,
      }],
      spoiler_disclosure: disclosure,
    })

    renderRoute(<GlobalScreen />, '/n/book-1/global')

    const continuation = await screen.findByRole('group', { name: '逐章骨架并行生成' })
    expect(continuation).toHaveTextContent('全局复核不再挡住逐章骨架')
    expect(continuation).toHaveTextContent('Stage4 会等当前全局复核的阻断面全部 PASS')
    expect(screen.getByRole('button', { name: '继续下一步' })).toBeVisible()
  })

  it('is present while stage2 is loading', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      hinge_kinds: [],
      fact_atom_kinds: [],
      fact_atom_obligations: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
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
      design_repair_constraint_arity: {},
      design_review_states: [],
      binding_concern_codes: [],
      binding_infeasibility_codes: [],
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
      hinge_kinds: [],
      fact_atom_kinds: [],
      fact_atom_obligations: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
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
      design_repair_constraint_arity: {},
      design_review_states: [],
      binding_concern_codes: [],
      binding_infeasibility_codes: [],
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
      hinge_kinds: [],
      fact_atom_kinds: [],
      fact_atom_obligations: [],
      nonbody_kinds: [],
      time_layers: [],
      hinge_groups: {},
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
      design_repair_constraint_arity: {},
      design_review_states: [],
      binding_concern_codes: [],
      binding_infeasibility_codes: [],
      actions: [],
      spoiler_disclosure: disclosure,
    })
    vi.spyOn(api, 'getGlobal').mockResolvedValue(globalView())

    renderRoute(<GlobalScreen />, '/n/book-1/global')
    expect(await screen.findByText('证据 ch3 · “古代证据”')).toBeVisible()
    expect(screen.getByText('证据定位失败 · 第 8 章')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /^关系/ }))
    expect(screen.getByText('随 secret_01 揭示')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /隐藏核心剧透字段（非完整遮蔽）/ }))
    fireEvent.click(await screen.findByRole('button', { name: /^秘密/ }))
    expect(await screen.findByText('在场众人（群体 · 第 2 章起）')).toBeVisible()
  })
})
