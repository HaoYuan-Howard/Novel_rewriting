import type { KnowledgeLinkReview } from '@/types/knowledgeLinks'

/** 无知识事实的显式界面测试输入；不用于 mock API 或生产审阅。 */
export function emptyKnowledgeReview(): KnowledgeLinkReview {
  return {
    scope: {
      contract: 'stage2-knowledge-links-v2', chapter_ordinal: null, scope_sha256: 'a'.repeat(64),
      main_text_sha256: 'b'.repeat(64), knowledge_sha256: 'c'.repeat(64),
      blueprint_sha256: 'd'.repeat(64), obligations: [],
    },
    inherited: [], pending_obligation_ids: [],
  }
}
