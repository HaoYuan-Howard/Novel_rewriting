import { useState } from 'react'
import { useRunMetricLog, useRunMetrics } from '@/hooks/useRunMetrics'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { QueryStates } from '@/components/state/QueryStates'
import type { RunMetrics, UsageSummary, UnitMetrics } from '@/types/runMetrics'

const number = (value: number | null) => value === null ? '未知' : value.toLocaleString('zh-CN')
const duration = (value: number | null) => value === null ? '未知' : (value / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 秒'

function Usage({ usage }: { usage: UsageSummary }) {
  return <dl className="m-0 grid grid-cols-2 gap-18 xl:grid-cols-4">
    <div><dt className="text-ink-3">模型累计耗时</dt><dd className="m-0 text-title font-semibold">{duration(usage.model_time_ms)}</dd>
      {usage.model_time_ms === null ? <small>已知小计 {duration(usage.known_model_time_ms)}</small> : null}
      <p className="m-0 text-caption">活跃区间合并：{duration(usage.active_time_ms)}{usage.active_time_ms === null ? `（已知 ${duration(usage.known_active_time_ms)}）` : ''}</p></div>
    <div><dt className="text-ink-3">输入 / 输出 token</dt><dd className="m-0 text-title font-semibold">{number(usage.prompt_tokens)} / {number(usage.completion_tokens)}</dd>
      {usage.total_tokens === null ? <small>已知小计 {number(usage.known_prompt_tokens)} / {number(usage.known_completion_tokens)}；缺用量 {usage.missing_usage_calls} 次</small> : <small>合计 {number(usage.total_tokens)}；reasoning 已含在输出内</small>}</div>
    <div><dt className="text-ink-3">已确认网络调用 / 失败</dt><dd className="m-0 text-title font-semibold">{usage.wire_calls} / {usage.failed_calls}</dd>
      <small>结果未知 {usage.unknown_outcome_calls} 次；冲突记录 {usage.conflicting_rows} 条</small></div>
    <div><dt className="text-ink-3">缓存命中</dt><dd className="m-0 text-title font-semibold">{usage.cached_calls}</dd>
      <small>缓存状态未知 {usage.unknown_cache_calls} 次；缓存不重复计费</small></div>
  </dl>
}

function MetricLog({ sid, run }: { sid: string; run?: RunMetrics['runs'][number] }) {
  const [open, setOpen] = useState(false)
  const q = useRunMetricLog(sid, open ? run : undefined)
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>查看所属运行日志</summary>
    {!run || run.kind === 'ledger' ? <p>历史记录没有可核验的日志归属</p> : open ?
      <QueryStates q={q} loadingLabel="读取运行日志">{(text) => <pre className="max-h-260 overflow-auto whitespace-pre-wrap break-all text-caption">{text || '该运行尚无日志内容'}</pre>}</QueryStates> : null}
  </details>
}

function UnitRow({ unit, maximum, maxTokens, sid, runs }: { unit: UnitMetrics; maximum: number; maxTokens: number; sid: string; runs: RunMetrics['runs'] }) {
  return <tr className="border-t border-line">
    <td className="py-12 pr-18 align-top">{unit.node}<br /><span className="text-ink-3">{unit.role === 'commander' ? 'commander（历史）' : unit.role} · {unit.scope}</span></td>
    <td className="py-12 pr-18 align-top">{duration(unit.usage.model_time_ms)}
      <div className="mt-5 h-5 w-140 bg-inset" aria-hidden="true"><div className="h-full bg-ink-3" style={{ width: (maximum ? unit.usage.known_model_time_ms / maximum * 100 : 0) + '%' }} /></div>
      {unit.usage.model_time_ms === null ? <small>已知 {duration(unit.usage.known_model_time_ms)}</small> : null}
      <p className="m-0 text-caption">活跃区间 {duration(unit.usage.active_time_ms)}</p></td>
    <td className="py-12 pr-18 align-top">{number(unit.usage.prompt_tokens)} / {number(unit.usage.completion_tokens)}
      <div className="mt-5 h-5 w-140 bg-inset" aria-hidden="true"><div className="h-full bg-ink-3" style={{ width: (maxTokens ? (unit.usage.known_prompt_tokens + unit.usage.known_completion_tokens) / maxTokens * 100 : 0) + '%' }} /></div>
      缺用量 {unit.usage.missing_usage_calls} 次</td>
    <td className="py-12 align-top"><details><summary className="cursor-pointer">{unit.content_turns} 内容轮 · {unit.network_retries} 网络重试</summary>
      <p>历史采用 {unit.history_adopted} · 机械处理 {unit.mechanical} · 缓存处理 {unit.cached_processing}</p>
      {unit.issues.map((issue) => <p key={issue}>{issue}</p>)}
      {unit.attempts.length === 0 ? <p>无已登记网络尝试</p> : <ol className="pl-18">{unit.attempts.map((attempt) => <li key={attempt.attempt_id} className="my-9">
        第 {attempt.turn} 轮 · {attempt.state} · {duration(attempt.latency_ms)} · 输入/输出 {number(attempt.prompt_tokens)} / {number(attempt.completion_tokens)}
        <br />首 token：{duration(attempt.first_token_ms)}{attempt.finished_at ? ' · ' + attempt.finished_at : ''}
        {attempt.issue ? <p className="m-0">{attempt.issue}</p> : null}
        <details><summary>查看日志关联坐标</summary><code className="break-all">run={attempt.parent_run_id ?? attempt.run_id}<br />session={attempt.session_run_id ?? '未知'}<br />attempt={attempt.attempt_id}</code></details>
        <MetricLog sid={sid} run={runs.find((run) => run.run_id === (attempt.parent_run_id ?? attempt.run_id))} />
      </li>)}</ol>}
    </details></td>
  </tr>
}

export function RunMetricsPanel({ sid }: { sid: string }) {
  const [runId, setRunId] = useState<string>()
  const q = useRunMetrics(sid, runId)
  return <Panel>
    <PaneHeader title="运行耗时与用量" meta="来自权威运行记录；模型累计耗时与包含等待的运行历时分开显示" />
    <div className="px-18 py-14 text-body">
      <QueryStates q={q} loadingLabel="读取运行统计">{(data) => {
        const selected = data.runs.find((run) => run.run_id === data.run_id)
        const maximum = Math.max(0, ...data.units.map((unit) => unit.usage.known_model_time_ms))
        const maxTokens = Math.max(0, ...data.units.map((unit) => unit.usage.known_prompt_tokens + unit.usage.known_completion_tokens))
        return <div className="flex flex-col gap-18">
          <label>统计范围 <select aria-label="统计范围" value={runId ?? ''} onChange={(event) => setRunId(event.target.value || undefined)} className="ml-9 border border-line bg-card px-12 py-5">
            <option value="">本书累计（跨运行）</option>
            {data.runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.kind === 'agent' ? '自动运行' : run.kind === 'job' ? '人工节点作业' : '历史调用'} · {run.started_at ? new Date(run.started_at * 1000).toLocaleString('zh-CN') : run.run_id} · {run.state}</option>)}
          </select></label>
          {selected ? <p className="m-0">本次运行历时：{duration(selected.elapsed_seconds === null ? null : selected.elapsed_seconds * 1000)}（包含暂停和等待）</p> : null}
          <Usage usage={data.usage} />
          <p className="m-0 text-caption text-ink-3">活跃区间由调用完成时刻与耗时还原，并发区间只计一次；旧记录保留原时间精度。不含人工等待、机械处理和校验。条形显示已知小计。</p>
          {data.issues.length ? <ul className="m-0 pl-18" aria-label="统计证据说明">{data.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
          {data.unassigned_rows ? <details><summary>未归属工作单元的 {data.unassigned_rows} 条记录</summary><Usage usage={data.unassigned_usage} /></details> : null}
          <div className="overflow-x-auto"><table className="w-full text-left" aria-label="工作单元耗时与用量"><thead><tr><th>节点 / 角色 / 范围</th><th>模型累计耗时</th><th>输入 / 输出 token</th><th>内容轮与尝试</th></tr></thead>
            <tbody>{data.units.map((unit) => <UnitRow key={unit.unit_id} unit={unit} maximum={maximum} maxTokens={maxTokens} sid={sid} runs={data.runs} />)}</tbody></table>
            {!data.units.length ? <p>此范围尚无可关联的工作单元</p> : null}
          </div>
        </div>
      }}</QueryStates>
    </div>
  </Panel>
}
