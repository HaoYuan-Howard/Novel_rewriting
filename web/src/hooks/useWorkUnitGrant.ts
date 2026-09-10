import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { WorkUnitGrantRequest } from '@/types/run'

export function useWorkUnitGrant(sid: string, unitId: string, additional: number, enabled: boolean) {
  return useQuery({
    queryKey: qk.workUnitGrant(sid, unitId, additional),
    queryFn: () => api.previewWorkUnitGrant(sid, unitId, additional),
    enabled,
    staleTime: 0,
    refetchOnMount: 'always',
  })
}

export function useApplyWorkUnitGrant(sid: string, unitId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (request: WorkUnitGrantRequest) => api.grantWorkUnit(sid, unitId, request),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.run(sid) })
      void qc.invalidateQueries({ queryKey: qk.workUnitGrantRoot(sid, unitId) })
    },
  })
}
