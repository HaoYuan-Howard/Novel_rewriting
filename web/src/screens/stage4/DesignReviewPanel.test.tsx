import { missingPrepareState } from '@/test/prepare'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, realApi } from '@/api/endpoints'
import { mockApi, plotFlowFixture } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { renderRoute } from '@/test/render'
import type {
  ApprovalDoc,
  DesignReviewDecision,
  DesignRepairConstraint,
  DesignReviewRequest,
  DesignReviewTemplate,
} from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import { DesignReviewPanel } from './DesignReviewPanel'

import type { PlannerRelation } from '@/types/plot.generated'

const SAMPLE_TRUTH = plotFlowFixture().design_template.chapters.find((c) => c.output_ordinal === 3)!.subject.blind_truth
const FIRST_BEAT = SAMPLE_TRUTH.beats[0].alias
const SECOND_BEAT = SAMPLE_TRUTH.beats[1].alias

const CANDIDATE = 'e'.repeat(64)
const AGGREGATE = 'd'.repeat(64)

function materializeDecision(
  request: DesignReviewRequest,
  template: DesignReviewTemplate,
  decisionSha: string,
): DesignReviewDecision {
  return {
    ...template,
    actor: request.actor,
    reviewer_kind: request.reviewer_kind,
    review_run_id: request.review_run_id,
    general_reason: request.general_reason,
    strategy: {
      ...template.strategy,
      placeholder: false,
      template_token: null,
      decision: request.strategy.decision,
      reason: request.strategy.reason,
      replan_constraints: request.strategy.replan_constraints.map((row) => ({
        ...row,
        subject_sha256: template.strategy.subject_sha256,
      })),
    },
    chapters: template.chapters.map((chapter) => {
      const ruling = request.chapters.find(
        (row) => row.output_ordinal === chapter.output_ordinal,
      )!
      return {
        ...chapter,
        placeholder: false,
        template_token: null,
        decision: ruling.decision,
        reason: ruling.reason,
        constraints: ruling.constraints,
        ...(ruling.constraint_operations ? { constraint_operations: ruling.constraint_operations } : {}),
      }
    }),
    decision_sha256: decisionSha,
    decided_at: '2026-08-28T12:00:00+00:00',
  }
}

function historyEntry(decision: DesignReviewDecision) {
  return {
    actor: decision.actor,
    reviewer_kind: decision.reviewer_kind,
    review_run_id: decision.review_run_id,
    general_reason: decision.general_reason,
    review_token: 's4dr1_' + AGGREGATE,
    strategy_decision: decision.strategy.decision,
    chapter_decisions: Object.fromEntries(
      decision.chapters.map((row) => [row.output_ordinal, row.decision]),
    ),
    decision_sha256: decision.decision_sha256,
    decided_at: decision.decided_at,
  }
}

function repairCapability({
  repairable = true,
  reasonCode = 'ready',
  committedRounds = 0,
  available = true,
} = {}) {
  return {
    repairable,
    reason_code: reasonCode,
    chapters: [{
      output_ordinal: 3,
      committed_rounds: committedRounds,
      request_repair_available: available,
    }],
  }
}

function approval(state = 'missing'): ApprovalDoc {
  return {
    v: 9, prepare: missingPrepareState(),
    sid: 'book-1',
    gate: { s0: true, s1: true, s4: false, s5: false, s6: false, s7: false },
    writing: false,
    design_review: {
      state,
      candidate_sha256: CANDIDATE,
      aggregate_subject_sha256: AGGREGATE,
      current_decision: null,
      history: [],
      binding_infeasibility: null,
      preflight: null,
      repair_capability: repairCapability(),
    },
    structure: {
      state: 'unavailable',
      current_subject_sha256: null,
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
    spawns: {},
  }
}

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

async function mount(state = 'missing', enumOverrides: Partial<EnumsResponse> = {}) {
  const doc = approval(state)
  vi.spyOn(api, 'getEnums').mockResolvedValue({ ...await mockApi.getEnums(), ...enumOverrides })
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  if (!vi.isMockFunction(api.getDesignApprovalWorkpack)) {
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue(
      await mockApi.getDesignApprovalWorkpack('book-1'),
    )
  }
  const template = vi.spyOn(api, 'getDesignReviewTemplate')
    .mockImplementation((sid, query) => mockApi.getDesignReviewTemplate(sid, query))
  const approve = vi.spyOn(api, 'approveDesignReview')
  const rendered = renderRoute(<DesignReviewPanel />, '/n/book-1/stage4/1')
  return { doc, template, approve, queryClient: rendered.queryClient }
}

async function startReview() {
  const user = userEvent.setup()
  await screen.findByText('Stage4A 正式设计审阅')
  await user.type(screen.getByLabelText('Stage4A 自报身份 actor'), 'reviewer-a')
  await user.type(
    screen.getByLabelText('Stage4A 本轮审阅总理由 reason'),
    '逐章核对盲证据与结构变化',
  )
  const start = screen.getByRole('button', { name: '读取关闭式审阅模板' })
  await waitFor(() => expect(start).toBeEnabled())
  await user.click(start)
  await screen.findByText('打散原章顺序，以因果揭示次序重组场景。')
  return user
}

const REVIEW_RELATIONS: PlannerRelation[] = (['cause', 'story_before', 'response_to', 'same_scene', 'observes'] as const).map((kind) => ({
  alias: 'a_' + 'f'.repeat(16), kind,
  before_beat_alias: FIRST_BEAT, after_beat_alias: SECOND_BEAT,
  observer_alias: kind === 'observes' ? SAMPLE_TRUTH.beats[0].participant_aliases[0] : null,
}))

function withActiveConstraint(template: DesignReviewTemplate): DesignReviewTemplate {
  return { ...template, constraint_catalog: {
    '3': { expected_constraint_set_sha256: 'b'.repeat(64), items: [{
      constraint_sha256: 'a'.repeat(64), source_decision_sha256s: ['c'.repeat(64)],
      constraint: { code: 'scene_goal', scene_refs: ['c03s01'], opaque_aliases: [FIRST_BEAT] },
    }] },
  } }
}

async function writeReviewReasons(user: ReturnType<typeof userEvent.setup>) {
  await user.clear(screen.getByLabelText('Stage4A 整书策略裁决理由'))
  await user.type(screen.getByLabelText('Stage4A 整书策略裁决理由'), '核对本轮策略与证据')
  await user.clear(screen.getByLabelText('第 3 章 Stage4A 裁决理由'))
  await user.type(screen.getByLabelText('第 3 章 Stage4A 裁决理由'), '根据本章证据明确填写返修要求')
  await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
}

describe('Stage4A 正式设计审阅', () => {

  it.each(['新增', '替代'])('%s场景约束可独立选择非 Beat 人物呈现并经 HTTP 原样提交', async (mode) => {
    const actors = [
      { alias: 'a_' + '1'.repeat(16), surfaces: ['来客'], role: null },
      { alias: 'a_' + '2'.repeat(16), surfaces: ['送信人'], role: null },
    ]
    const approveOverHttp = realApi.approveDesignReview.bind(realApi)
    const { template, approve, doc } = await mount('expired')
    template.mockImplementation(async (sid, query) => {
      const base = await mockApi.getDesignReviewTemplate(sid, query)
      const current = mode === '替代' ? withActiveConstraint(base) : base
      const truth = current.chapters[0].subject.blind_truth
      expect(truth.beats.flatMap((beat) => beat.participant_aliases)).not.toContain(actors[0].alias)
      expect(truth.beats.flatMap((beat) => beat.participant_aliases)).not.toContain(actors[1].alias)
      truth.characters.push(...structuredClone(actors))
      truth.states.push({ alias: 'a_' + '3'.repeat(16), content: '来客还相信信中所言。',
        modality: 'belief', holder_alias: actors[0].alias, global_subject_alias: null,
        established_at_entry: true, supersedes_state_aliases: [] })
      return current
    })
    approve.mockImplementation(approveOverHttp)
    const send = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(doc), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    const user = await startReview()
    await writeReviewReasons(user)
    if (mode === '替代') {
      await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'supersede')
      await user.type(screen.getByLabelText('第 3 章历史约束 1理由'), '保留两份公开呈现的不同场景职责')
    }
    await user.click(screen.getByLabelText(mode === '替代'
      ? '第 3 章历史约束 1替代码 scene_goal_exclude' : '第 3 章返修码 scene_goal_exclude'))
    await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude 场景 c03s01'))
    for (const actor of actors) {
      const option = screen.getByLabelText('第 3 章 scene_goal_exclude alias ' + actor.alias)
      expect(option.closest('label')).toHaveTextContent(actor.surfaces[0])
      await user.click(option)
    }
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    const save = screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
    expect(save).toBeEnabled()
    await user.click(save)
    await waitFor(() => expect(send.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
    const [url, init] = send.mock.calls.find(([, value]) => value?.method === 'POST')!
    expect(String(url)).toContain('/novels/book-1/approvals/design')
    const posted = JSON.parse(String(init?.body)) as DesignReviewRequest
    const constraints = mode === '替代'
      ? posted.chapters[0].constraint_operations!.items[0].replacements : posted.chapters[0].constraints
    expect(constraints.find((value) => value.code === 'scene_goal_exclude')).toEqual({
      code: 'scene_goal_exclude', scene_refs: ['c03s01'], opaque_aliases: actors.map((actor) => actor.alias),
    })
  })

  it.each(['新增', '替代'])('%s scene_goal_exclude 缺少任一引用时不能保存', async (mode) => {
    const { template, approve } = await mount('expired')
    if (mode === '替代') template.mockImplementation(async (sid, query) => withActiveConstraint(
      await mockApi.getDesignReviewTemplate(sid, query),
    ))
    const user = await startReview()
    await writeReviewReasons(user)
    if (mode === '替代') {
      await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'supersede')
      await user.type(screen.getByLabelText('第 3 章历史约束 1理由'), '明确替换旧方向')
    }
    await user.click(screen.getByLabelText(mode === '替代'
      ? '第 3 章历史约束 1替代码 scene_goal_exclude' : '第 3 章返修码 scene_goal_exclude'))
    // ActionButton 切换禁用态时会更换包装层，必须断言当前挂载按钮。
    const save = () => screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(save()).toBeDisabled()
    await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude 场景 c03s01'))
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(save()).toBeDisabled()
    await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude alias ' + FIRST_BEAT))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(screen.getByLabelText('第 3 章 scene_goal_exclude 场景 c03s01')).toBeChecked()
    expect(screen.getByLabelText('第 3 章 scene_goal_exclude alias ' + FIRST_BEAT)).toBeChecked()
    expect(save()).toBeInTheDocument()
    expect(save()).toBeEnabled()
    expect(approve).not.toHaveBeenCalled()
  })

  describe.each(['新增', '替代'])('%s语义约束引用', (mode) => {
    it.each(REVIEW_RELATIONS)('显式引用 $kind 后 HTTP POST 不复制或提升原关系', async (source) => {
      const approveOverHttp = realApi.approveDesignReview.bind(realApi)
      const { template, approve, doc } = await mount('expired')
      const original = structuredClone(source)
      template.mockImplementation(async (sid, query) => {
        const base = await mockApi.getDesignReviewTemplate(sid, query)
        const current = mode === '替代' ? withActiveConstraint(base) : base
        current.chapters[0].subject.blind_truth.constraints = [source]
        return current
      })
      approve.mockImplementation(approveOverHttp)
      const send = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(doc), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
      const user = await startReview()
      await writeReviewReasons(user)
      if (mode === '替代') {
        await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'supersede')
        await user.type(screen.getByLabelText('第 3 章历史约束 1理由'), '按已登记关系修正呈现安排')
      }
      await user.click(screen.getByLabelText(mode === '替代'
        ? '第 3 章历史约束 1替代码 semantic_constraint' : '第 3 章返修码 semantic_constraint'))
      await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
      expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()
      await user.click(screen.getByLabelText('第 3 章 semantic_constraint alias ' + source.alias))
      expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
      await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
      const save = screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
      expect(save).toBeEnabled()
      await user.click(save)
      await waitFor(() => expect(send.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
      const [url, init] = send.mock.calls.find(([, value]) => value?.method === 'POST')!
      expect(String(url)).toContain('/novels/book-1/approvals/design')
      const posted = JSON.parse(String(init?.body)) as DesignReviewRequest
      const constraints = mode === '替代'
        ? posted.chapters[0].constraint_operations!.items[0].replacements : posted.chapters[0].constraints
      expect(constraints.find((value) => value.code === 'semantic_constraint')).toEqual({
        code: 'semantic_constraint', scene_refs: [], opaque_aliases: [source.alias],
      })
      expect(source).toEqual(original)
    })
  })

  it('没有已登记语义约束或端点时，不允许现场补造关系', async () => {
    const { template } = await mount('expired')
    template.mockImplementation(async (sid, query) => {
      const base = withActiveConstraint(await mockApi.getDesignReviewTemplate(sid, query))
      base.chapters[0].subject.blind_truth.constraints = []
      base.chapters[0].subject.blind_truth.endpoints = []
      return base
    })
    const user = await startReview()
    expect(screen.getByLabelText('第 3 章返修码 semantic_constraint')).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'supersede')
    expect(screen.getByLabelText('第 3 章历史约束 1替代码 semantic_constraint')).toBeDisabled()
    expect(screen.getByText('本章没有可引用的语义约束或端点，相关替换保持禁用。')).toBeVisible()
  })

  it.each(['复制effect', '未知引用'])('%s不能因勾选确认而提交', async (failure) => {
    const { template, approve } = await mount('expired')
    template.mockImplementation(async (sid, query) => {
      const base = await mockApi.getDesignReviewTemplate(sid, query)
      base.chapters[0].subject.blind_truth.constraints = REVIEW_RELATIONS.slice(0, 1)
      base.chapters[0].constraints = [{
        code: 'semantic_constraint', scene_refs: [],
        opaque_aliases: [failure === '未知引用' ? 'a_' + '0'.repeat(16) : REVIEW_RELATIONS[0].alias],
        ...(failure === '复制effect' ? { effect: REVIEW_RELATIONS[0] } : {}),
      } as DesignRepairConstraint]
      return base
    })
    const user = await startReview()
    await writeReviewReasons(user)
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()
    expect(approve).not.toHaveBeenCalled()
  })

  it.each(['未知code', '缺arity'])('%s不能因引用非空而保存', async (failure) => {
    const { template, approve } = await mount('expired', failure === '缺arity'
      ? { design_repair_constraint_arity: {} } : {})
    template.mockImplementation(async (sid, query) => {
      const base = await mockApi.getDesignReviewTemplate(sid, query)
      if (failure === '未知code') base.chapters[0].constraints = [{
        code: 'unknown_constraint', scene_refs: ['c03s01'], opaque_aliases: [FIRST_BEAT],
      }]
      return base
    })
    const user = await startReview()
    await writeReviewReasons(user)
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()
    expect(approve).not.toHaveBeenCalled()
  })

  it('supersede后切换accept仍不能保存，显式改为withdraw并重新确认才可提交', async () => {
    const { template, approve, doc } = await mount('expired')
    template.mockImplementation(async (sid, query) => withActiveConstraint(
      await mockApi.getDesignReviewTemplate(sid, query),
    ))
    approve.mockResolvedValue(doc)
    const user = await startReview()
    await writeReviewReasons(user)
    await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'supersede')
    await user.type(screen.getByLabelText('第 3 章历史约束 1理由'), '明确替换旧方向')
    await user.click(screen.getByLabelText('第 3 章历史约束 1替代码 scene_goal_exclude'))
    await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude 场景 c03s01'))
    await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude alias ' + FIRST_BEAT))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    // 每次读当前按钮；禁用与启用之间旧 DOM 节点已经卸载。
    const save = () => screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
    expect(save()).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('第 3 章 Stage4A 设计裁决'), 'accept')
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(screen.getByLabelText('第 3 章 Stage4A 设计裁决')).toHaveValue('accept')
    expect(screen.getByLabelText('第 3 章历史约束 1处置')).toHaveValue('supersede')
    expect(save()).toBeInTheDocument()
    expect(save()).toBeDisabled()
    await user.click(save())
    expect(approve).not.toHaveBeenCalled()
    await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), 'withdraw')
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(save()).toBeEnabled()
    await user.click(save())
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve.mock.calls[0][1].chapters[0]).toMatchObject({
      decision: 'accept', constraints: [],
      constraint_operations: { items: [{ kind: 'withdraw', replacements: [] }] },
    })
  })

  it('mock与正式schema一致拒绝accept携带supersede', async () => {
    const template = await mockApi.getDesignReviewTemplate('book-1', {
      actor: 'reviewer-a', reviewer_kind: 'human', review_run_id: 'review-operations', reason: '明确逐章审阅',
    })
    const request: DesignReviewRequest = {
      actor: template.actor, reviewer_kind: template.reviewer_kind, review_run_id: template.review_run_id,
      general_reason: '明确逐章审阅', review_token: template.review_token,
      strategy: { decision: 'accept', reason: '策略已核对', replan_constraints: [] },
      chapters: template.chapters.map((chapter) => ({
        output_ordinal: chapter.output_ordinal, decision: 'accept', reason: '本章已核对', constraints: [],
        constraint_operations: { expected_constraint_set_sha256: 'b'.repeat(64), items: [{
          kind: 'supersede', constraint_sha256: 'a'.repeat(64), reason: '仍要求替代修复',
          replacements: [{ code: 'scene_goal_exclude', scene_refs: ['c03s01'], opaque_aliases: [FIRST_BEAT] }],
        }] },
      })),
    }
    await expect(mockApi.approveDesignReview('book-1', request)).rejects.toThrow()
  })

  it.each(['keep', 'withdraw', 'supersede'])('历史约束必须显式选择%s并原样提交来源绑定', async (kind) => {
    const { template, approve, doc } = await mount('expired')
    const sourceSha = 'a'.repeat(64)
    const setSha = 'b'.repeat(64)
    template.mockImplementation(async (sid, query) => ({
      ...await mockApi.getDesignReviewTemplate(sid, query),
      constraint_catalog: {
        '3': {
          expected_constraint_set_sha256: setSha,
          items: [{
            constraint_sha256: sourceSha,
            constraint: {
              code: 'scene_goal', scene_refs: ['c03s01'], opaque_aliases: [FIRST_BEAT],
            },
            source_decision_sha256s: ['c'.repeat(64)],
          }],
        },
      },
    }))
    approve.mockResolvedValue(doc)
    const user = await startReview()
    expect(screen.getByLabelText('第 3 章历史约束 1处置')).toHaveValue('')
    await user.clear(screen.getByLabelText('Stage4A 整书策略裁决理由'))
    await user.type(screen.getByLabelText('Stage4A 整书策略裁决理由'), '核对本轮策略')
    await user.clear(screen.getByLabelText('第 3 章 Stage4A 裁决理由'))
    await user.type(screen.getByLabelText('第 3 章 Stage4A 裁决理由'), '根据当前场景核对历史指令')
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('第 3 章历史约束 1处置'), kind)
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    await user.type(screen.getByLabelText('第 3 章历史约束 1理由'), '基于当前候选明确更正历史约束')
    if (kind === 'supersede') {
      await user.click(screen.getByLabelText('第 3 章历史约束 1替代码 scene_goal_exclude'))
      await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude 场景 c03s01'))
      await user.click(screen.getByLabelText('第 3 章 scene_goal_exclude alias ' + FIRST_BEAT))
    }
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    const save = screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve.mock.calls[0][1].chapters[0].constraint_operations).toEqual({
      expected_constraint_set_sha256: setSha,
      items: [{
        kind, constraint_sha256: sourceSha, reason: '基于当前候选明确更正历史约束',
        replacements: kind === 'supersede' ? [{
          code: 'scene_goal_exclude', scene_refs: ['c03s01'], opaque_aliases: [FIRST_BEAT],
        }] : [],
      }],
    })
  })

  it('用同一 human run id 读取关闭式模板，逐项写入真实反馈并显式确认后提交', async () => {
    const { template, approve, doc } = await mount()
    approve.mockImplementation(async (_sid, request) => {
      const materialized = await mockApi.getDesignReviewTemplate(_sid, {
        actor: request.actor,
        reviewer_kind: request.reviewer_kind,
        review_run_id: request.review_run_id,
        reason: request.general_reason,
      })
      const decision = materializeDecision(request, materialized, '9'.repeat(64))
      return {
        ...doc,
        design_review: {
          state: 'strategy_replan_requested',
          candidate_sha256: materialized.candidate_sha256,
          aggregate_subject_sha256: materialized.aggregate_subject_sha256,
          current_decision: decision,
          history: [historyEntry(decision)],
          binding_infeasibility: materialized.binding_infeasibility,
          preflight: materialized.preflight,
          repair_capability: materialized.repair_capability,
        },
      }
    })

    const user = await startReview()
    expect(template).toHaveBeenCalledTimes(1)
    const query = template.mock.calls[0]?.[1]
    expect(query).toMatchObject({
      actor: 'reviewer-a',
      reviewer_kind: 'human',
      reason: '逐章核对盲证据与结构变化',
    })
    expect(query?.review_run_id).toMatch(/^[0-9a-f-]+$/)

    expect(screen.getByLabelText('Stage4A 整书策略裁决')).toHaveValue('request_replan')
    expect(screen.getByLabelText('第 3 章 Stage4A 设计裁决')).toHaveValue('request_repair')
    expect(screen.getByLabelText('第 3 章返修码 entry_anchor')).toBeChecked()
    expect(screen.getAllByText('c03s01').length).toBeGreaterThan(0)
    expect(screen.getAllByText(SAMPLE_TRUTH.beats[0].content).length).toBeGreaterThan(0)
    expect(screen.getByText('plot-presentation-structure-v1')).toBeVisible()
    expect(screen.getAllByText(/entry_anchor/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('ready').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('第 3 章定向返修能力')).toHaveTextContent(
      '服务端判定：本章 request_repair 可用',
    )
    expect(screen.queryByRole('button', { name: /一键|全收/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/可进入 Stage4B/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Stage4A 整书策略已核对')).not.toBeChecked()
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()

    await user.clear(screen.getByLabelText('Stage4A 整书策略裁决理由'))
    await user.type(
      screen.getByLabelText('Stage4A 整书策略裁决理由'),
      '策略未能给出可靠的全书重构次序，要求重规划',
    )
    await user.clear(screen.getByLabelText('第 3 章 Stage4A 裁决理由'))
    await user.type(
      screen.getByLabelText('第 3 章 Stage4A 裁决理由'),
      '本章入口锚点仍沿用原结构，需要定向返修',
    )
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))

    await user.click(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    const request = approve.mock.calls[0]?.[1]
    expect(request).toMatchObject({
      actor: 'reviewer-a',
      reviewer_kind: 'human',
      review_run_id: query?.review_run_id,
      general_reason: '逐章核对盲证据与结构变化',
      review_token: 's4dr1_' + AGGREGATE,
      strategy: {
        decision: 'request_replan',
        reason: '策略未能给出可靠的全书重构次序，要求重规划',
      },
      chapters: [{
        output_ordinal: 3,
        decision: 'request_repair',
        reason: '本章入口锚点仍沿用原结构，需要定向返修',
        constraints: [{ code: 'entry_anchor', scene_refs: [], opaque_aliases: [FIRST_BEAT] }],
      }],
    })
  })

  it('逐项接受后仍只呈现服务端 accepted 结论，B 最终计划票保持独立', async () => {
    const { approve, doc } = await mount('expired')
    approve.mockImplementation(async (_sid, request) => {
      const materialized = await mockApi.getDesignReviewTemplate(_sid, {
        actor: request.actor,
        reviewer_kind: request.reviewer_kind,
        review_run_id: request.review_run_id,
        reason: request.general_reason,
      })
      const decision = materializeDecision(request, materialized, '8'.repeat(64))
      return {
        ...doc,
        spawns: {
          s4: [{ kind: 'stage4.plan', label: 'Stage4B 证据绑定与计划编译', extra: null }],
        },
        design_review: {
          state: 'accepted',
          candidate_sha256: materialized.candidate_sha256,
          aggregate_subject_sha256: materialized.aggregate_subject_sha256,
          current_decision: decision,
          history: [historyEntry(decision)],
          binding_infeasibility: materialized.binding_infeasibility,
          preflight: materialized.preflight,
          repair_capability: materialized.repair_capability,
        },
      }
    })

    const user = await startReview()
    expect(screen.getByText('历史审阅已过期')).toBeVisible()
    await user.selectOptions(screen.getByLabelText('Stage4A 整书策略裁决'), 'accept')
    await user.selectOptions(screen.getByLabelText('第 3 章 Stage4A 设计裁决'), 'accept')
    expect(screen.queryByLabelText('第 3 章返修码 entry_anchor')).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))

    await user.click(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve.mock.calls[0]?.[1]).toMatchObject({
      strategy: { decision: 'accept' },
      chapters: [{ decision: 'accept', constraints: [] }],
    })
    expect(await screen.findByText(/服务端判定：当前 A 已接受，可进入 Stage4B/)).toBeVisible()
    expect(screen.getByText(/最终计划仍须独立审批/)).toBeVisible()
    expect(screen.getByRole('button', { name: /Stage4B 证据绑定与计划编译/ })).toBeVisible()
  })

  it('同主体历史 accept 可保持确认；修改任一已确认行会清来源并恢复未核对', async () => {
    const { template, approve, doc } = await mount()
    const fresh = await mockApi.getDesignReviewTemplate('history-seed', {
      actor: 'reviewer-a',
      reviewer_kind: 'human',
      review_run_id: 'history-run',
      reason: '逐章核对盲证据与结构变化',
    })
    const source = '4'.repeat(64)
    const reused: DesignReviewTemplate = {
      ...fresh,
      strategy: {
        ...fresh.strategy,
        placeholder: false,
        template_token: null,
        decision: 'accept',
        reason: '沿用仍绑定当前策略主体的历史 accept',
        source_decision_sha256: source,
      },
      chapters: fresh.chapters.map((chapter) => ({
        ...chapter,
        placeholder: false,
        template_token: null,
        decision: 'accept',
        reason: '沿用仍绑定当前章主体的历史 accept',
        constraints: [],
        source_decision_sha256: source,
      })),
    }
    template.mockResolvedValue(reused)
    approve.mockImplementation(async (_sid, request) => {
      const decision = materializeDecision(request, reused, '3'.repeat(64))
      return {
        ...doc,
        design_review: {
          state: 'accepted',
          candidate_sha256: reused.candidate_sha256,
          aggregate_subject_sha256: reused.aggregate_subject_sha256,
          current_decision: decision,
          history: [historyEntry(decision)],
          binding_infeasibility: reused.binding_infeasibility,
          preflight: reused.preflight,
          repair_capability: reused.repair_capability,
        },
      }
    })

    const user = await startReview()
    expect(screen.getByLabelText('Stage4A 整书策略已核对')).toBeChecked()
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).toBeChecked()
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeEnabled()

    await user.type(screen.getByLabelText('Stage4A 整书策略裁决理由'), '（本轮复核）')
    await user.type(screen.getByLabelText('第 3 章 Stage4A 裁决理由'), '（本轮复核）')
    expect(screen.getByLabelText('Stage4A 整书策略已核对')).not.toBeChecked()
    expect(screen.getByLabelText('第 3 章 Stage4A 已核对')).not.toBeChecked()
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()

    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    await user.click(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve.mock.calls[0]?.[1]).toMatchObject({
      strategy: {
        decision: 'accept',
      },
      chapters: [{
        decision: 'accept',
      }],
    })
  })

  it('首次模板从界面经 hook 走真实 HTTP 缝，POST 只发送已显式确认的反馈', async () => {
    const doc = approval()
    const seed = await mockApi.getDesignReviewTemplate('http-seed', {
      actor: 'seed',
      reviewer_kind: 'human',
      review_run_id: 'seed-run',
      reason: 'seed',
    })
    // 测试环境可能让 `api === realApi`；先捕获原始函数，避免给唯一 API 缝加 spy 后递归。
    const getTemplateOverHttp = realApi.getDesignReviewTemplate.bind(realApi)
    const approveOverHttp = realApi.approveDesignReview.bind(realApi)
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue(
      await mockApi.getDesignApprovalWorkpack('book-1'),
    )
    vi.spyOn(api, 'getDesignReviewTemplate')
      .mockImplementation((sid, query) => getTemplateOverHttp(sid, query))
    vi.spyOn(api, 'approveDesignReview')
      .mockImplementation((sid, request) => approveOverHttp(sid, request))
    const send = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = new URL(String(input), 'http://test.local')
      if ((init?.method ?? 'GET') === 'GET') {
        const request: DesignReviewTemplate = {
          ...seed,
          actor: url.searchParams.get('actor') ?? '',
          reviewer_kind: url.searchParams.get('reviewer_kind') ?? '',
          review_run_id: url.searchParams.get('review_run_id') ?? '',
          general_reason: url.searchParams.get('reason') ?? '',
        }
        return new Response(JSON.stringify(request), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const request = JSON.parse(String(init?.body)) as DesignReviewRequest
      const decision = materializeDecision(request, seed, '2'.repeat(64))
      return new Response(JSON.stringify({
        ...doc,
        design_review: {
          state: 'strategy_replan_requested',
          candidate_sha256: seed.candidate_sha256,
          aggregate_subject_sha256: seed.aggregate_subject_sha256,
          current_decision: decision,
          history: [historyEntry(decision)],
          binding_infeasibility: seed.binding_infeasibility,
          preflight: seed.preflight,
          repair_capability: seed.repair_capability,
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    renderRoute(<DesignReviewPanel />, '/n/book-1/stage4/1')
    const user = await startReview()
    await user.clear(screen.getByLabelText('Stage4A 整书策略裁决理由'))
    await user.type(screen.getByLabelText('Stage4A 整书策略裁决理由'), '策略证据不足，要求重规划')
    await user.clear(screen.getByLabelText('第 3 章 Stage4A 裁决理由'))
    await user.type(screen.getByLabelText('第 3 章 Stage4A 裁决理由'), '本章结构变化不足，要求返修')
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    await user.click(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }))

    await waitFor(() => expect(
      send.mock.calls.some(([, init]) => init?.method === 'POST'),
    ).toBe(true))
    const [postUrl, postInit] = send.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(postUrl).toBe('/api/novels/book-1/approvals/design')
    expect(JSON.parse(String(postInit?.body))).toMatchObject({
      review_token: 's4dr1_' + AGGREGATE,
      strategy: { decision: 'request_replan' },
      chapters: [{ decision: 'request_repair' }],
    })
  })

  it('只展示服务端投影为 current/fresh 的 Stage4B 不可行证明', async () => {
    const doc = approval()
    doc.design_review.binding_infeasibility = {
      candidate_sha256: CANDIDATE,
      binding_concerns: [{
        constraint_code: 'material_conflict',
        chapter_ordinal: 3,
        scenes: ['c03s01'],
        reason: '模型担忧仅作非阻断证据',
        closed_refs: [FIRST_BEAT],
      }],
      infeasibility_proof: [{
        chapter_ordinal: 3,
        scenes: ['c03s01'],
        constraint_code: 'missing_chapter_catalog',
        closed_refs: [FIRST_BEAT],
        proof_basis: '1'.repeat(64),
      }],
    }
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    renderRoute(<DesignReviewPanel />, '/n/book-1/stage4/1')

    expect(await screen.findByText('当前 Stage4B 不可行证明')).toBeVisible()
    expect(screen.getByText('missing_chapter_catalog')).toBeVisible()
    expect(screen.getByText(/模型担忧仅作非阻断证据/)).toBeVisible()
    expect(screen.getByText(/模型 concern 不会被前端升级/)).toBeVisible()
  })

  it('按服务端逐章能力禁用 request_repair，并明确展示已提交轮次与耗尽结论', async () => {
    const { template } = await mount()
    const seed = await mockApi.getDesignReviewTemplate('exhausted-seed', {
      actor: 'reviewer-a',
      reviewer_kind: 'human',
      review_run_id: 'exhausted-run',
      reason: '逐章核对盲证据与结构变化',
    })
    template.mockResolvedValue({
      ...seed,
      repair_capability: repairCapability({ committedRounds: 2, available: false }),
      chapters: seed.chapters.map((chapter) => ({
        ...chapter,
        decision: 'accept',
        reason: '关闭式模板：当前章不能执行 targeted repair，须确认接受或改走策略 replan',
        constraints: [],
      })),
    })

    await startReview()
    expect(screen.getByLabelText('第 3 章 Stage4A 设计裁决')).toHaveValue('accept')
    expect(screen.getByRole('option', { name: 'request_repair' })).toBeDisabled()
    expect(screen.getByLabelText('第 3 章定向返修能力')).toHaveTextContent(
      '服务端判定：本章返修轮次已耗尽，request_repair 不可再选',
    )
    expect(screen.getByLabelText('第 3 章定向返修能力')).toHaveTextContent(
      'committed_rounds 2',
    )
  })

  it('工作包仍在读取时关闭式禁用模板入口，不允许先点后补绑定', async () => {
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockImplementation(
      () => new Promise(() => undefined),
    )
    await mount()
    const user = userEvent.setup()
    await screen.findByText('Stage4A 正式设计审阅')
    await user.type(screen.getByLabelText('Stage4A 自报身份 actor'), 'reviewer-a')
    await user.type(
      screen.getByLabelText('Stage4A 本轮审阅总理由 reason'),
      '逐章核对当前证据',
    )

    const start = screen.getByRole('button', { name: '读取关闭式审阅模板' })
    expect(start).toBeDisabled()
    expect(start.parentElement).toHaveAttribute('title', '正在读取或刷新当前 Stage4A 工作包')
  })

  it('同一当前 Stage4A 工作包已批准时默认跳过重复整读', async () => {
    const pack = await mockApi.getDesignApprovalWorkpack('book-1')
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue({
      ...pack,
      disposition: 'already_current',
      current_decision_sha256: '9'.repeat(64),
    })
    await mount()

    const start = await screen.findByRole('button', { name: '读取关闭式审阅模板' })
    expect(start).toBeDisabled()
    expect(start.parentElement).toHaveAttribute(
      'title',
      '同一当前 Stage4A 工作包已有正式接受决定，无需重复整读',
    )
    expect(screen.getByText(/默认跳过重复人工整读/)).toBeVisible()
  })


  it('模板已读后后台刷新工作包时禁用最终保存，刷新失败后也不复开', async () => {
    const pack = await mockApi.getDesignApprovalWorkpack('book-1')
    const getWorkpack = vi.spyOn(api, 'getDesignApprovalWorkpack')
      .mockResolvedValue(pack)
    const { queryClient } = await mount()
    const user = await startReview()
    await user.clear(screen.getByLabelText('Stage4A 整书策略裁决理由'))
    await user.type(
      screen.getByLabelText('Stage4A 整书策略裁决理由'),
      '策略证据不足，要求重规划',
    )
    await user.clear(screen.getByLabelText('第 3 章 Stage4A 裁决理由'))
    await user.type(
      screen.getByLabelText('第 3 章 Stage4A 裁决理由'),
      '入口锚点需要定向返修',
    )
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))
    const save = screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })
    await waitFor(() => expect(save).toBeEnabled())

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    const callsBeforeRefresh = getWorkpack.mock.calls.length
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.approvalWorkpack('book-1', 'design'),
        type: 'active',
      })
    })
    await waitFor(() => expect(getWorkpack).toHaveBeenCalledTimes(callsBeforeRefresh + 1))
    await waitFor(() => expect(
      screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }),
    ).toBeDisabled())
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }).parentElement)
      .toHaveAttribute('title', '正在读取或刷新当前 Stage4A 工作包')
    await act(async () => rejectRefresh(new Error('refresh failed')))
    await waitFor(() => expect(
      screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }).parentElement,
    ).toHaveAttribute(
      'title',
      '当前 Stage4A 工作包读取失败，不能绑定本轮审阅',
    ))
    expect(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ })).toBeDisabled()
  })
})
