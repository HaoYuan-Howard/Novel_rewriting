import type { CheckKind } from '@/types/stages'
import type { JobRequest } from '@/types/jobs'

/**
 * 所有 query key 都以 sid 开头（除了语料库与 enums）。
 * 这一条让「闸门不能是全局的」变成结构性保证：换一本书 = 换一条缓存记录，
 * 不存在 A 书的 ✓ 能漏到 B 书的代码路径。
 */
export const qk = {
  novels: () => ['novels'] as const,
  novel: (sid: string) => ['novel', sid] as const,
  run: (sid: string) => ['run', sid] as const,
  enums: () => ['enums'] as const,

  stage0Report: (sid: string) => ['stage0', sid, 'report'] as const,
  stage0Segments: (sid: string) => ['stage0', sid, 'segments'] as const,
  stage0Candidate: (sid: string) => ['stage0', sid, 'candidate'] as const,
  stage1: (sid: string) => ['stage1', sid] as const,
  reading: (sid: string, o: number) => ['reading', sid, o] as const,
  global: (sid: string, spoilers = false) => ['global', sid, spoilers] as const,
  globalReview: (sid: string) => ['globalReview', sid] as const,
  style: (sid: string) => ['style', sid] as const,
  stage4: (sid: string, o: number) => ['stage4', sid, o] as const,
  stage5: (sid: string, o: number) => ['stage5', sid, o] as const,
  review: (sid: string, o: number) => ['review', sid, o] as const,
  inbox: (sid: string) => ['inbox', sid] as const,
  stage6: (sid: string) => ['stage6', sid] as const,
  stage7: (sid: string) => ['stage7', sid] as const,
  stage7ch: (sid: string, o: number) => ['stage7', sid, o] as const,
  stage7book: (sid: string) => ['stage7', sid, 'book'] as const,
  check: (sid: string, kind: CheckKind) => ['check', sid, kind] as const,

  jobs: (sid: string) => ['jobs', sid] as const,
  jobLog: (id: string) => ['jobLog', id] as const,
  jobPreview: (req: JobRequest | null) =>
    [
      'jobPreview',
      req?.kind ?? null,
      req?.extra ?? null,
      req?.args ?? null,
      req?.sid ?? null,
      req?.ordinal ?? null,
    ] as const,

  agentRuns: (sid: string) => ['agentRuns', sid] as const,
  /** 队列是**全 base 一份**，不带 sid：它回答的是「这台机器上现在跑着几本」。 */
  agentQueue: () => ['agentQueue'] as const,
  agentRunLog: (id: string) => ['agentRunLog', id] as const,
  agentRunPreview: (sid: string, authorized: boolean, egressAuthorized: boolean) =>
    ['agentRunPreview', sid, authorized, egressAuthorized] as const,

  approvals: (sid: string) => ['approvals', sid] as const,
}
