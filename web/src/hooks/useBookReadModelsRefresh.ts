import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { AgentRunsResponse } from '@/types/agent'
import type { RunProgress } from '@/types/run'
import { invalidateBookReadModels } from './bookReadModels'
import { qk } from '@/api/queryKeys'

interface Observation {
  fingerprint: string
  version: number
  query: object | undefined
}

// 同一 QueryClient 可有多个 observer；副作用属于书与来源，不属于某个组件。
const observed = new WeakMap<QueryClient, Map<string, Observation>>()

/** 只比较持久进展，不把 elapsed 或诊断文案的计时变化当作全书失效事件。 */
export function progressReadIdentity(progress: RunProgress): string {
  const { why: _why, ...frontier } = progress.resume_frontier
  return JSON.stringify({
    stage_done: progress.stage_done,
    frontier,
    steps: progress.steps.map(({ node, beacon, last_exit, last_ts }) => (
      { node, beacon, last_exit, last_ts }
    )),
    events: progress.events_tail.map(({ note: _note, ...event }) => event),
  })
}

/** checked_at 只是读数时间；进程/epoch/交付状态本身改变才刷新书的读面。 */
export function agentReadIdentity(response: AgentRunsResponse): string {
  return JSON.stringify(response.runs.map((run) => ({
    run_id: run.run_id,
    state: run.state,
    ended: run.ended,
    exit_code: run.exit_code,
    exit_origin: run.exit_origin,
    active_epoch_id: run.active_epoch_id,
    epochs: run.epochs.map(({ epoch_id, code_version, reason }) => ({ epoch_id, code_version, reason })),
    outcome: run.outcome === null ? null : Object.fromEntries(
      Object.entries(run.outcome).filter(([key]) => key !== 'checked_at'),
    ),
  })).sort((left, right) => left.run_id.localeCompare(right.run_id)))
}

/**
 * 只在一次成功的实际取数后观察，挂载前缓存不能冒充这次服务端观察。
 * 首次观察统一取消并复验材料：并发 GET 没有共同 revision，不能认定同批读数更晚。
 * 之后同快照无操作，真正变化才刷新。两个来源均排除自身。
 */
export function useBookReadModelsRefresh(
  sid: string, source: 'run' | 'agentRuns', fingerprint: string | null,
  fetchedAfterMount: boolean, fetching: boolean,
) {
  const qc = useQueryClient()
  const mount = useRef({ sid, first: true })
  if (mount.current.sid !== sid) mount.current = { sid, first: true }
  // 每次成功提交有独立版本，即使多次响应和挂载恰在同一毫秒也不会混为一次。
  const query = qc.getQueryCache().find({
    queryKey: source === 'run' ? qk.run(sid) : qk.agentRuns(sid), exact: true,
  })
  const version = query?.state.dataUpdateCount ?? 0
  useEffect(() => {
    if (!sid || fingerprint === null || !fetchedAfterMount || fetching) return
    let seen = observed.get(qc)
    if (!seen) {
      seen = new Map()
      observed.set(qc, seen)
    }
    const key = source + '\0' + sid
    const previous = seen.get(key)
    const first = mount.current.first
    mount.current.first = false
    const changed = previous !== undefined && previous.fingerprint !== fingerprint
    if (!changed && (!first || (previous?.query === query && previous?.version === version))) return
    seen.set(key, { fingerprint, version, query })
    void invalidateBookReadModels(qc, sid)
  }, [qc, sid, source, fingerprint, query, version, fetchedAfterMount, fetching])
}