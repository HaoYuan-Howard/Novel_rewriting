import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useSid } from './useRouteParams'

/**
 * 只读审批投影。人工闸写面已退役；Stage 页只消费 getApprovals。
 */
export function useApprovals(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid

  const q = useQuery({
    queryKey: qk.approvals(sid),
    queryFn: () => api.getApprovals(sid),
    enabled: !!sid,
    staleTime: Infinity,
  })

  return {
    sid,
    doc: q.isError ? null : q.data ?? null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
  }
}
