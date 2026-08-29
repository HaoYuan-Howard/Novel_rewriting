import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, realApi } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { renderRoute } from '@/test/render'
import type {
  ApprovalDoc,
  DesignReviewDecision,
  DesignReviewTemplate,
} from '@/types/approvals'
import { DesignReviewPanel } from './DesignReviewPanel'

const CANDIDATE = 'e'.repeat(64)
const AGGREGATE = 'd'.repeat(64)

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
    v: 5,
    sid: 'book-1',
    gate: { s0: true, s1: true, s2: true, s4: false, s5: false, s6: false, s7: false },
    seen: { read: true, global: true },
    writing: false,
    design_review: {
      state,
      candidate_sha256: CANDIDATE,
      aggregate_subject_sha256: AGGREGATE,
      current_decision: null,
      history: [],
      binding_infeasibility: null,
      repair_capability: repairCapability(),
    },
    structure: {
      state: 'unavailable',
      current_subject_sha256: null,
      current_decision: null,
      history: [],
      binding_concerns: [],
    },
    spawns: {},
  }
}

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

async function mount(state = 'missing') {
  const doc = approval(state)
  vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  const template = vi.spyOn(api, 'getDesignReviewTemplate')
    .mockImplementation((sid, query) => mockApi.getDesignReviewTemplate(sid, query))
  const approve = vi.spyOn(api, 'approveDesignReview')
  renderRoute(<DesignReviewPanel />, '/n/book-1/stage4/1')
  return { doc, template, approve }
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

describe('Stage4A 正式设计审阅', () => {
  it('用同一 human run id 读取关闭式模板，逐项写入真实反馈并显式确认后提交', async () => {
    const { template, approve, doc } = await mount()
    approve.mockImplementation(async (_sid, request) => {
      const decision: DesignReviewDecision = {
        ...request,
        decision_sha256: '9'.repeat(64),
        decided_at: '2026-08-28T12:00:00+00:00',
      }
      return {
        ...doc,
        design_review: {
          state: 'strategy_replan_requested',
          candidate_sha256: request.candidate_sha256,
          aggregate_subject_sha256: request.aggregate_subject_sha256,
          current_decision: decision,
          history: [decision],
          binding_infeasibility: request.binding_infeasibility,
          repair_capability: request.repair_capability,
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
    expect(screen.getByLabelText('第 3 章返修码 structure_delta')).toBeChecked()
    expect(screen.getAllByText('c03s01').length).toBeGreaterThan(0)
    expect(screen.getAllByText('主角在公开场合取得关键证据。').length).toBeGreaterThan(0)
    expect(screen.getByText('atomic-structure-v2')).toBeVisible()
    expect(screen.getByText('entry_anchor_changed')).toBeVisible()
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
      candidate_sha256: CANDIDATE,
      aggregate_subject_sha256: AGGREGATE,
      binding_infeasibility: null,
      strategy: {
        placeholder: false,
        template_token: null,
        decision: 'request_replan',
        reason: '策略未能给出可靠的全书重构次序，要求重规划',
        source_decision_sha256: null,
      },
      chapters: [{
        output_ordinal: 3,
        placeholder: false,
        template_token: null,
        decision: 'request_repair',
        reason: '本章入口锚点仍沿用原结构，需要定向返修',
        source_decision_sha256: null,
        constraints: [{ code: 'structure_delta', scene_refs: [], opaque_aliases: [] }],
      }],
    })
  })

  it('逐项接受后仍只呈现服务端 accepted 结论，B 最终计划票保持独立', async () => {
    const { approve, doc } = await mount('expired')
    approve.mockImplementation(async (_sid, request) => {
      const decision: DesignReviewDecision = {
        ...request,
        decision_sha256: '8'.repeat(64),
        decided_at: '2026-08-28T12:10:00+00:00',
      }
      return {
        ...doc,
        spawns: {
          s2: [{ kind: 'stage4.plan', label: 'Stage4B 证据绑定与计划编译', extra: null }],
        },
        design_review: {
          state: 'accepted',
          candidate_sha256: request.candidate_sha256,
          aggregate_subject_sha256: request.aggregate_subject_sha256,
          current_decision: decision,
          history: [decision],
          binding_infeasibility: request.binding_infeasibility,
          repair_capability: request.repair_capability,
        },
      }
    })

    const user = await startReview()
    expect(screen.getByText('历史审阅已过期')).toBeVisible()
    await user.selectOptions(screen.getByLabelText('Stage4A 整书策略裁决'), 'accept')
    await user.selectOptions(screen.getByLabelText('第 3 章 Stage4A 设计裁决'), 'accept')
    expect(screen.queryByLabelText('第 3 章返修码 structure_delta')).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('Stage4A 整书策略已核对'))
    await user.click(screen.getByLabelText('第 3 章 Stage4A 已核对'))

    await user.click(screen.getByRole('button', { name: /保存 Stage4A 正式审阅/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
    expect(approve.mock.calls[0]?.[1]).toMatchObject({
      strategy: { placeholder: false, template_token: null, decision: 'accept' },
      chapters: [{ placeholder: false, template_token: null, decision: 'accept', constraints: [] }],
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
    approve.mockImplementation(async (_sid, request) => ({
      ...doc,
      design_review: {
        state: 'accepted',
        candidate_sha256: request.candidate_sha256,
        aggregate_subject_sha256: request.aggregate_subject_sha256,
        current_decision: {
          ...request,
          decision_sha256: '3'.repeat(64),
          decided_at: '2026-08-28T12:20:00+00:00',
        },
        history: [],
        binding_infeasibility: request.binding_infeasibility,
        repair_capability: request.repair_capability,
      },
    }))

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
        placeholder: false,
        template_token: null,
        source_decision_sha256: null,
      },
      chapters: [{
        placeholder: false,
        template_token: null,
        source_decision_sha256: null,
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
      const request = JSON.parse(String(init?.body)) as DesignReviewTemplate
      const decision: DesignReviewDecision = {
        ...request,
        decision_sha256: '2'.repeat(64),
        decided_at: '2026-08-28T12:30:00+00:00',
      }
      return new Response(JSON.stringify({
        ...doc,
        design_review: {
          state: 'strategy_replan_requested',
          candidate_sha256: request.candidate_sha256,
          aggregate_subject_sha256: request.aggregate_subject_sha256,
          current_decision: decision,
          history: [decision],
          binding_infeasibility: request.binding_infeasibility,
          repair_capability: request.repair_capability,
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
      strategy: { placeholder: false, template_token: null },
      chapters: [{ placeholder: false, template_token: null }],
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
        closed_refs: ['a_' + '1'.repeat(16)],
      }],
      infeasibility_proof: [{
        chapter_ordinal: 3,
        scenes: ['c03s01'],
        constraint_code: 'empty_stage_allowlist',
        closed_refs: ['a_' + '1'.repeat(16)],
        proof_basis: '1'.repeat(64),
      }],
    }
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    renderRoute(<DesignReviewPanel />, '/n/book-1/stage4/1')

    expect(await screen.findByText('当前 Stage4B 不可行证明')).toBeVisible()
    expect(screen.getByText('empty_stage_allowlist')).toBeVisible()
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
})
