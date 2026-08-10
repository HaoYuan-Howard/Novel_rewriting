import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { JobRequest } from '@/types/jobs'
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

/** 最近一条作业的日志。真后端上这条会换成 /stream?offset=N 的 SSE，签名不变。 */
export function useJobLog(id: string | null) {
  return useQuery({
    queryKey: qk.jobLog(id ?? ''),
    queryFn: () => api.getJobLog(id ?? '', 0),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.done ? false : 800),
  })
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
