import type { Freshness } from '@/constants/vocabulary'
import type { Provenance, StaleRef } from './common'

export interface Artifact {
  stage: string
  name: string
  exists: boolean
  bytes: number
  /** unix 秒 */
  mtime: number
  provenance: Provenance
  freshness: Freshness
  stale: StaleRef[]
}

export interface NovelRow {
  sid: string
  title: string
  dir: string
  dirname: string
  chapters: number
  /** 只有 stage1/2/3/4/6/7 六个 key —— stage5 成稿不进产物表 */
  stages: Record<string, Artifact[]>
  /** stage5 只记跑过哪几条臂。以 _ 开头的是目录名泄漏，显示时要过滤掉 */
  stage5_modes: string[]
  any_stale: boolean
  any_broken: boolean
}

export interface NovelsResponse {
  base: string
  novels: NovelRow[]
}

/** /api/novels/:sid 的响应就是一条 NovelRow */
export type NovelOverview = NovelRow
