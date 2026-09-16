import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'

export function useCharacters(sid: string, enabled = true) {
  return useQuery({
    queryKey: qk.characters(sid),
    queryFn: () => api.getCharacters(sid),
    enabled,
    retry: false,
  })
}
