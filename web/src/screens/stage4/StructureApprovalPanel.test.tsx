import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import type { Stage4IndexRow } from '@/types/stages'
import { StructureApprovalPanel } from './StructureApprovalPanel'

const SUBJECT = 'a'.repeat(64)

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
    span_chars: 500,
    scenes: 1,
    hidden: 0,
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
    span_chars: 760,
    scenes: 2,
    hidden: 1,
  },
]

function undecided(): ApprovalDoc {
  return {
    v: 3,
    sid: 'book-1',
    gate: {
      s0: true, s1: true, s2: true, s3: true,
      s4: false, s5: false, s6: false, s7: false,
    },
    seen: { read: true, global: true },
    writing: false,
    structure: {
      state: 'missing',
      current_subject_sha256: SUBJECT,
      current_decision: null,
      history: [],
    },
    spawns: {},
  }
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('Stage4 typed 结构审批', () => {
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
      /><ConfirmDialog /></>,
      '/n/book-1/stage4/1',
    )

    expect(await screen.findByText(SUBJECT)).toBeVisible()
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
      subject_sha256: SUBJECT,
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
  })

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
        current_decision: null, history: [],
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
})
