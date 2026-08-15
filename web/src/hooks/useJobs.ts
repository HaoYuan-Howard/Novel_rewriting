import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, splitLog } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { JobLogResponse, JobRequest } from '@/types/jobs'
import { useSid } from './useRouteParams'

/** 在跑的作业与历史。轮询由服务端下发的 running 决定，前端不自己判断。 */
export function useJobs(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid

  return useQuery({
    queryKey: qk.jobs(sid),
    queryFn: () => api.getJobs(sid),
    enabled: !!sid,
    refetchInterval: (q) => (q.state.data?.running ? 1000 : false),
  })
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })
}
