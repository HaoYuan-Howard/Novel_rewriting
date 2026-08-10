import { SEV, type Severity } from '@/constants/vocabulary'

/**
 * 带标注的文本。
 *
 * props 里**没有 start / end** —— 这是故意的：
 * 服务端给的 start/end 是 Python 码点下标，而 JS 字符串是 UTF-16 码元。
 * 纯中文两者重合，但网文常带 emoji，出现一个就让其后所有偏移静默错位。
 * 服务端已经把切好的片段一起发过来了，直接用。
 *
 * 类型里没有下标，这条坑就踩不进去。
 */
export interface TextPart {
  text: string
  /** 有 sev 的片段才上色；没有就是普通正文 */
  sev?: Severity
  rule?: string
}

export function HighlightedText({
  parts,
  className,
}: {
  parts: TextPart[]
  className?: string
}) {
  return (
    <span className={className}>
      {parts.map((p, i) =>
        p.sev ? (
          <mark
            key={i}
            title={p.rule}
            className="rounded-[3px] px-2"
            style={{
              color: SEV[p.sev].color,
              background: SEV[p.sev].bg,
              boxShadow: 'inset 0 -1px 0 ' + SEV[p.sev].border,
            }}
          >
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </span>
  )
}
