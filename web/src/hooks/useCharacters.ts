import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { CharactersEditRequest } from '@/types/characters'
import { invalidateBookReadModels } from './bookReadModels'

export function useCharacters(sid: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.characters(sid),
    queryFn: () => api.getCharacters(sid),
    enabled,
    retry: false,
  })
}

export function useEditCharacters(sid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (request: CharactersEditRequest) => api.editCharacters(sid, request),
    // 失败作业也可能已落 INVALID；实际结果只从后端重读，不能按 HTTP 200 猜通过。
    onSettled: async () => {
      await invalidateBookReadModels(qc, sid)
      await qc.invalidateQueries({ queryKey: qk.run(sid) })
      await qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })
}
