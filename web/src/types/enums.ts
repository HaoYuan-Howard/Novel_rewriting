/**
 * /api/enums —— 除 vocabulary.ts 里那几个核心枚举外，其余枚举一律运行时从这里取，
 * 不硬编码进前端。后端改了枚举，前端不该跟着改代码。
 */

export interface EnumAction {
  kind: string
  label: string
  /** CLI 附加参数。命令行按这个拼，不按 free_flag。 */
  extra: string[] | null
  /** 花不花钱由服务端算好下发。36 个动作里只有 7 个是 true。 */
  paid: boolean
  /** 免费动作的 flag 串（可能是空串）；paid 的一律是 null。 */
  free_flag: string | null
  mode?: string | null
  writes?: string
  writes_disk?: boolean
  needs_value?: string | null
}

export interface EnumJobKind {
  label: string
  paid: boolean
  stage?: string
  section?: string
  modes?: string[]
  flags?: string[]
  produces?: string[]
}

export interface EnumsResponse {
  hinge_kinds: string[]
  nonbody_kinds: string[]
  time_layers: string[]
  hinge_groups: Record<string, string>
  style_words: string[]
  invent: { allow: string[]; forbid: string[]; principle: string }
  voice_keys: string[]
  hook_keys: string[]
  job_kinds: Record<string, EnumJobKind>
  actions: EnumAction[]
  uncovered_min_chars: number
  /**
   * This is an honest display boundary, not an access-control boundary: the server still
   * sends the complete stage2 document and the adapter projects it before a Screen sees it.
   */
  spoiler_disclosure: {
    mode: 'display_only'
    security_boundary: false
    server_sends_full_data: true
    hidden_paths: string[]
    known_leakage_surfaces: string[]
    consumers: string[]
    notice: string
  }
  /** Compatibility with frozen v1 fixtures; new servers publish spoiler_disclosure. */
  partial_mask_note?: string
}
