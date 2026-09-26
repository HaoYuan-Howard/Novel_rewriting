import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { JOB } from '@/constants/vocabulary'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { READY_PROFILES } from '@/test/agent'
import type { UsageSummary } from '@/types/runMetrics'
import { RunScreen } from './RunScreen'

/** 全零读数：统计面板只验形状，不在这里重放计费语义。 */
function emptyUsage(): UsageSummary {
  return {
    observed_rows: 0,
    conflicting_rows: 0,
    wire_calls: 0,
    cached_calls: 0,
    unknown_cache_calls: 0,
    failed_calls: 0,
    unknown_outcome_calls: 0,
    missing_usage_calls: 0,
    missing_latency_calls: 0,
    known_prompt_tokens: 0,
    known_completion_tokens: 0,
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    reasoning_tokens: null,
    known_model_time_ms: 0,
    model_time_ms: null,
    known_active_time_ms: 0,
    active_time_ms: null,
    missing_interval_calls: 0,
  }
}

beforeEach(() => {
  vi.spyOn(api, 'getNovel').mockImplementation(async (sid) => mockApi.getNovel(sid))
})

describe('RunScreen 交付终验', () => {
  it('显示后端当前恢复与续跑前沿，历史工作单元不决定下一角色', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    vi.spyOn(api, 'getRunProgress').mockResolvedValue({
      ...run,
      resume_frontier: {
        ...run.resume_frontier, next_handler: 'human',
        review_states: [
          { node: 'str06.stage2.source', scope: 'book', state: 'accepted' },
          { node: 'str06.stage2.names', scope: 'book', state: 'uncertain' },
        ],
      },
      current_recovery_actions: ['book: replay_response'],
      work_units: [{ unit_id: 'old', node: 'str06.stage2.names', role: 'executor', scope: 'chapter:1',
        content_used: 1, content_limit: 4, content_remaining: 3, state: 'adopted',
        latest_request_sha256: null, latest_response_sha256: null, recovery_action: null }],
    })
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)
    expect(await screen.findByText('下一处理者：人工（只读监控面上的例外动作）')).toBeVisible()
    expect(screen.getByText('str06.stage2.source · book：accepted')).toBeVisible()
    expect(screen.getByText('str06.stage2.names · book：uncertain')).toBeVisible()
    expect(screen.getByText('当前恢复：book: replay_response')).toBeVisible()
  })

  it('原样显示后端工作单元预算和恢复状态，不从已用次数重算余量', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    vi.spyOn(api, 'getRunProgress').mockResolvedValue({
      ...run,
      work_units: [{
        unit_id: 'u1', node: 'str06.stage2.names', role: 'executor', scope: 'book',
        content_used: 2, content_limit: 4, content_remaining: 1,
        state: 'response_saved', latest_request_sha256: null,
        latest_response_sha256: 'a'.repeat(64), recovery_action: 'replay_saved_response',
        content_base_limit: 4, content_granted: 0,
        processing: { run_id: 'saved', input_sha256: 'a'.repeat(64), total: 1,
          pending: 0, generated: 0, mechanical: 0, history_adopted: 1,
          cached: 0, failed: 0, unknown: 0, reason: null },
        review: { subject_sha256: 'b'.repeat(64), total: 19, pending: 1, decision_inherited: 18 },
      }],
    })
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)
    expect(await screen.findByText('2 / 4，剩余 1')).toBeInTheDocument()
    expect(screen.getByText('response_saved')).toBeInTheDocument()
    expect(screen.getByText('replay_saved_response')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: '工作单元预算' })).toHaveTextContent('历史采用 1')
    expect(screen.getByRole('table', { name: '工作单元预算' })).toHaveTextContent('待审 1，决定继承 18')
  })

  it('按服务端 allowed_controls 恢复，202 仍返回 paused 时持续轮询到 running', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const active = await mockApi.startAgentRun({
      sid: SAMPLE_SID,
      authorized: true,
      egress_authorized: true,
      profiles: READY_PROFILES,
    })
    const paused = {
      ...active,
      state: 'paused' as const,
      note: '当前 claim 已安全闭合',
      allowed_controls: ['resume', 'stop'] as const,
    }
    const resumed = {
      ...paused,
      state: 'running' as const,
      note: '同一 parent run 已恢复',
      allowed_controls: ['pause', 'stop'] as const,
    }
    vi.spyOn(api, 'getRunProgress').mockResolvedValue({
      ...run, work_units: null, work_units_unavailable_reason: '处理来源账损坏，保持未知',
    })
    let reads = 0
    const getRuns = vi.spyOn(api, 'getAgentRuns').mockImplementation(async () => {
      reads += 1
      const record = reads < 3 ? paused : resumed
      return { runs: [{ ...record, allowed_controls: [...record.allowed_controls] }] }
    })
    // resume 是异步 202：HTTP 返回时 worker 可能还没落 resumed 事件。
    const resume = vi.spyOn(api, 'resumeAgentRun').mockResolvedValue({
      ...paused,
      allowed_controls: [...paused.allowed_controls],
    })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    const button = await screen.findByRole('button', { name: '继续' })
    expect(screen.getByText('工作单元状态未知：处理来源账损坏，保持未知')).toBeVisible()
    expect(screen.queryByRole('button', { name: '暂停' })).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-run-note')).toHaveTextContent(
      '当前 claim 已安全闭合',
    )
    fireEvent.click(button)
    await waitFor(() => expect(resume).toHaveBeenCalledWith(paused.run_id))
    await waitFor(() => expect(getRuns).toHaveBeenCalledTimes(2))

    const transitioning = screen.getByRole('button', { name: '恢复中…' })
    expect(transitioning).toBeDisabled()
    await waitFor(() => expect(getRuns).toHaveBeenCalledTimes(3), { timeout: 4000 })
    expect(await screen.findByRole('button', { name: '暂停' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '恢复中…' })).not.toBeInTheDocument()
  })

  it('恢复响应丢失时仍轮询服务端，直到真实 running 状态收敛', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const active = await mockApi.startAgentRun({
      sid: SAMPLE_SID,
      authorized: true,
      egress_authorized: true,
      profiles: READY_PROFILES,
    })
    const paused = {
      ...active,
      state: 'paused' as const,
      note: '当前 claim 已安全闭合',
      allowed_controls: ['resume', 'stop'] as const,
    }
    const resumed = {
      ...paused,
      state: 'running' as const,
      note: '服务端其实已经恢复',
      allowed_controls: ['pause', 'stop'] as const,
    }
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    let reads = 0
    const getRuns = vi.spyOn(api, 'getAgentRuns').mockImplementation(async () => {
      reads += 1
      const record = reads < 3 ? paused : resumed
      return { runs: [{ ...record, allowed_controls: [...record.allowed_controls] }] }
    })
    const resume = vi.spyOn(api, 'resumeAgentRun').mockRejectedValue(
      new Error('202 响应在网络途中丢失'),
    )

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)
    fireEvent.click(await screen.findByRole('button', { name: '继续' }))

    await waitFor(() => expect(resume).toHaveBeenCalledWith(paused.run_id))
    expect(screen.getByRole('button', { name: '恢复中…' })).toBeDisabled()
    await waitFor(() => expect(getRuns).toHaveBeenCalledTimes(3), { timeout: 4000 })
    expect(await screen.findByRole('button', { name: '暂停' })).toBeEnabled()
    expect(screen.getByTestId('agent-run-note')).toHaveTextContent(
      '服务端其实已经恢复',
    )
  })

  it('真实 exit2/stopped 默认展示停点理由并只导航回后端当前阶段', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const stopStep = run.steps.find((step) => step.node === 'stage0.remove') ?? run.steps[0]
    if (!stopStep) throw new Error('mock pipeline 缺可用停点节点')
    run.resume_frontier = {
      state: 'blocked',
      node: stopStep.node,
      seq: stopStep.seq,
      label: stopStep.label,
      resume_verdict: 'run',
      why: '同进程租约停点；可在进度屏继续或熄火',
      waiting_on: stopStep.node,
      paid: false,
      writes: 'approval',
      next_handler: 'human',
      review_states: null,
      verified_skipped_nodes: [],
      verified_skipped_count: 0,
    }
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({
      runs: [{
        run_id: 'run-1',
        start: {
          sid: SAMPLE_SID,
          argv: ['claude', '-p', 'x'],
          prompt: 'x',
          authorized: true,
          egress_authorized: true,
          code_version: 'abc',
          profiles: null,
        },
        contract: null,
        epochs: [],
        active_epoch_id: null,
        started: 1,
        ended: 2,
        exit_code: 2,
        state: 'stopped',
        note: null,
        outcome: {
          state: 'stopped',
          reason: '同进程租约停点；可在进度屏继续或熄火',
          checked_at: 3,
        },
        allowed_controls: [],
      }],
    })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    const toggle = await screen.findByRole('button', {
      name: /查看交付终验依据/,
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(await screen.findByTestId('delivery-reason')).toHaveTextContent(
      '同进程租约停点',
    )
    expect(screen.getByText('stopped 安全停点')).toBeVisible()
    expect(screen.queryByText('failed 失败')).not.toBeInTheDocument()
    expect(screen.getByText(/exit 2/)).toBeVisible()
    const back = screen.getByTestId('stopped-stage-link')
    expect(back).toHaveTextContent(`返回当前阶段：${stopStep.label}`)
    expect(back).toHaveAttribute('href', `/n/${SAMPLE_SID}/stage0`)
    expect(screen.queryByRole('button', { name: '继续' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '暂停' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '熄火' })).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-profiles-unknown')).toHaveTextContent(
      '历史未记录，不能按当前默认倒推',
    )

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('delivery-reason')).not.toBeInTheDocument()
  })

  it('exit70 仍显示 failed，不被安全停点状态洗白', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({
      runs: [{
        run_id: 'run-crashed',
        start: {
          sid: SAMPLE_SID,
          argv: ['claude', '-p', 'x'],
          prompt: 'x',
          authorized: true,
          egress_authorized: true,
          code_version: 'abc',
          profiles: null,
        },
        contract: null,
        epochs: [],
        active_epoch_id: null,
        started: 1,
        ended: 2,
        exit_code: 70,
        state: 'failed',
        note: null,
        outcome: {
          state: 'unknown',
          reason: '外部进程异常退出，未形成可信交付结论',
          checked_at: 3,
        },
        allowed_controls: [],
      }],
    })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    expect(await screen.findByText('failed 失败')).toBeVisible()
    expect(screen.queryByText('stopped 安全停点')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stopped-stage-link')).not.toBeInTheDocument()
  })

  it.each(['commander', 'supervisor', undefined] as const)(
    '父运行 exit1 来源=%s 时保持服务端失败与交付未知，不推断已落盘',
    async (exitOrigin) => {
      const run = await mockApi.getRunProgress(SAMPLE_SID)
      vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
      vi.spyOn(api, 'getAgentRuns').mockResolvedValue({
        runs: [{
          run_id: 'run-external-exit-one',
          start: {
            sid: SAMPLE_SID,
            argv: ['codex', 'exec', '--json'],
            prompt: 'x',
            authorized: true,
            egress_authorized: true,
            code_version: 'abc',
            profiles: null,
          },
          contract: null,
          epochs: [],
          active_epoch_id: null,
          started: 1,
          ended: 2,
          exit_code: 1,
          ...(exitOrigin === undefined ? {} : { exit_origin: exitOrigin }),
          state: 'failed',
          note: null,
          outcome: {
            state: 'unknown',
            reason: '外部进程失败，未形成可信交付结论',
            checked_at: 3,
          },
          allowed_controls: [],
        }],
      })

      renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

      expect(await screen.findByText('failed 失败')).toBeVisible()
      expect(screen.getByText(/起于.*exit 1/)).toBeVisible()
      const delivery = screen.getByRole('button', {
        name: '查看交付终验依据：交付未知',
      })
      expect(delivery).toBeVisible()
      fireEvent.click(delivery)
      expect(screen.getByTestId('delivery-reason')).toHaveTextContent(
        '外部进程失败，未形成可信交付结论',
      )
      expect(screen.queryByText('landed_red 已落盘但有红项')).not.toBeInTheDocument()
      expect(screen.queryByText(/正式结果.*已经落盘/)).not.toBeInTheDocument()
      expect(screen.queryByTestId('agent-run-note')).not.toBeInTheDocument()
      expect(screen.queryByText('stopped 安全停点')).not.toBeInTheDocument()
      expect(screen.queryByTestId('stopped-stage-link')).not.toBeInTheDocument()
    },
  )

  it('展示每次运行已冻结的默认/override 精确配置', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({
      runs: [{
        run_id: 'run-profile-history',
        start: {
          sid: SAMPLE_SID,
          argv: ['agent-dynamic'],
          prompt: 'x',
          authorized: false,
          egress_authorized: true,
          code_version: 'abc',
          profiles: {
            ...READY_PROFILES,
            executor_overrides: {
              'node.dynamic-a': { ...READY_PROFILES.executor_default },
            },
          },
        },
        contract: null,
        epochs: [{
          epoch_id: 'epoch-1', ordinal: 1, reason: 'initial', argv: ['agent-dynamic'],
          prompt_sha256: '1'.repeat(64), protocol_version: 'v1', cli_version: '1.0',
          code_version: 'code-before-rollover', skill_sha256: '2'.repeat(64),
          session_run_id: 'session-1', resume_provider_session_id: null,
        }, {
          epoch_id: 'epoch-2', ordinal: 2, reason: 'code_drift', argv: ['agent-dynamic'],
          prompt_sha256: '3'.repeat(64), protocol_version: 'v1', cli_version: '1.0',
          code_version: 'code-after-rollover', skill_sha256: '4'.repeat(64),
          session_run_id: 'session-2', resume_provider_session_id: null,
        }],
        active_epoch_id: 'epoch-2',
        started: 1,
        ended: null,
        exit_code: null,
        state: 'running',
        note: null,
        outcome: null,
        allowed_controls: ['stop'],
      }],
    })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    const snapshot = await screen.findByTestId('agent-profiles-snapshot')
    expect(snapshot).toHaveTextContent('deepseek-v4-pro')
    expect(snapshot).toHaveTextContent('node.dynamic-a')
    expect(snapshot).toHaveTextContent('effort=high')
    expect(screen.getByTestId('agent-epoch')).toHaveTextContent(
      '当前执行 epoch：#2 · code_drift · code-after-r',
    )
    expect(screen.getByTestId('agent-epoch')).toHaveTextContent('已换 epoch 1 次')
  })
})

describe('RunScreen 节点图与完成度', () => {
  it('默认折叠 operator 已确认无需重跑的前缀，并显示真实续跑处理者', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    const next = run.steps.find((step) => step.node === 'str06.stage2.names')
    const running = run.steps.find((step) => step.node === 'str06.stage2.source')
    if (!next) throw new Error('mock pipeline 缺 str06.stage2.names')
    if (!running) throw new Error('mock pipeline 缺 str06.stage2.source')
    running.beacon = 'running'
    const skipped = run.steps
      .filter((step) => step.seq < next.seq && step.node !== running.node)
      .map((step) => step.node)
    run.resume_frontier = {
      state: 'blocked',
      node: next.node,
      seq: next.seq,
      label: next.label,
      resume_verdict: 'run',
      why: '同指纹熔断不重购，等待新的授权',
      waiting_on: null,
      paid: false,
      writes: 'artifact',
      next_handler: 'executor',
      review_states: null,
      verified_skipped_nodes: skipped,
      verified_skipped_count: skipped.length,
    }
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    const frontier = await screen.findByTestId('resume-frontier')
    expect(frontier).toHaveTextContent('下一节点：Stage2 源名称结构窄调用并发布 Characters v4')
    expect(frontier).toHaveTextContent('当前执行：Stage2 源语义抽取与确定性 Blueprint 编译（str06.stage2.source）')
    expect(frontier).toHaveTextContent('下一处理者：Executor（执行既有决定）')
    expect(screen.getByRole('button', { name: '跳到下一节点' })).toBeEnabled()
    expect(screen.queryAllByTitle('stage0.remove')).toHaveLength(0)

    const toggle = screen.getByRole('button', { name: /当前快照已确认无需重跑/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByTitle('stage0.remove')[0]).toBeVisible()
  })

  it('renders exit 1 as landed_red instead of a failed command', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    run.steps[0] = { ...run.steps[0], beacon: 'landed_red', last_exit: 1 }
    run.events_tail = [{
      ts: 1,
      sid: SAMPLE_SID,
      run_id: 'run-screen-test',
      execution_id: 'execution-screen-test',
      node: run.steps[0].node,
      phase: 'end',
      exit_code: 1,
      note: null,
      beacon: 'landed_red',
    }]
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    expect(await screen.findByText('landed_red 已落盘但有红项')).toBeInTheDocument()
    expect(screen.getAllByText('无运行记录').length).toBeGreaterThan(0)
    expect(screen.queryByText('pending 还没跑到')).not.toBeInTheDocument()
    expect(screen.getByText('UI、队列与 agent 共用同一套运行机制。')).toBeVisible()
    expect(screen.getByText('每行是一条命令记录；无记录不代表阶段未完成')).toBeVisible()
    expect(screen.queryByText('发起口在语料库或 Stage0；互斥由服务端 409 裁决')).not.toBeInTheDocument()
    expect(screen.queryByText('failed 失败')).not.toBeInTheDocument()
    expect(screen.getAllByText('exit 1').some(
      (row) => row.style.color === JOB.landed_red.color,
    )).toBe(true)
  })

  it('按服务端 beacon 把 exit2 时间线与节点显示为安全停点', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    run.steps[0] = { ...run.steps[0], beacon: 'stopped', last_exit: 2 }
    run.events_tail = [{
      ts: 1,
      sid: SAMPLE_SID,
      run_id: 'run-stopped-event',
      execution_id: 'execution-stopped-event',
      node: run.steps[0].node,
      phase: 'end',
      exit_code: 2,
      note: '显式终态，未落盘',
      beacon: 'stopped',
    }]
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    expect((await screen.findAllByText('stopped 安全停点')).length).toBeGreaterThan(0)
    expect(screen.getByText('exit 2 · stopped 安全停点')).toBeVisible()
    expect(screen.queryByText('failed 失败')).not.toBeInTheDocument()
  })

  it('不再自建层级校验面板：/check 取值面只剩 stage0/stage1', async () => {
    const run = await mockApi.getRunProgress(SAMPLE_SID)
    vi.spyOn(api, 'getRunProgress').mockResolvedValue(run)
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ runs: [] })
    vi.spyOn(api, 'getRunMetrics').mockResolvedValue({
      sid: SAMPLE_SID,
      run_id: null,
      runs: [],
      usage: emptyUsage(),
      units: [],
      unassigned_rows: 0,
      unassigned_usage: emptyUsage(),
      issues: [],
    })
    const getCheck = vi.spyOn(api, 'getCheck').mockResolvedValue(null)

    renderRoute(<RunScreen />, `/n/${SAMPLE_SID}/run`)

    // 层级复检归 Stage0/Stage1 两屏的 CheckPanel；进度屏不再逐层打 /check。
    expect(await screen.findByTestId('resume-frontier')).toBeVisible()
    expect(await screen.findByText('各层产物完成度')).toBeVisible()
    expect(getCheck).not.toHaveBeenCalled()
    expect(screen.queryByText('各层产物完成度与校验')).not.toBeInTheDocument()
    expect(screen.queryByText(/badge 与计数来自各层 CheckReport/)).not.toBeInTheDocument()
    // 旧链蓝图进度面板随该字段与旧链视图一起退役，屏上不再有第二个生成进度落点。
    expect(screen.queryByLabelText('蓝图生成进度')).not.toBeInTheDocument()
  })
})
