import type { Freshness } from '@/constants/vocabulary'
import type { Provenance, StaleRef } from './common'

export interface Artifact {
  stage: string
  name: string
  exists: boolean
  bytes: number | null
  /** unix 秒 */
  mtime: number | null
  /** 总览只发摘要，产物未读或无来源账时可为空；不是正式 artifact 的 `_provenance`。 */
  provenance: Partial<Provenance>
  freshness: Freshness
  display_freshness: Freshness | 'missing'
  condition: 'missing' | 'ready' | 'invalid' | 'unreadable'
  stale: StaleRef[]
  /** 真实服务的新载体字段；旧 mock 没有时保持可选。 */
  reason?: string
  ghosts?: string[]
  invalid?: boolean
  readable?: boolean
}

export interface NovelRow {
  sid: string
  title: string
  dir: string
  dirname: string
  /** stage1 主正文一落盘即可确定；还没分章时为 null。 */
  chapters: number | null
  /** stage0~7；Stage5 干净章只进 chapters_done，artifacts 最多携带章级问题投影。 */
  stages: Record<string, Artifact[]>
  /** 服务端折叠出的三态完成度。 */
  stage_done: Record<string, boolean | null>
  /** 人工流程导航是否可进入；不是运行闸门。 */
  stage_reviewable: Record<string, boolean>
  /** 各层已落盘的 canonical 逐章产物；现役仅 stage5 非空。 */
  chapters_done: Record<string, number[]>
  summary: {
    verdict: 'broken' | 'legacy_incompatible' | 'stale' | 'pending' | 'unknown' | 'complete'
    text: string
    detail: string
    artifact_count: number
    latest_mtime: number | null
  }
}

export interface NovelsResponse {
  base: string
  novels: NovelRow[]
}

export interface TrashNovelResult {
  sid: string
  title: string
  trash_id: string
  trash_rel: string
}

/** /api/novels/:sid 的响应就是一条 NovelRow */
export type NovelOverview = NovelRow
