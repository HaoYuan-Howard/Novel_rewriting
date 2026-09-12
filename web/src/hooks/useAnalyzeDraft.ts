import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { AnalyzeDraftOwner, AnalyzeDraftEditRequest } from '@/types/analyzeDraft'
import { invalidateBookReadModels } from './bookReadModels'

export function useAnalyzeDraft(sid: string, owner: AnalyzeDraftOwner) {
  return useQuery({ queryKey: qk.analyzeDraft(sid, owner),
    queryFn: () => api.getAnalyzeDraft(sid, owner), retry: false })
}

export function useEditAnalyzeDraft(sid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (request: AnalyzeDraftEditRequest) => api.editAnalyzeDraft(sid, request),
    onSettled: async () => {
      await invalidateBookReadModels(qc, sid)
      await qc.invalidateQueries({ queryKey: qk.jobs(sid) })
      await qc.invalidateQueries({ queryKey: qk.run(sid) })
    },
  })
}
