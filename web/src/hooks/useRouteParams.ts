import { useParams } from 'react-router-dom'

/** 当前这本书。永远来自 URL —— 这就是「闸门不能是全局的」的结构保证。 */
export function useSid(): string {
  const { sid } = useParams<{ sid: string }>()
  return sid ?? ''
}
