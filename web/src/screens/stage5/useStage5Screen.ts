import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo, type Severity } from '@/constants/vocabulary'
import type { Finding } from '@/types/common'
import type { TextPart } from '@/components/ui/HighlightedText'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'
import { useEnums } from '@/hooks/useEnums'

export type S5Tab = 'chapter' | 'book' | 'index'

export interface FindingView {
  key: string
  rule: string
  item: string
  detail: string
  sev: Severity
  evidenceLabel: string
}

/** 把服务端给的证据串在成稿里找出来高亮。**只做 indexOf，不解析、不抠结构。** */
function markParts(text: string, needles: string[]): TextPart[] {
  if (!needles.length) return [{ text }]
  const out: TextPart[] = []
  let rest = text
  let guard = 0
  while (rest && guard < 400) {
    guard += 1
    let at = -1
    let hit = ''
    for (const n of needles) {
      const i = rest.indexOf(n)
      if (i >= 0 && (at < 0 || i < at)) {
        at = i
        hit = n
      }
    }
    if (at < 0) {
      out.push({ text: rest })
      break
    }
    if (at > 0) out.push({ text: rest.slice(0, at) })
    out.push({ text: hit, sev: 'info' })
    rest = rest.slice(at + hit.length)
  }
  return out
}

function paras(t: string): string[] {
  return t.split(/\n+/).filter((x) => x.trim())
}

export function useStage5Screen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const qc = useQueryClient()
  const enums = useEnums()

  const [tab, setTab] = useState<S5Tab>('chapter')
  const [selFinding, setSelFinding] = useState(0)
  const [sideBySide, setSideBySide] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draftText, setDraftText] = useState<string | null>(null)
  const [matOpen, setMatOpen] = useState(false)
  const [chaptersArg, setChaptersArg] = useState(String(ordinal))

  const draftQ = useQuery({
    queryKey: qk.stage5(sid, ordinal),
    queryFn: () => api.getStage5(sid, ordinal),
    retry: false,
  })
  const reviewQ = useQuery({
    queryKey: qk.review(sid, ordinal),
    queryFn: () => api.getReview(sid, ordinal),
    retry: false,
  })

  const save = useMutation({
    mutationFn: (text: string) => api.saveDraftEdit(sid, ordinal, text),
    onSuccess: () => {
      setEditing(false)
      void qc.invalidateQueries({ queryKey: qk.review(sid, ordinal) })
    },
  })

  const d = draftQ.data
  const ch = d?.chapter
  const mat = ch?.materials
  const rv = reviewQ.data

  const findings: Finding[] = rv?.chapter.findings ?? []
  const active = findings[selFinding]
  const firstDraft = ch?.drafts[0] ?? null
  const baseText = rv?.chapter.text ?? firstDraft?.text ?? ''
  const text = draftText ?? baseText

  // 证据在成稿里命中了几处 —— 一处都没命中要明说，不能装作高亮过
  const evHits = (active?.evidence ?? []).map((e) => {
    let n = 0
    let i = 0
    while ((i = baseText.indexOf(e, i)) >= 0) {
      n += 1
      i += e.length
    }
    return { e, n }
  })
  const hitTotal = evHits.reduce((a, b) => a + b.n, 0)
  const live = evHits.filter((x) => x.n > 0).map((x) => x.e)

  const align = firstDraft?.align ?? null
  const lowAnchor = !!align && align.coverage < 0.6

  return {
    draftQ,
    reviewQ,
    sid,
    ordinal,
    enums: enums.data,

    tab,
    setTab,
    selFinding,
    setSelFinding,
    sideBySide,
    toggleSide: () => setSideBySide((v) => !v),
    editing,
    toggleEdit: () => {
      setDraftText(text)
      setEditing((v) => !v)
    },
    text,
    setText: setDraftText,
    save,
    editNote:
      rv?.edit_note ?? '保存走子进程重跑校验，落成新的一轮；旧轮归档，不覆盖。',

    counts: d ? '计划 ' + d.planned + ' · 写了 ' + d.written + ' · 被拦下 ' + d.invalid : '',
    missingLabel: d ? (d.missing.length ? '缺 ' + d.missing.join(',') : '没有缺章') : '',
    missingBad: !!d && d.missing.length > 0,
    invalidLabel: ch
      ? ch.invalid_drafts.length
        ? ch.invalid_drafts.length + ' 份被闸门拦下'
        : '本章没有被拦下的稿'
      : '',
    invalidBad: !!ch && ch.invalid_drafts.length > 0,
    depthNote: d?.depth_note ?? '',
    gateNote: d?.gate_note ?? '',
    /** ctx_exists 为 false 时不显示假材料 */
    ctxExists: d?.ctx_exists ?? true,
    ctxNote: d?.note ?? null,

    index: (d?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      freshness: i.freshness,
      modes: i.modes.join('+'),
      chars: i.chars + '/' + i.budget_chars,
      overlap: Math.round(i.overlap_excl_verbatim * 100) + '%',
      active: i.output_ordinal === ordinal,
    })),

    findings: findings.map<FindingView>((f, i) => ({
      key: f.rule + i,
      rule: f.rule,
      item: f.item,
      detail: f.detail,
      sev: f.sev,
      evidenceLabel: f.evidence.length ? '证据 ' + f.evidence.length + ' 条' : '没有证据串',
    })),
    bookFindings: (rv?.book ?? []).map<FindingView>((f, i) => ({
      key: 'b' + f.rule + i,
      rule: f.rule,
      item: f.item,
      detail: f.detail,
      sev: f.sev,
      evidenceLabel: f.evidence.length ? '证据 ' + f.evidence.length + ' 条' : '没有证据串',
    })),
    reviewIndex: (rv?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      mode: i.mode + ' r' + i.round,
      chars: i.chars + '/' + i.budget_chars,
      hard: i.hard,
      info: i.info,
      unver: i.unver,
      unresolved: i.unresolved,
      active: i.output_ordinal === ordinal,
    })),
    // 41/61 条是 metric —— 四级分开计数，metric 不能画成警告
    sevCounts: (rv?.counts ?? {}) as Partial<Record<Severity, number>>,
    tabLabels: {
      chapter: '本章 ' + findings.length + ' 条',
      book: '全书 ' + (rv?.book.length ?? 0) + ' 条',
      index: '章索引 ' + (rv?.index.length ?? 0),
    },
    findNote: rv
      ? '这几条来自全书验收（review_' +
        rv.review_seq +
        '）：其中 metric 是度量、不是问题。全书级与其他上游层的结论在各自阶段屏的校验面板里。'
      : '',
    hasHard: findings.some((f) => f.sev === 'hard'),

    evidenceHint: active
      ? hitTotal > 0
        ? '「' +
          active.item +
          '」的证据 ' +
          evHits.length +
          ' 条，在成稿里命中 ' +
          hitTotal +
          ' 处，已高亮：' +
          live.join('、')
        : '「' +
          active.item +
          '」的证据 ' +
          evHits.length +
          ' 条，在成稿里一处都没命中：' +
          evHits.map((x) => x.e).join('、') +
          '。这类证据是度量值或 id，不是正文片段。'
      : '',
    evidenceHit: hitTotal > 0,

    matOpen,
    toggleMat: () => setMatOpen((v) => !v),
    matGoal: mat?.chapter_goal ?? '',
    matNumbers: mat
      ? [
          { k: '预算', v: String(mat.budget_chars) },
          { k: '源文', v: String(mat.source_chars) },
          { k: '写手目标', v: mat.writer_target.join('–') },
          { k: 'must_not_reveal', v: String(mat.must_not_reveal_count) },
        ]
      : [],
    matBeats: (mat?.beats ?? []).map((b, i) => ({
      i: i + 1,
      budget: b.budget_chars + ' 字',
      // 这三个字段会是 null（不是空数组）—— null 的意思是「这一拍没标」，不是「没有人」
      stage: [
        b.time_layer === null ? '时间层未标注' : b.time_layer === 'present' ? '现在' : b.time_layer,
        b.place ?? '地点未标注',
        b.present === null ? '在场未标注' : b.present.join('、') || '无人',
      ].join(' · '),
      unlabelled: b.time_layer === null || b.place === null || b.present === null,
      events: b.events,
      // stage5 这一层这些字段缺省是 null（stage4 那边是 []）—— 两套判空
      verbatim: b.verbatim ?? [],
    })),
    matFacts: mat
      ? '另附全套 facts：关系 ' +
        mat.facts.relations.length +
        ' 条 · 世界规则 ' +
        mat.facts.world_rules.length +
        ' 条 · 秘密 ' +
        mat.facts.secrets.length +
        ' 条 · 称呼 ' +
        mat.facts.address.length +
        ' 条 · 人物 ' +
        Object.keys(mat.characters).length +
        ' 人。'
      : '',

    draft: firstDraft
      ? {
          mode: firstDraft.mode,
          title: firstDraft.mode === 'deep' ? '深度 · 闭卷重构' : '浅层 · 开卷改写',
          freshness: firstDraft.freshness,
          numbers:
            firstDraft.chars +
            ' 字 / 预算 ' +
            firstDraft.budget_chars +
            ' · 与原文重合（除逐字保留）' +
            Math.round(firstDraft.overlap_excl_verbatim * 100) +
            '%',
          prov:
            'model ' +
            (firstDraft.provenance.model ?? '—') +
            ' · code ' +
            (firstDraft.provenance.code ?? '—') +
            ' · prompts ' +
            Object.keys(firstDraft.provenance.prompts ?? {}).join(','),
        }
      : null,
    /** 锚点覆盖率常驻显示。没有这个数，「这一行盖了 3 拍」和「这一行就是第 3 拍」长得一样。 */
    anchorLabel: align ? align.anchors + '/' + align.beats + ' 拍' : '',
    anchorLow: lowAnchor,
    anchorColor: lowAnchor ? SEV.unver.color : FRESH.fresh.color,
    anchorBg: lowAnchor ? SEV.unver.bg : FRESH.fresh.bg,
    anchorBorder: lowAnchor ? SEV.unver.border : 'transparent',
    alignMeta: align
      ? '切成 ' + align.rows + ' 块 · 覆盖率 ' + align.coverage + ' · 倒挂 ' + align.inversions
      : '',
    alignNote: align?.note ?? '',

    rows: (firstDraft?.align_rows ?? []).map((r, i) => {
      const multi = r.beats.length > 1
      return {
        key: 'r' + i,
        anchored: !multi && r.anchored,
        badge: multi ? '盖了第 ' + r.beats.join('、') + ' 拍' : '第 ' + r.beats[0] + ' 拍',
        note: multi
          ? '其中只有一拍有锚——其余并进来了，这一块的对应没有验证过'
          : '这一拍自己锚到了',
        meta: '源 ' + r.source_chars + ' 字 → 稿 ' + r.budget_chars + ' 字预算',
        left: r.l,
        right: r.r,
      }
    }),

    paragraphs: paras(text).map((p) => markParts(p, live)),
    sourceParagraphs: paras(ch?.source_text ?? ''),

    chaptersArg,
    setChaptersArg,
    writeActions: (enums.data?.actions ?? []).filter(
      (a) => a.kind === 'n5_write' || a.kind === 'n6_write',
    ),
  }
}
