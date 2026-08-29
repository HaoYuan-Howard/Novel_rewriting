import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useSid } from './useRouteParams'

/** A/B 工作包各绑定自己的当前主体，不能共享 query key 或从审批文档拼出来。 */
export function useApprovalWorkpack(kind: 'design' | 'structure') {
  const sid = useSid()
  return useQuery({
    queryKey: qk.approvalWorkpack(sid, kind),
    queryFn: () => kind === 'design'
      ? api.getDesignApprovalWorkpack(sid)
      : api.getStructureApprovalWorkpack(sid),
    enabled: !!sid,
    retry: false,
  })
}
