import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { AnalyzeDraftOwner } from '@/types/analyzeDraft'

/** 分析草稿只读查询；人工写回已退役。 */
export function useAnalyzeDraft(sid: string, owner: AnalyzeDraftOwner) {
  return useQuery({
    queryKey: qk.analyzeDraft(sid, owner),
    queryFn: () => api.getAnalyzeDraft(sid, owner),
    retry: false,
  })
}
