import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { invalidateBookReadModels } from '@/hooks/bookReadModels'
import type { BlueprintRefinementRequest } from '@/types/plot.generated'

export function useBlueprintRefinement(sid: string, origin: 'design' | 'structure', enabled: boolean) {
  const qc = useQueryClient()
  const submit = useMutation({
    mutationFn: (request: BlueprintRefinementRequest) => api.submitBlueprintRefinement(sid, request),
    onSuccess: async (doc) => {
      await qc.cancelQueries({ queryKey: qk.approvals(doc.sid), exact: true })
      qc.setQueryData(qk.approvals(doc.sid), doc)
      await invalidateBookReadModels(qc, doc.sid, { currentKeys: [qk.approvals(doc.sid)] })
      void qc.invalidateQueries({ queryKey: qk.run(doc.sid) })
    },
  })
  const query = useQuery({
    queryKey: qk.blueprintRefinement(sid, origin),
    queryFn: () => api.getBlueprintRefinementCatalog(sid, origin),
    enabled: !!sid && enabled && !submit.isSuccess,
    staleTime: 0,
  })
  return { query, submit }
}
