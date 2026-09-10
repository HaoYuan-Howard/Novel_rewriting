import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { qk } from '@/api/queryKeys'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { renderRoute } from '@/test/render'
import { useChrome } from '@/hooks/useChrome'
import type { ApprovalDoc } from '@/types/approvals'
import type { Stage4IndexRow } from '@/types/stages'
import { StructureApprovalPanel } from './StructureApprovalPanel'

const SUBJECT = 'a'.repeat(64)

async function currentWorkpack() {
  const pack = await mockApi.getStructureApprovalWorkpack('book-1')
  return { ...pack, subject_sha256: SUBJECT, review_token: 's4sr1_' + SUBJECT }
}

function NoticeReadout() {
  const { notices } = useChrome()
  return <output data-testid="structure-notices">{notices.length}</output>
}

async function finishNotices() {
  // 成功回包还会展示 3 秒通知；等真实消费者归零再结束，避免环境销毁后遗留回调。
  await waitFor(() => expect(screen.getByTestId('structure-notices')).not.toHaveTextContent('0'))
  await waitFor(() => expect(screen.getByTestId('structure-notices')).toHaveTextContent('0'), { timeout: 4000 })
}

const rows: Stage4IndexRow[] = [
  {
    output_ordinal: 1,
    chapter_goal: '开场建立冲突',
    structure_decision: {
      mode: 'retain',
      reason: '本章线性因果很短，保留入口更清楚。',
      declared_change_kinds: [],
      computed_change_kinds: [],
    },
    budget_chars: 500,
    scenes: 1,
  },
  {
    output_ordinal: 2,
    chapter_goal: '重组信息揭示',
    structure_decision: {
      mode: 'reconstruct',
      reason: '改从结果切入，并把非相邻事实组合成新场。',
      declared_change_kinds: ['entry_anchor', 'noncontiguous_regroup'],
      computed_change_kinds: ['entry_anchor', 'noncontiguous_regroup'],
    },
    budget_chars: 800,
    scenes: 2,
  },
]

function undecided(): ApprovalDoc {
  return {
    v: 6,
    sid: 'book-1',
    gate: {
      s0: true, s1: true, s2: true,
      s4: false, s5: false, s6: false, s7: false,
    },
    seen: { read: true, global: true },
    writing: false,
    design_review: {
      state: 'accepted', candidate_sha256: 'c'.repeat(64),
      aggregate_subject_sha256: 'd'.repeat(64), current_decision: null, history: [],
      binding_infeasibility: null,
      preflight: null,
      repair_capability: {
        repairable: true, reason_code: 'ready',
        chapters: [{ output_ordinal: 3, committed_rounds: 0, request_repair_available: true }],
      },
    },
    structure: {
      state: 'missing',
      current_subject_sha256: SUBJECT,
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

beforeEach(async () => {
  vi.restoreAllMocks()
  sessionStorage.clear()
  vi.spyOn(api, 'getStructureApprovalWorkpack').mockResolvedValue(
    await currentWorkpack(),
  )
})

describe('Stage4 typed 结构审批', () => {
  it('保留后端继承行的原理由和来源，只对待审章填写新决定', async () => {
    const pack = await currentWorkpack()
    const inherited = {
      output_ordinal: 1, decision: 'accept', reason: ' 原决定理由 ',
      source_decision_sha256: '7'.repeat(64),
    }
    vi.spyOn(api, 'getStructureApprovalWorkpack').mockResolvedValue({
      ...pack,
      groups: [{ group: 'coverage', scanned: '逐章义务', items: [{
        code: 'chapter_coverage', rule: null, chapter_ordinal: 1,
        scene_refs: [], refs: [], repair_owner: null, detail: '本章依赖未变化',
        effect: 'coverage', allowed_actions: ['accept'], changed: false,
        historical_accept_reusable: true, source_decision_sha256: inherited.source_decision_sha256,
        inherited_structure_decision: inherited,
      }] }],
    })
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(undecided())
    // 不制造成功通知；本用例检查的真实边界是 typed 请求内容。
    const approve = vi.spyOn(api, 'approveStructure').mockRejectedValue(new Error('请求已捕获'))
    renderRoute(<StructureApprovalPanel rows={rows} identity={false} viewSubject={SUBJECT}
      onRefreshView={vi.fn()} />, '/n/book-1/stage4/1')
    expect(await screen.findByLabelText('第 1 章裁决理由')).toHaveValue(inherited.reason)
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('第 2 章结构裁决'), 'accept')
    await user.type(screen.getByLabelText('第 2 章裁决理由'), '新证据已核对')
    await user.type(screen.getByLabelText('自报身份 actor'), 'reviewer-a')
    await user.type(screen.getByLabelText('本次批准 / 编辑理由 reason'), '复核变化章')
    await user.click(screen.getByRole('button', { name: '保存全书结构裁决' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith('book-1', {
      actor: 'reviewer-a', reason: '复核变化章', review_token: 's4sr1_' + SUBJECT,
      chapters: [inherited, { output_ordinal: 2, decision: 'accept', reason: '新证据已核对' }],
    }))
    await screen.findByText(/请求已捕获/)
    await user.type(screen.getByLabelText('第 1 章裁决理由'), '补充本轮判断')
    await user.click(screen.getByRole('button', { name: '保存全书结构裁决' }))
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(2))
    expect(approve.mock.calls[1][1].chapters[0]).not.toHaveProperty('source_decision_sha256')
  })

  it('工作包与审批主体错位时禁用继承提交', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(undecided())
    vi.spyOn(api, 'getStructureApprovalWorkpack').mockResolvedValue({
      ...await currentWorkpack(), subject_sha256: 'b'.repeat(64),
    })
    renderRoute(<StructureApprovalPanel rows={rows} identity={false} viewSubject={SUBJECT}
      onRefreshView={vi.fn()} />, '/n/book-1/stage4/1')
    const save = await screen.findByRole('button', { name: '保存全书结构裁决' })
    await waitFor(() => expect(save.parentElement).toHaveAttribute(
      'title', '结构工作包与页面当前主体不一致，请刷新后再提交'))
    expect(save).toBeDisabled()
  })

  it('A 不是服务端 current accepted 时不呈现 B 审批表单', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...undecided(),
      design_review: {
        state: 'expired', candidate_sha256: 'c'.repeat(64),
        aggregate_subject_sha256: 'd'.repeat(64), current_decision: null, history: [],
        binding_infeasibility: null,
        preflight: null,
        repair_capability: {
          repairable: true, reason_code: 'ready',
          chapters: [{ output_ordinal: 3, committed_rounds: 0, request_repair_available: true }],
        },
      },
    })
    const approve = vi.spyOn(api, 'approveStructure')

    renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      />,
      '/n/book-1/stage4/1',
    )

    expect(await screen.findByText('Stage4B 最终计划审批正在等待 A')).toBeVisible()
    expect(screen.getByText(/expired/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '保存全书结构裁决' })).not.toBeInTheDocument()
    expect(approve).not.toHaveBeenCalled()
  })

  it('逐条展示 B 绑定关注项，但不按数量自动生成结构裁决', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...undecided(),
      structure: {
        ...undecided().structure,
        binding_concerns: [{
          constraint_code: 'material_conflict',
          chapter_ordinal: 2,
          scenes: ['c02s01', 'c02s03'],
          reason: '两场依赖同一闭合事实，需要人确认不会重复揭示。',
          closed_refs: ['a_1234567890abcdef'],
        }],
      },
    })
    const approve = vi.spyOn(api, 'approveStructure')

    renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      />,
      '/n/book-1/stage4/1',
    )

    expect(await screen.findByText('Stage4B 绑定关注项')).toBeVisible()
    expect(screen.getByText('非阻断但需人审；数量不会自动改变逐章裁决')).toBeVisible()
    expect(screen.getByText('material_conflict')).toBeVisible()
    expect(screen.getByText('场景：c02s01、c02s03')).toBeVisible()
    expect(screen.getByText('两场依赖同一闭合事实，需要人确认不会重复揭示。')).toBeVisible()
    expect(screen.getByText('closed_refs：a_1234567890abcdef')).toBeVisible()
    expect(screen.getByLabelText('第 1 章结构裁决')).toHaveValue('')
    expect(approve).not.toHaveBeenCalled()
  })

  it('展示完整 subject 与逐章计划决定，并原子提交服务端枚举给出的全章裁决', async () => {
    const doc = undecided()
    const enums = await mockApi.getEnums()
    // 使用非生产字面值守住这条数据缝：选项必须来自响应，组件不能藏一份本地枚举。
    const liveDecisions = ['live-accept', 'live-request-replan']
    const replan = {
      kind: 'stage4.design',
      label: '按结构裁决重新生成 Stage4A 盲候选',
      extra: ['--replan'],
    }
    vi.spyOn(api, 'getEnums').mockResolvedValue({
      ...enums,
      structure_approval_decisions: liveDecisions,
      actions: [
        ...enums.actions.filter((action) => !(
          action.kind === replan.kind
          && JSON.stringify(action.extra ?? []) === JSON.stringify(replan.extra)
        )),
        {
          kind: replan.kind,
          label: replan.label,
          extra: replan.extra,
          paid: true,
          free_flag: null,
          writes: 'stage4/blind_candidate.json',
          writes_disk: true,
          needs_value: null,
          required_inputs: [],
        },
      ],
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
    const approve = vi.spyOn(api, 'approveStructure').mockImplementation(async (_sid, request) => {
      const decided = { ...request, decided_at: '2026-08-25T12:00:00+00:00' }
      return {
        ...doc,
        structure: {
          state: 'replan_requested',
          current_subject_sha256: SUBJECT,
          current_decision: decided,
          history: [decided],
          binding_concerns: [],
        },
        spawns: { s4: [replan] },
      }
    })
    const writing = vi.spyOn(api, 'startWriting')
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const argv = 'python -m novel.cli run book-1 stage4.design --replan --yes'
    const preview = vi.spyOn(api, 'previewJob').mockResolvedValue({
      argv,
      paid: true,
      label: replan.label,
      kind: replan.kind,
      steps: [{
        label: replan.label,
        paid: true,
        writes: 'stage4/blind_candidate.json',
        argv,
      }],
    })
    const job = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'replan-job', kind: replan.kind, label: replan.label, argv,
      state: 'running', paid: true, started: 1, finished: null,
      sid: 'book-1', ordinal: null,
    })

    renderRoute(
      <><StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      /><ConfirmDialog /><NoticeReadout /></>,
      '/n/book-1/stage4/1',
    )

    expect((await screen.findAllByText(SUBJECT))[0]).toBeVisible()
    expect(screen.getByText('retain')).toBeVisible()
    expect(screen.getByText('reconstruct')).toBeVisible()
    expect(screen.getAllByText('entry_anchor').length).toBeGreaterThan(0)

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('第 1 章结构裁决'), liveDecisions[0])
    await user.type(screen.getByLabelText('第 1 章裁决理由'), '线性章保留合理')
    await user.selectOptions(screen.getByLabelText('第 2 章结构裁决'), liveDecisions[1])
    await user.type(screen.getByLabelText('第 2 章裁决理由'), '复算变化仍不足，需要重规划')
    await user.type(screen.getByLabelText('自报身份 actor'), 'reviewer-a')
    await user.type(screen.getByLabelText('本次批准 / 编辑理由 reason'), '已逐章核对结构报告')
    await user.click(screen.getByRole('button', { name: '保存全书结构裁决' }))

    await waitFor(() => expect(approve).toHaveBeenCalledWith('book-1', {
      review_token: 's4sr1_' + SUBJECT,
      actor: 'reviewer-a',
      reason: '已逐章核对结构报告',
      chapters: [
        { output_ordinal: 1, decision: liveDecisions[0], reason: '线性章保留合理' },
        { output_ordinal: 2, decision: liveDecisions[1], reason: '复算变化仍不足，需要重规划' },
      ],
    }))
    expect(writing).not.toHaveBeenCalled()
    expect(job).not.toHaveBeenCalled()

    const replanButton = await screen.findByRole('button', { name: replan.label })
    expect(job).not.toHaveBeenCalled()
    await user.click(replanButton)
    expect(await screen.findByText(/stage4\.design --replan --yes/)).toBeVisible()
    expect(preview).toHaveBeenCalledWith({
      ...replan, required_inputs: [], sid: 'book-1', ordinal: null,
    })
    expect(job).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '确认发起（花钱）' }))
    await waitFor(() => expect(job).toHaveBeenCalledWith({
      ...replan, required_inputs: [], sid: 'book-1', ordinal: null,
    }))
    await finishNotices()
  }, 10000)

  it('B infeasible 没有正式 subject 时不造假审批，但仍可另行确认重跑 A', async () => {
    const doc = undecided()
    const replan = {
      kind: 'stage4.design',
      label: 'Stage4B 证据不可行，重新生成 Stage4A 盲候选',
      extra: ['--replan'],
    }
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...doc,
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: { s2: [], s4: [replan] },
    })
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const argv = 'python -m novel.cli run book-1 stage4.design --replan --yes'
    const preview = vi.spyOn(api, 'previewJob').mockResolvedValue({
      argv, paid: true, label: replan.label, kind: replan.kind,
      steps: [{
        label: replan.label, paid: true,
        writes: 'stage4/blind_candidate.json', argv,
      }],
    })
    const job = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'infeasible-replan', kind: replan.kind, label: replan.label, argv,
      state: 'running', paid: true, started: 1, finished: null,
      sid: 'book-1', ordinal: null,
    })

    renderRoute(
      <><StructureApprovalPanel
        rows={rows}
        identity
        viewSubject={null}
        onRefreshView={vi.fn()}
      /><ConfirmDialog /></>,
      '/n/book-1/stage4/1',
    )

    expect(await screen.findByText('正式 Stage4 计划还不能审批')).toBeVisible()
    expect(screen.queryByRole('button', { name: '保存全书结构裁决' })).not.toBeInTheDocument()
    expect(job).not.toHaveBeenCalled()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: replan.label }))
    expect(await screen.findByText(/stage4\.design --replan --yes/)).toBeVisible()
    expect(preview).toHaveBeenCalledWith({
      ...replan, required_inputs: [], sid: 'book-1', ordinal: null,
    })
    expect(job).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '确认发起（花钱）' }))
    await waitFor(() => expect(job).toHaveBeenCalledWith({
      ...replan, required_inputs: [], sid: 'book-1', ordinal: null,
    }))
  })

  it('页面 rows 与审批服务主体竞态错位时禁止提交，并同时重读两面', async () => {
    const approvalSubject = 'b'.repeat(64)
    const getApprovals = vi.spyOn(api, 'getApprovals').mockResolvedValue({
      ...undecided(),
      structure: {
        state: 'missing',
        current_subject_sha256: approvalSubject,
        current_decision: null,
        history: [],
        binding_concerns: [],
      },
    })
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    const approve = vi.spyOn(api, 'approveStructure')
    const refreshView = vi.fn().mockResolvedValue(undefined)

    renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={refreshView}
      />,
      '/n/book-1/stage4/1',
    )

    expect(await screen.findByText('结构审批对象已变化')).toBeVisible()
    expect(screen.getByText(SUBJECT)).toBeVisible()
    expect(screen.getByText(approvalSubject)).toBeVisible()
    expect(screen.getByText(/提交已禁用/)).toBeVisible()
    const save = screen.getByRole('button', { name: '保存全书结构裁决' })
    expect(save).toBeDisabled()

    const user = userEvent.setup()
    await user.click(save)
    expect(approve).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '重新读取结构与审批' }))
    await waitFor(() => {
      expect(refreshView).toHaveBeenCalledTimes(1)
      expect(getApprovals).toHaveBeenCalledTimes(2)
    })
    expect(approve).not.toHaveBeenCalled()
  })

  it('工作包读取失败时关闭式禁用 Stage4B 提交并明确原因', async () => {
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(undecided())
    vi.spyOn(api, 'getStructureApprovalWorkpack').mockRejectedValue(
      new Error('workpack unavailable'),
    )
    const approve = vi.spyOn(api, 'approveStructure')

    renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      />,
      '/n/book-1/stage4/1',
    )

    const save = await screen.findByRole('button', { name: '保存全书结构裁决' })
    await waitFor(() => expect(save.parentElement).toHaveAttribute(
      'title',
      '当前 Stage4B 工作包读取失败，不能绑定本轮审批',
    ))
    expect(save).toBeDisabled()
    expect(approve).not.toHaveBeenCalled()
  })

  it('同一当前 Stage4B 工作包已批准时默认跳过重复提交', async () => {
    const pack = await currentWorkpack()
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(undecided())
    vi.spyOn(api, 'getStructureApprovalWorkpack').mockResolvedValue({
      ...pack,
      disposition: 'already_current',
      current_decision_sha256: '8'.repeat(64),
    })

    renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      />,
      '/n/book-1/stage4/1',
    )

    const save = await screen.findByRole('button', { name: '保存全书结构裁决' })
    await waitFor(() => expect(save.parentElement).toHaveAttribute(
      'title',
      '同一当前 Stage4B 工作包已有全章接受决定，无需重复整读',
    ))
    expect(save).toBeDisabled()
    expect(screen.getByText(/默认跳过重复人工整读/)).toBeVisible()
  })

  it('表单完成后后台刷新工作包时禁用提交，刷新失败后也不复开', async () => {
    const pack = await currentWorkpack()
    const getWorkpack = vi.spyOn(api, 'getStructureApprovalWorkpack')
      .mockResolvedValue(pack)
    vi.spyOn(api, 'getEnums').mockResolvedValue(await mockApi.getEnums())
    vi.spyOn(api, 'getApprovals').mockResolvedValue(undecided())

    const { queryClient } = renderRoute(
      <StructureApprovalPanel
        rows={rows}
        identity={false}
        viewSubject={SUBJECT}
        onRefreshView={vi.fn()}
      />,
      '/n/book-1/stage4/1',
    )
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('第 1 章结构裁决'), 'accept')
    await user.type(screen.getByLabelText('第 1 章裁决理由'), '已核对线性入口')
    await user.selectOptions(screen.getByLabelText('第 2 章结构裁决'), 'accept')
    await user.type(screen.getByLabelText('第 2 章裁决理由'), '已核对重构顺序')
    await user.type(screen.getByLabelText('自报身份 actor'), 'reviewer-a')
    await user.type(screen.getByLabelText('本次批准 / 编辑理由 reason'), '逐章核对完成')
    const save = screen.getByRole('button', { name: '保存全书结构裁决' })
    await waitFor(() => expect(save).toBeEnabled())

    let rejectRefresh!: (error: Error) => void
    getWorkpack.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectRefresh = reject
    }))
    const callsBeforeRefresh = getWorkpack.mock.calls.length
    await act(async () => {
      void queryClient.refetchQueries({
        queryKey: qk.approvalWorkpack('book-1', 'structure'),
        type: 'active',
      })
    })
    await waitFor(() => expect(getWorkpack).toHaveBeenCalledTimes(callsBeforeRefresh + 1))
    await waitFor(() => expect(
      screen.getByRole('button', { name: '保存全书结构裁决' }),
    ).toBeDisabled())
    expect(screen.getByRole('button', { name: '保存全书结构裁决' }).parentElement)
      .toHaveAttribute('title', '正在读取或刷新当前 Stage4B 工作包')
    await act(async () => rejectRefresh(new Error('refresh failed')))
    await waitFor(() => expect(
      screen.getByRole('button', { name: '保存全书结构裁决' }).parentElement,
    ).toHaveAttribute(
      'title',
      '当前 Stage4B 工作包读取失败，不能绑定本轮审批',
    ))
    expect(screen.getByRole('button', { name: '保存全书结构裁决' })).toBeDisabled()
  })
})
