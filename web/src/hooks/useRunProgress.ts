import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useSid } from './useRouteParams'
import { progressReadIdentity, useBookReadModelsRefresh } from './useBookReadModelsRefresh'

/** 跑书进度。有步在跑 2.5s 一轮，都停着 10s 一轮 —— 由服务端下发的 beacon 决定，前端不自己掐表。 */
export function useRunProgress(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid

  const query = useQuery({
    queryKey: qk.run(sid),
    queryFn: () => api.getRunProgress(sid),
    enabled: !!sid,
    refetchOnMount: 'always',
    refetchInterval: (q) =>
      q.state.data?.steps.some((step) => step.beacon === 'running') ? 2500 : 10000,
  })
  useBookReadModelsRefresh(
    sid, 'run', query.isSuccess && query.data.sid === sid ? progressReadIdentity(query.data) : null,
    query.isFetchedAfterMount, query.isFetching,
  )
  return query
}
