import type { PrepareState } from '@/types/prepare'

/** 当前契约的明确未运行状态；不为历史样例补造准备接受。 */
export function missingPrepareState(): PrepareState {
  return { state: 'missing', reason: '尚未生成文案准备', subject_sha256: null,
    review_token: null, prepare: null, review: null, pending_commit: false }
}
