import type { Freshness } from '@/constants/vocabulary'
import type { Provenance, StaleRef } from './common'

export interface Artifact {
  stage: string
  name: string
  exists: boolean
  bytes: number | null
  /** unix 秒 */
  mtime: number | null
  provenance: Provenance
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
  /** 新服务的 overview 不直接带章数；能从逐章产物确认时填数，否则为 null。 */
  chapters: number | null
  /** stage0~7；stage5 成稿不进 artifacts，只在 stage5_modes / chapters_done 中体现。 */
  stages: Record<string, Artifact[]>
  /** stage5 只记跑过哪几条臂。以 _ 开头的是目录名泄漏，显示时要过滤掉 */
  stage5_modes: string[]
  /** 服务端折叠出的三态完成度。 */
  stage_done: Record<string, boolean | null>
  /** 人工流程导航是否可进入；不是运行闸门。 */
  stage_reviewable: Record<string, boolean>
  /** 逐章层已经落盘的章号，按 mode 分组。 */
  chapters_done: Record<string, Record<string, number[]>>
  summary: {
    verdict: 'broken' | 'stale' | 'pending' | 'unknown' | 'complete'
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

/** /api/novels/:sid 的响应就是一条 NovelRow */
export type NovelOverview = NovelRow
