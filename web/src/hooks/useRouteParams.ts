import { useParams } from 'react-router-dom'

/** 当前这本书。永远来自 URL —— 这就是「闸门不能是全局的」的结构保证。 */
export function useSid(): string {
  const { sid } = useParams<{ sid: string }>()
  return sid ?? ''
}

/** 当前章号（1 起）。只有带 /:o 的路由会用。 */
export function useOrdinal(): number {
  const { o } = useParams<{ o: string }>()
  const n = Number(o)
  return Number.isFinite(n) && n > 0 ? n : 1
}
