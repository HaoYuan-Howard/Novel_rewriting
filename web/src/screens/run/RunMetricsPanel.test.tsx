import { useState } from 'react'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { renderRoute } from '@/test/render'
import type { RunMetrics, UsageSummary } from '@/types/runMetrics'
import { RunMetricsPanel } from './RunMetricsPanel'

// 当前响应的合成例；不修改冻结抓包，不冒充真实模型用量。
const usage: UsageSummary = {
  observed_rows: 3, conflicting_rows: 0, wire_calls: 2, cached_calls: 1,
  unknown_cache_calls: 0, failed_calls: 1, unknown_outcome_calls: 0,
  missing_usage_calls: 1, missing_latency_calls: 0,
  known_prompt_tokens: 100, known_completion_tokens: 20,
  prompt_tokens: null, completion_tokens: null, total_tokens: null, reasoning_tokens: null,
  known_model_time_ms: 15000, model_time_ms: 15000,
  known_active_time_ms: 10000, active_time_ms: 10000, missing_interval_calls: 0,
}

function fixture(sid = 'book-1', runId?: string): RunMetrics {
  return {
    sid, run_id: runId ?? null, usage,
    runs: [{ run_id: 'run-1', kind: 'agent', state: 'failed', started_at: 100, ended_at: 200, elapsed_seconds: 100 }],
    units: [{ unit_id: sid + '-unit', node: 'str06.stage2.chapters', role: 'executor', scope: 'chapter:3',
      content_turns: 1, network_retries: 1, mechanical: 0, cached_processing: 1,
      usage, issues: ['一次失败尚无用量'], attempts: [{
        attempt_id: sid + '-attempt', turn: 1, run_id: 'producer-1', parent_run_id: 'run-1',
        session_run_id: 'session-1', state: 'error', cached: false,
        prompt_tokens: null, completion_tokens: null, reasoning_tokens: null,
        latency_ms: 10000, first_token_ms: null, finished_at: null, issue: '用量未返回',
      }] }],
    unassigned_rows: 0, unassigned_usage: usage, issues: [],
  }
}

describe('运行统计的真实消费形状', () => {
  it('区分未知、小计、累计耗时与活跃区间，日志只在展开后读取', async () => {
    vi.spyOn(api, 'getRunMetrics').mockResolvedValue(fixture())
    const log = vi.spyOn(api, 'getAgentRunLog').mockResolvedValue({ text: '该运行的原始日志', next_byte: 24 })
    renderRoute(<RunMetricsPanel sid="book-1" />)
    expect(await screen.findByText('已知小计 100 / 20；缺用量 1 次')).toBeVisible()
    expect(screen.getByText('活跃区间合并：10 秒')).toBeVisible()
    expect(screen.getByText('结果未知 0 次；冲突记录 0 条')).toBeVisible()
    expect(log).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('1 内容轮 · 1 网络重试'))
    expect(await screen.findByText('机械处理 0 · 缓存处理 1')).toBeVisible()
    expect(screen.queryByText(/历史采用/)).not.toBeInTheDocument()
    expect(screen.getByText(/首 token：未知/)).toBeVisible()
    fireEvent.click(screen.getByText('查看所属运行日志'))
    expect(await screen.findByText('该运行的原始日志')).toBeVisible()
    expect(log).toHaveBeenCalledWith('run-1', 0)
  })

  it('切换 run 先等待该范围的响应，不沿用全书累计', async () => {
    let resolve!: (value: RunMetrics) => void
    const get = vi.spyOn(api, 'getRunMetrics').mockImplementation(async (sid, runId) => {
      if (!runId) return fixture(sid)
      return new Promise<RunMetrics>((done) => { resolve = done })
    })
    const { queryClient } = renderRoute(<RunMetricsPanel sid="book-1" />)
    await screen.findByRole('table', { name: '工作单元耗时与用量' })
    fireEvent.change(screen.getByLabelText('统计范围'), { target: { value: 'run-1' } })
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('book-1', 'run-1'))
    expect(screen.queryByRole('table', { name: '工作单元耗时与用量' })).not.toBeInTheDocument()
    await act(async () => resolve(fixture('book-1', 'run-1')))
    expect(await screen.findByText('本次运行历时：100 秒（包含暂停和等待）')).toBeVisible()
    expect(queryClient.getQueryData(qk.runMetrics('book-1', 'run-1'))).toEqual(fixture('book-1', 'run-1'))
    expect(queryClient.getQueryState(qk.run('book-1'))).toBeUndefined()
  })

  it('跨书切换不串工作单元或保留原书 run 筛选', async () => {
    const get = vi.spyOn(api, 'getRunMetrics').mockImplementation(async (sid, runId) => fixture(sid, runId))
    function Books() {
      const [sid, setSid] = useState('book-1')
      return <><button onClick={() => setSid('book-2')}>切到第二本</button><RunMetricsPanel key={sid} sid={sid} /></>
    }
    renderRoute(<Books />)
    await screen.findByLabelText('统计范围')
    fireEvent.change(screen.getByLabelText('统计范围'), { target: { value: 'run-1' } })
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('book-1', 'run-1'))
    fireEvent.click(screen.getByText('切到第二本'))
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('book-2', undefined))
    expect(await screen.findByLabelText('统计范围')).toHaveValue('')
  })

  it('账读取失败显示错误，空工作单元保持明确的空态', async () => {
    vi.spyOn(api, 'getRunMetrics').mockRejectedValueOnce(new Error('成本账损坏'))
      .mockResolvedValue({ ...fixture(), units: [], issues: ['无工作单元关联'] })
    renderRoute(<RunMetricsPanel sid="book-1" />)
    expect(await screen.findByText('成本账损坏')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(await screen.findByText('此范围尚无可关联的工作单元')).toBeVisible()
    expect(within(screen.getByLabelText('统计证据说明')).getByText('无工作单元关联')).toBeVisible()
  })
})
