/** 必要知识 V2：引用已有主体，时点选择来自本次工作包。 */
export type {
  KnowledgeLinkDecision, KnowledgeLinkSubmission,
  KnowledgeLinkRecord, KnowledgeLinkReview,
} from './plot.generated'
export type KnowledgeLinkResolution = import('./plot.generated').KnowledgeLinkDecision['resolution']
