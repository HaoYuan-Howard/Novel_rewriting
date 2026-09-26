/**
 * 跨 endpoint 复用的形状。字段名一律照抄后端响应，不改名、不压平嵌套。
 * 权威：`contracts/schema/api.py` 与 `contracts/schema/report.py`
 * （backend_v1 fixture 是抓包证据）。
 *
 * ⚠ STR-06 切换（F8c）后只剩校验报告一族与总览 artifact 还在用：Stage2 全局
 * 设定的 `NameRef`、`writer_context` 来源账与 Stage5/7 读屏的 `Sources` 随旧链
 * 读屏整层退役，本文件不为它们保留第二个类型落点。
 */
import type { Severity } from '@/constants/vocabulary'

/** 过期原因的一条边（总览 artifact 与工作单元账的 detail）。 */
export interface StaleRef {
  key: string
  was: string | null
  now: string | null
}

/** 来源记录。形状随 stage 而异，`{}` 很常见 —— 空对象即「没有来源记录」。 */
export interface Provenance {
  root_clean_sha1: string
  shared_analysis_source?: {
    receipt_rel: string
    receipt_sha256: string
    run_id: string
    attempt_id: string
  } | null
  model?: string
  adopted_models?: string[]
  code?: string
  round?: number
  origin?: string
  prompts?: Record<string, string>
  inputs?: Record<string, unknown>
  [k: string]: unknown
}

/**
 * 一条判据结论（后端 `Finding` 十五字段，键恒在）。
 * `wontfix` 非空 = 有人看过并豁免 —— 结论不删、单独计数。
 */
export interface Finding {
  rule: string
  sev: Severity
  item: string
  output_ordinal: number | null
  detail: string
  evidence: string[]
  fix_args: Record<string, string>
  evidence_key: string | null
  owner: string | null
  actionable: boolean
  identity_hit: boolean
  identity_dead: boolean
  converge: string | null
  unresolved_rounds: number | null
  wontfix: { why: string; at: string } | null
}

/** 一条能修它的动作（服务端下发，前端不拼 argv）。没有它就不画「重跑修它」。 */
export interface FixRoute {
  node: string
  flags: string[]
  paid: boolean
  available: boolean
  unavailable_reason: string | null
}

/**
 * 校验报告的一行（`CheckReport.rules[]`，`contracts/schema/report.py::RuleLine`）。
 * 两轴在此：`check_kind`（结论怎么来）× `remediation`（谁能修），与 `sev`/`status` 正交。
 */
export interface RuleLine {
  rule: string
  sev: Severity
  status: 'ok' | 'hit' | 'unver' | 'skipped'
  item: string
  requirement: string
  check_kind: 'mechanical' | 'semantic'
  remediation: 'llm' | 'human' | 'deterministic' | 'none'
  fix: FixRoute | null
  findings: Finding[]
  why: string | null
  scanned: string | null
  baseline: string | null
  note: string | null
}
