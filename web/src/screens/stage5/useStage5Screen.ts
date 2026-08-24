import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo, type Severity } from '@/constants/vocabulary'
import type { Finding } from '@/types/common'
import type { ChapterSegment, ReviewResponse, TrimResolutionRequest } from '@/types/stages'
import type { TextPart } from '@/components/ui/HighlightedText'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'
import { useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { lookupAction } from '@/constants/actions'
import type { EnumAction } from '@/types/enums'
import { useApprovals } from '@/hooks/useApprovals'
import { useJobs } from '@/hooks/useJobs'

export type S5Tab = 'chapter' | 'book' | 'index'

export interface FindingView {
  key: string
  rule: string
  item: string
  detail: string
  sev: Severity
  evidenceLabel: string
  wontfixLabel: string | null
}

export type Stage5WontfixScope = number | 'book'

export interface Stage5WontfixTarget {
  key: string
  scope: Stage5WontfixScope
  rule: string
  item: string
  evidenceKey: string | null
  wontfix: Finding['wontfix']
  blockedReason: string | null
}

function stage5FindingKey(
  scope: Stage5WontfixScope,
  finding: Pick<Finding, 'rule' | 'evidence_key'>,
): string | null {
  return finding.evidence_key?.trim()
    ? `${scope}|${finding.rule}|${finding.evidence_key}`
    : null
}

/**
 * Stage5's chapter box can supply only the dynamic --chapters value. Actions
 * needing another primary value (notably --apply-edit's file) have dedicated
 * UI and must not be advertised here as runnable buttons.
 */
export function stage5WriteActions(actions: readonly EnumAction[]): EnumAction[] {
  return actions.filter(
    (action) => action.kind === 'stage5.write'
      && !action.extra?.includes('--rebuild-draft')
      && (!action.needs_value || action.extra?.includes('--chapters')),
  )
}

/**
 * 付费写作在用户确认之后、真正启动作业之前补齐 writing 授权。
 * `undefined` 也按「尚未证明已授权」处理；查询未返回时直接放行，会留下一个
 * 只在慢请求上出现的 409 死路。免费恢复动作永远不走这条状态转换。
 */
export function stage5WritingBefore(
  action: { paid: boolean } | null,
  writing: boolean | null | undefined,
  startWriting: () => Promise<unknown>,
): (() => Promise<void>) | undefined {
  if (!action?.paid || writing === true) return undefined
  return async () => {
    await startWriting()
  }
}

/** 专用 retain 路由与同源 preview 共用的完整 flags；输入先去首尾空白。 */
export function retainPretrimArgs(ordinal: number, actor: string, reason: string): string[] {
  return [
    '--chapters', String(ordinal),
    '--actor', actor.trim(),
    '--reason', reason.trim(),
  ]
}

/**
 * 一次 wontfix 只绑定一条现役结论。证据键即使含 `|` 也原样放在第三段；
 * 服务端只切前两刀，前端不解析或重算 evidence identity。
 */
export function stage5WontfixSpec(
  scope: Stage5WontfixScope,
  rule: string,
  evidenceKey: string | null,
  reason: string,
): string | null {
  if (!rule.trim() || !evidenceKey?.trim() || !reason.trim()) return null
  return `${scope}|${rule}|${evidenceKey}=${reason.trim()}`
}

/** 只让 current fresh、未豁免、且可精确寻址的 finding 进入 wontfix。 */
export function stage5WontfixBlockedReason(
  reviewState: ReviewResponse['review_state'] | undefined,
  finding: Pick<Finding, 'evidence_key' | 'wontfix'> | undefined,
  submitted: boolean,
): string | null {
  if (!finding) return '没有选中一条 finding'
  if (reviewState !== 'fresh') return '只有 fresh Review 的 finding 可以提交 wontfix'
  if (finding.wontfix) return '这条 finding 已经有 wontfix 审计记录'
  if (!finding.evidence_key?.trim()) return '这条 finding 没有 evidence_key，不能精确绑定人工决定'
  if (submitted) return '这条 wontfix 作业已经发起，等待作业结束后刷新报告'
  return null
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
  const approvals = useApprovals(sid)
  const jobs = useJobs(sid)
  const { notify, openDrawer } = useChrome()

  const [tab, setTab] = useState<S5Tab>('chapter')
  const [selFinding, setSelFinding] = useState(0)
  const [selBookFinding, setSelBookFinding] = useState(0)
  const [sideBySide, setSideBySide] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draftSegments, setDraftSegments] = useState<ChapterSegment[] | null>(null)
  const [matOpen, setMatOpen] = useState(false)
  const [chaptersArg, setChaptersArg] = useState(String(ordinal))
  const [trimActor, setTrimActor] = useState('')
  const [trimReason, setTrimReason] = useState('')
  const [wontfixReason, setWontfixReason] = useState('')
  const [submittedWontfix, setSubmittedWontfix] = useState<{
    key: string
    jobId: string
  } | null>(null)

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
    mutationFn: (req: { segments: ChapterSegment[] }) =>
      api.saveDraftEdit(sid, ordinal, req),
    onSuccess: (job) => {
      if (job.state === 'failed') {
        // CLI 内失败不是 HTTP 错误（作业落 state='failed'）——完整输出在抽屉日志里
        openDrawer()
      } else {
        setEditing(false)
        notify('改稿已保存')
      }
      void qc.invalidateQueries({ queryKey: qk.stage5(sid, ordinal) })
      void qc.invalidateQueries({ queryKey: qk.review(sid, ordinal) })
      void qc.invalidateQueries({ queryKey: qk.check(sid, 'stage5') })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const retainPretrim = useMutation({
    mutationFn: (req: TrimResolutionRequest) => api.retainPretrim(sid, ordinal, req),
    onSuccess: (job) => {
      if (job.state === 'failed') {
        openDrawer()
      } else {
        setTrimReason('')
        notify('未裁剪正文已由人工确认保留')
      }
      void qc.invalidateQueries({ queryKey: qk.stage5(sid, ordinal) })
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const d = draftQ.data
  const mat = d?.materials ?? null
  const rv = reviewQ.data

  const findings: Finding[] = rv?.chapter.findings ?? []
  const active = findings[selFinding]
  const bookFindings: Finding[] = rv?.book ?? []
  const activeBook = bookFindings[selBookFinding]
  const wontfixFinding = tab === 'chapter' ? active : tab === 'book' ? activeBook : undefined
  const wontfixScope: Stage5WontfixScope = tab === 'book'
    ? 'book'
    : (rv?.chapter.output_ordinal ?? ordinal)
  const wontfixKey = wontfixFinding ? stage5FindingKey(wontfixScope, wontfixFinding) : null
  const wontfixBlockedReason = stage5WontfixBlockedReason(
    rv?.review_state,
    wontfixFinding,
    wontfixKey !== null && submittedWontfix?.key === wontfixKey,
  )

  // 本地 submitted 只表示「这个真实 job 尚未由服务端收口」。启动成功绝不等于落盘：
  // 成功终态要等同一 evidence identity 的 wontfix 从新 Review 读回来；failed / unknown
  // 则明确没有可采信的落盘结论，解除占位供人查看日志后重试。
  const submittedLanded = !!submittedWontfix && !!rv && [
    ...rv.chapter.findings.map((finding) => ({
      scope: rv.chapter.output_ordinal,
      finding,
    })),
    ...rv.book.map((finding) => ({ scope: 'book' as const, finding })),
  ].some(({ scope, finding }) =>
    !!finding.wontfix && stage5FindingKey(scope, finding) === submittedWontfix.key,
  )
  const submittedJobState = submittedWontfix
    ? jobs.data?.jobs.find((job) => job.id === submittedWontfix.jobId)?.state ?? null
    : null
  useEffect(() => {
    if (!submittedWontfix) return
    const landedAtTerminal = submittedLanded && (
      submittedJobState === 'ok' || submittedJobState === 'landed_red'
    )
    if (!landedAtTerminal && submittedJobState !== 'failed' && submittedJobState !== 'unknown') {
      return
    }
    setSubmittedWontfix((current) =>
      current?.jobId === submittedWontfix.jobId ? null : current,
    )
  }, [submittedJobState, submittedLanded, submittedWontfix])
  const firstDraft = d?.drafts[0] ?? null
  const baseSegments = rv?.chapter.segments ?? firstDraft?.segments ?? []
  const segments = draftSegments ?? baseSegments
  const baseText = rv?.chapter.text ?? firstDraft?.text ?? ''
  const text = segments.length ? segments.map((segment) => segment.text.trim()).join('\n\n') : baseText

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
  const anchorState = align?.coverage_state ?? 'unknown'
  const lowAnchor = anchorState === 'low'
  const unknownAnchor = !!align && anchorState === 'unknown'

  /** 编辑中且草稿与基准不一致 —— 这时退出编辑 / 跳章会先挽留一句 */
  const dirty =
    !!draftSegments && JSON.stringify(draftSegments) !== JSON.stringify(baseSegments)
  /** true = 可以放心离开（没改动，或本人在原生确认里点了确定） */
  const guardLeave = () => !dirty || window.confirm('改稿还没保存，确定离开？')

  return {
    draftQ,
    reviewQ,
    sid,
    ordinal,
    enums: enums.data,

    tab,
    setTab: (next: S5Tab) => {
      setTab(next)
      setWontfixReason('')
    },
    selFinding,
    setSelFinding: (index: number) => {
      setSelFinding(index)
      setWontfixReason('')
    },
    selBookFinding,
    setSelBookFinding: (index: number) => {
      setSelBookFinding(index)
      setWontfixReason('')
    },
    sideBySide,
    toggleSide: () => setSideBySide((v) => !v),
    editing,
    dirty,
    guardLeave,
    toggleEdit: () => {
      // 有未保存的改动时退出编辑先挽留；取消则留在编辑态
      if (editing && !guardLeave()) return
      setEditing((wasEditing) => {
        if (!wasEditing) {
          setDraftSegments(baseSegments.map((segment) => ({ ...segment })))
        }
        return !wasEditing
      })
    },
    text,
    editSegments: segments,
    setSegmentText: (slot: string, value: string) =>
      setDraftSegments((current) =>
        (current ?? baseSegments).map((segment) =>
          segment.slot === slot ? { ...segment, text: value } : segment,
        ),
      ),
    save,
    editNote: '保存走子进程重跑校验，落成新的一轮；旧轮归档，不覆盖。',
    trimAudit: d?.trim_audit ?? null,
    trimActor,
    setTrimActor,
    trimReason,
    setTrimReason,
    retainPretrim,

    counts: d
      ? '计划 ' +
        (d.planned ?? '判不了') +
        ' · 写了 ' +
        d.written +
        ' · 被拦下 ' +
        d.invalid
      : '',
    missingLabel: d ? (d.missing.length ? '缺 ' + d.missing.join(',') : '没有缺章') : '',
    missingBad: !!d && d.missing.length > 0,
    invalidLabel: d
      ? d.invalid_drafts.length
        ? d.invalid_drafts.length + ' 份被闸门拦下'
        : '本章没有被拦下的稿'
      : '',
    invalidBad: !!d && d.invalid_drafts.length > 0,
    gateNote: 'Stage5 只有全书闭卷写作路径；定向章号只缩小本次写入集合，不改变契约。',
    legacyNotice: d?.legacy_incompatible
      ? '检测到旧 Stage5 双臂产物，当前契约不会读取或迁移：' + d.legacy_paths.join('、')
      : null,
    reviewNotice: rv && rv.review_state !== 'fresh'
      ? '验收报告不可用于返工或批准（' + rv.review_state + '）：' + rv.review_reasons.join('；')
      : null,
    /** ctx_exists 为 false 时不显示假材料 */
    ctxExists: d?.ctx_exists ?? true,
    ctxNote: d && !d.ctx_exists ? '写作上下文不在盘上——先跑 stage4，这里不显示假材料。' : null,

    index: (d?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      freshness: i.freshness,
      status: i.has_draft ? (i.invalid ? '正式稿 + INVALID' : '已写') : i.invalid ? '仅 INVALID' : '未写',
      chars: (i.chars ?? '—') + '/' + (i.budget_chars ?? '—'),
      overlap:
        i.overlap_excl_verbatim === null
          ? '—'
          : Math.round(i.overlap_excl_verbatim * 100) + '%',
      active: i.output_ordinal === ordinal,
    })),

    findings: findings.map<FindingView>((f, i) => ({
      key: f.rule + i,
      rule: f.rule,
      item: f.item,
      detail: f.detail,
      sev: f.sev,
      evidenceLabel: f.evidence.length ? '证据 ' + f.evidence.length + ' 条' : '没有证据串',
      wontfixLabel: f.wontfix ? '已 wontfix：' + f.wontfix.why : null,
    })),
    bookFindings: bookFindings.map<FindingView>((f, i) => ({
      key: 'b' + f.rule + i,
      rule: f.rule,
      item: f.item,
      detail: f.detail,
      sev: f.sev,
      evidenceLabel: f.evidence.length ? '证据 ' + f.evidence.length + ' 条' : '没有证据串',
      wontfixLabel: f.wontfix ? '已 wontfix：' + f.wontfix.why : null,
    })),
    reviewIndex: (rv?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      roundLabel: i.round ? 'r' + i.round : '无稿',
      chars: (i.chars ?? '—') + '/' + (i.budget_chars ?? '—'),
      hard: i.finding_counts.hard,
      info: i.finding_counts.info,
      unver: i.finding_counts.unver,
      unresolved: i.unresolved,
      stale: i.stale,
      active: i.output_ordinal === ordinal,
    })),
    // 四级分开计数，metric 只报读数、不能画成警告
    sevCounts: (rv?.finding_counts ?? {}) as Partial<Record<Severity, number>>,
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

    wontfixTarget: wontfixFinding
      ? {
          key: wontfixKey ?? `${wontfixScope}|${wontfixFinding.rule}|<missing-evidence-key>`,
          scope: wontfixScope,
          rule: wontfixFinding.rule,
          item: wontfixFinding.item,
          evidenceKey: wontfixFinding.evidence_key,
          wontfix: wontfixFinding.wontfix,
          blockedReason: wontfixBlockedReason,
        } satisfies Stage5WontfixTarget
      : null,
    wontfixReason,
    setWontfixReason,
    afterWontfix: (key: string, jobId: string) => {
      setSubmittedWontfix({ key, jobId })
      setWontfixReason('')
      void qc.invalidateQueries({ queryKey: qk.review(sid, ordinal) })
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },

    matOpen,
    toggleMat: () => setMatOpen((v) => !v),
    /** 与写手同一份下发面（build_material 的 blocks）——不是重组的第二份 */
    matBlocks: mat?.blocks ?? [],
    matNumbers: mat
      ? [
          {
            k: '写手目标',
            v: mat.writer_target.join('–'),
          },
          { k: '下发面', v: mat.chars + ' 字' },
          { k: '源章原文', v: (d?.source_chars_raw ?? '—') + ' 字' },
        ]
      : [],

    draft: firstDraft
      ? {
          title: '闭卷重构',
          freshness: firstDraft.freshness,
          numbers:
            firstDraft.chars +
            ' 字 / 预算 ' +
            firstDraft.budget_chars +
            ' · 与原文重合（除逐字保留）' +
            (firstDraft.overlap_excl_verbatim === null
              ? '量不了'
              : Math.round(firstDraft.overlap_excl_verbatim * 100) + '%'),
          prov:
            'actual model ' +
            (firstDraft._provenance.adopted_models?.join(' + ') ??
              firstDraft._provenance.model ??
              '—') +
            ' · code ' +
            (firstDraft._provenance.code ?? '—') +
            ' · prompts ' +
            Object.keys(firstDraft._provenance.prompts ?? {}).join(','),
        }
      : null,
    /** 锚点覆盖率常驻显示。没有这个数，「这一行盖了 3 拍」和「这一行就是第 3 拍」长得一样。 */
    anchorLabel: align ? align.anchors + '/' + align.beats + ' 拍' : '',
    anchorLow: lowAnchor || unknownAnchor,
    anchorColor: lowAnchor
      ? SEV.unver.color
      : unknownAnchor
        ? FRESH.unknown.color
        : FRESH.fresh.color,
    anchorBg: lowAnchor ? SEV.unver.bg : unknownAnchor ? FRESH.unknown.bg : FRESH.fresh.bg,
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
        meta: '原文 ' + [...r.l].length + ' 字 → 成稿 ' + [...r.r].length + ' 字',
        left: r.l,
        right: r.r,
      }
    }),

    paragraphs: paras(text).map((p) => markParts(p, live)),
    sourceParagraphs: paras(d?.source_text ?? ''),

    chaptersArg,
    setChaptersArg,
    writeActions: stage5WriteActions(enums.data?.actions ?? []),
    fullWriteAction: lookupAction(enums.data, { kind: 'stage5.write', extra: null }),
    approvals,
    writingBefore: (action: { paid: boolean } | null) =>
      stage5WritingBefore(
        action,
        approvals.doc?.writing,
        () => approvals.startWriting.mutateAsync(),
      ),
    /**
     * 全书验收动作。**它不在 `writeActions` 里，那是对的**——那个盒子只喂
     * `--chapters`，而本条是书级的。但它必须在这一屏有自己的入口：
     * 没跑过时后端原话就是「盘上一份 `stage5/review_*.json` 都没有——先跑
     * `stage5.review`」，而在此之前**整个界面都没有地方能跑它**，
     * 浏览器里的人到这一步只能改去命令行。
     */
    reviewAction: lookupAction(enums.data, { kind: 'stage5.review', extra: null }),
    wontfixAction: lookupAction(enums.data, {
      kind: 'stage5.review',
      extra: ['--wontfix'],
    }),
    retainPretrimAction: lookupAction(enums.data, {
      kind: 'stage5.review',
      extra: ['--retain-pretrim'],
    }),
    rebuildDraftAction: lookupAction(enums.data, {
      kind: 'stage5.write',
      extra: ['--rebuild-draft'],
    }),
  }
}
