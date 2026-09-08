import { useEffect, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { api, splitLog } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { JobLogResponse, JobRequest } from '@/types/jobs'
import { useSid } from './useRouteParams'
import { invalidateBookReadModels } from './bookReadModels'

// AppShell、确认框和具体屏可能同时观察同一个 jobs query。终态刷新是书级副作用，
// 同一个 QueryClient / sid / job 只做一次，避免三个 observer 把同一批读模型重复拉取。
const lastTerminalRefresh = new WeakMap<QueryClient, Map<string, string>>()
const startedRunningJobs = new WeakMap<QueryClient, Map<string, string>>()

function refreshTerminalOnce(qc: QueryClient, sid: string, jobId: string) {
  let bySid = lastTerminalRefresh.get(qc)
  if (!bySid) {
    bySid = new Map<string, string>()
    lastTerminalRefresh.set(qc, bySid)
  }
  if (bySid.get(sid) === jobId) return
  bySid.set(sid, jobId)
  void invalidateBookReadModels(qc, sid)
}

function rememberStartedRunning(qc: QueryClient, sid: string, jobId: string) {
  let bySid = startedRunningJobs.get(qc)
  if (!bySid) {
    bySid = new Map<string, string>()
    startedRunningJobs.set(qc, bySid)
  }
  bySid.set(sid, jobId)
}

function forgetStartedRunning(qc: QueryClient, sid: string, jobId: string) {
  const bySid = startedRunningJobs.get(qc)
  if (bySid?.get(sid) === jobId) bySid.delete(sid)
}

/** 在跑的作业与历史。轮询由服务端下发的 running 决定，前端不自己判断。 */
export function useJobs(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  const observed = useRef<{ sid: string; runningId: string | null } | null>(null)

  const query = useQuery({
    queryKey: qk.jobs(sid),
    queryFn: () => api.getJobs(sid),
    enabled: !!sid,
    refetchInterval: (q) => (q.state.data?.running ? 1000 : false),
  })

  const runningId = query.data?.running?.id ?? null
  useEffect(() => {
    if (!sid || !query.data) return
    const previous = observed.current
    observed.current = { sid, runningId }
    let terminalJobId =
      previous &&
      previous.sid === sid &&
      previous.runningId !== null &&
      previous.runningId !== runningId
        ? previous.runningId
        : null

    // wontfix 这类免费命令可能短到 jobs 首次 refetch 已经直接看到终态，根本没有
    // 一帧 running 可供 observer 记录。POST /jobs 返回的真实 running job 另存一格，
    // 只在 jobs 历史明确给出它的终态时补上这次转换；不能凭「列表里暂时没有」猜完成。
    const startedJobId = startedRunningJobs.get(qc)?.get(sid) ?? null
    const startedRecord = startedJobId
      ? query.data.jobs.find((job) => job.id === startedJobId)
      : null
    if (
      terminalJobId === null &&
      startedJobId !== null &&
      runningId !== startedJobId &&
      startedRecord &&
      startedRecord.state !== 'running'
    ) {
      terminalJobId = startedJobId
    }
    if (terminalJobId === null) return
    // previous.runningId 不再是 running：无论 ok / landed_red / failed / unknown，
    // 终态之后都必须重读盘面，不能从外层退出码推断哪份产物已经落成。
    forgetStartedRunning(qc, sid, terminalJobId)
    refreshTerminalOnce(qc, sid, terminalJobId)
  }, [qc, query.data, runningId, sid])

  return query
}

/** 最近一条作业的日志。SSE id 与 query offset 都是 UTF-8 字节位置。 */
export function useJobLog(id: string | null) {
  const qc = useQueryClient()
  const sid = useSid()
  const [snapshot, setSnapshot] = useState<{
    id: string
    data?: JobLogResponse
    error?: unknown
  }>({ id: '' })

  useEffect(() => {
    if (!id) {
      setSnapshot({ id: '' })
      return
    }
    let raw = ''
    let closed = false
    setSnapshot({ id, data: { lines: [], offset: 0, done: false } })
    const close = api.streamJobLog(id, 0, {
      onEvent: (event) => {
        if (closed) return
        raw += event.text
        setSnapshot({
          id,
          data: {
            lines: splitLog(raw),
            offset: event.next_byte,
            done: event.done,
          },
        })
        if (event.done) void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
      },
      onError: (error) => {
        if (!closed) setSnapshot({ id, error })
      },
    })
    return () => {
      closed = true
      close()
    }
  }, [id, qc, sid])

  const current = snapshot.id === (id ?? '') ? snapshot : { id: id ?? '' }
  return {
    data: current.data,
    error: current.error,
    isPending: !!id && current.data === undefined && current.error === undefined,
    isError: current.error !== undefined,
  }
}

export function useStartJob(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: JobRequest) => api.startJob(req),
    onSuccess: (job) => {
      if (job.state === 'running') {
        rememberStartedRunning(qc, sid, job.id)
      } else {
        // 极短作业也可能在 POST 返回前就结束；此时响应本身已经是终态证据。
        refreshTerminalOnce(qc, sid, job.id)
      }
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })
}
