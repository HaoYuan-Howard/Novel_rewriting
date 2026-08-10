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
  enums: () => ['enums'] as const,

  stage0: (sid: string) => ['stage0', sid] as const,
  s1report: (sid: string) => ['s1report', sid] as const,
  s1chapters: (sid: string) => ['s1chapters', sid] as const,
  s1hook: (sid: string) => ['s1hook', sid] as const,
  reading: (sid: string, o: number) => ['reading', sid, o] as const,
  global: (sid: string) => ['global', sid] as const,
  style: (sid: string) => ['style', sid] as const,
  stage4: (sid: string, o: number) => ['stage4', sid, o] as const,
  stage5: (sid: string, o: number) => ['stage5', sid, o] as const,
  review: (sid: string, o: number) => ['review', sid, o] as const,
  inbox: (sid: string) => ['inbox', sid] as const,
  stage6: (sid: string) => ['stage6', sid] as const,
  stage7: (sid: string) => ['stage7', sid] as const,
  stage7ch: (sid: string, o: number) => ['stage7', sid, o] as const,
  check: (sid: string, kind: CheckKind) => ['check', sid, kind] as const,

  jobs: (sid: string) => ['jobs', sid] as const,
  jobLog: (id: string) => ['jobLog', id] as const,
  jobPreview: (req: JobRequest | null) =>
    ['jobPreview', req?.kind ?? null, req?.extra ?? null, req?.args ?? null, req?.sid ?? null, req?.ordinal ?? null] as const,

  approvals: (sid: string) => ['approvals', sid] as const,
}
