import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { MASKED_SECRET_ID } from '@/api/spoilers'
import { MUTED, SPOILER_ON } from '@/constants/vocabulary'
import { useEnums } from '@/hooks/useEnums'
import { useSampleCoverage } from '@/hooks/useSample'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { EmptyIsSuspicious } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { cx } from '@/lib/cx'
import type { GlobalTimeWindow } from '@/types/stages'
import { CharactersPanel } from './CharactersPanel'
import { CoreEvidence, SharedAnalysisSource } from './CoreEvidence'

type Tab = 'setting' | 'rels' | 'rules' | 'secrets'

function TimeWindow({ value }: { value: GlobalTimeWindow }) {
  return <span className="text-mini text-ink-4">
    成立：第 {value.effective_from} 章起{value.effective_until === null ? '，未声明截止章' : `，至第 ${value.effective_until} 章（含）`}
    {' · '}读者第 {value.revealed_from} 章起可见
  </span>
}

/**
 * Stage2 语义审阅已退役：本屏只做只读监控。
 */
export function GlobalScreen() {
  const sid = useSid()
  const nav = useNavigate()
  const { defaultOrdinal } = useSampleCoverage()
  const [spoilers, setSpoilers] = useState(false)
  const [tab, setTab] = useState<Tab>('setting')

  const q = useQuery({
    queryKey: qk.global(sid, spoilers),
    queryFn: () => api.getGlobal(sid, spoilers),
    retry: false,
  })
  const enums = useEnums()
  const maskNote =
    enums.data?.spoiler_disclosure?.notice ??
    '仅在屏幕展示层隐藏跨章秘密；服务端仍全量下发，关系、规则及浏览器网络与缓存仍可能透露相同信息。'

  return (
    <ScreenFrame screen="global" onJumpChapter={(n) => nav('/n/' + sid + '/read/' + n)}>
      <section className="flex flex-col gap-16 px-30 py-24">
        <header className="flex items-start justify-between gap-24">
          <div className="flex flex-col gap-5">
            <h1 className="m-0 text-screen font-semibold">全局设定 · stage2</h1>
            <div className="text-body text-ink-3">
              独立人物登记，以及背景、世界规则、长期关系和跨章秘密。
            </div>
          </div>
          <div className="flex max-w-[620px] flex-col items-end gap-7">
            <button
              type="button"
              onClick={() => setSpoilers((value) => !value)}
              className={cx(
                'flex cursor-pointer items-center gap-8 rounded-btn border px-12 py-6 text-body',
                !spoilers && 'border-line-btn bg-card',
              )}
              style={
                spoilers
                  ? { borderColor: SPOILER_ON.border, background: SPOILER_ON.bg }
                  : undefined
              }
            >
              <span
                aria-hidden
                className="inline-block h-9 w-9 rounded-full"
                style={{ background: spoilers ? SPOILER_ON.dot : MUTED }}
              />
              隐藏核心剧透字段（非完整遮蔽）：{spoilers ? '已关闭' : '已开启'}
            </button>
            <div className="flex items-start gap-7 text-right" role="note">
              <span
                aria-hidden
                className="mt-4 inline-block h-10 w-10 flex-none bg-warn"
                style={{ clipPath: 'polygon(50% 0,100% 100%,0 100%)' }}
              />
              <span className="prose-cn text-tag text-warn-ink">{maskNote}</span>
            </div>
          </div>
        </header>
        <p className="text-body text-ink-3" role="note">
          Stage2 语义未审：机械检查不证明人物完整、主张获原文支持或剧情抽取正确。本屏只读。
        </p>
        <CharactersPanel key={sid} sid={sid} />
        <div className="flex flex-wrap items-center gap-10">
          <LinkButton onClick={() => nav('/n/' + sid + '/read/' + defaultOrdinal(sid))}>
            打开逐章对照
          </LinkButton>
        </div>
        <QueryStates q={q} loadingLabel="读全局设定" missing={{ title: '全局设定 · stage2' }}>
        {(d) => {
          const tabs: { key: Tab; label: string; n: number | null }[] = [
            { key: 'setting', label: '背景', n: null },
            { key: 'rels', label: '长期关系', n: d.relations.length },
            { key: 'rules', label: '世界规则', n: d.world_rules.length },
            { key: 'secrets', label: '跨章秘密', n: spoilers ? d.arc_secrets.length : null },
          ]
          const evidence = (refs: string[]) => <CoreEvidence refs={refs} anchors={d.evidence_anchors} snippets={d.evidence_snippets} />
          const settingRows = [
            { label: '时代背景', value: d.setting.era, refs: d.setting.era_evidence_refs },
            { label: '社会背景', value: d.setting.social_context, refs: d.setting.social_context_evidence_refs },
          ]
          const empty = (label: string) => <EmptyIsSuspicious title={`未提取到${label}`}
            body="空列表仍需结合全书检查完整性，不能自动等同于原文没有。"
            action={<LinkButton onClick={() => nav('/n/' + sid + '/read/' + defaultOrdinal(sid))}>对着原文看一遍</LinkButton>} />
          return (
            <div className="flex flex-col gap-16">
              <SharedAnalysisSource provenance={d._provenance} />
              <div className="text-mini text-ink-4">
                当前展示：{d.invalid_exists ? '问题稿' : '正式稿'} · 正式稿状态：{d.freshness} · {d.reason || '未提供原因'}
                {d.invalid_exists ? ' · 问题稿须修复后再接受' : ''}
              </div>
              <div className="flex flex-wrap gap-8" role="tablist" aria-label="全局长期约束">
                {tabs.map((t) => <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                  onClick={() => setTab(t.key)} className={cx('cursor-pointer rounded-btn-lg border px-14 py-7 text-body-lg',
                    tab === t.key ? 'border-ink bg-ink text-card' : 'border-line-btn bg-card text-ink-2')}>
                  {t.label}{t.n === null ? null : <Mono className="ml-7 text-mini">{t.n}</Mono>}
                </button>)}
              </div>
              {tab === 'setting' ? <div className="grid gap-12 lg:grid-cols-2">
                {settingRows.map((row) => <Panel key={row.label} className="flex flex-col gap-5 px-18 py-14">
                  <span className="text-tag text-ink-4">{row.label}</span>
                  <span className="prose-cn text-section text-ink-2">{row.value ?? '源文未建立'}</span>
                  {evidence(row.refs)}
                </Panel>)}
              </div> : null}
              {tab === 'rels' ? <div className="flex flex-col gap-12">
                {d.relations.length === 0 ? empty('长期关系') : d.relations.map((row) => <Panel key={row.relation_id} className="flex flex-col gap-6 px-18 py-14">
                  <Mono className="text-mini text-ink-5">{row.relation_id}</Mono>
                  <span className="prose-cn text-section">{row.subject.display} · {row.predicate} · {row.object.display}</span>
                  <span className="text-mini text-ink-5">{row.subject.raw} → {row.object.raw}</span>
                  <TimeWindow value={row} />
                  {evidence(row.evidence_refs)}
                </Panel>)}
              </div> : null}
              {tab === 'rules' ? <div className="flex flex-col gap-12">
                {d.world_rules.length === 0 ? empty('世界规则') : d.world_rules.map((rule) => <Panel key={rule.rule_id} className="flex flex-col gap-10 px-18 py-14">
                  <span className="text-tag text-ink-4">{rule.rule_id} · {rule.kind}</span>
                  {rule.versions.map((version) => <div key={version.version_id} className="flex flex-col gap-4 border-t border-line-soft pt-8">
                    <Mono className="text-mini text-ink-5">{version.version_id}</Mono>
                    <span className="prose-cn text-section">{version.desc}</span>
                    <TimeWindow value={version} />
                    {evidence(version.evidence_refs)}
                  </div>)}
                </Panel>)}
              </div> : null}
              {tab === 'secrets' ? <div className="flex flex-col gap-12">
                {d.arc_secrets.length === 0 ? empty('跨章秘密') : d.arc_secrets.map((secret) => <Panel key={secret.secret_id} className="flex flex-col gap-6 px-18 py-14">
                  {secret.secret_id === MASKED_SECRET_ID ? <span className="text-body text-ink-4">{secret.fact}</span> : <>
                    <Mono className="text-mini text-ink-5">{secret.secret_id}</Mono>
                    {secret.fact_ref === null
                      ? <><span className="prose-cn text-section">{secret.fact}</span>{evidence(secret.evidence_refs)}</>
                      : <span className="text-body">引用{secret.fact_ref.kind === 'relation'
                        ? `关系 ${secret.fact_ref.relation_id}`
                        : `世界规则 ${secret.fact_ref.rule_id} / ${secret.fact_ref.version_id}`}；事实与证据见对应主体。</span>}
                    <span className="text-tag text-ink-4">读者第 {secret.revealed_from} 章首次揭示</span>
                    <span className="text-mini text-ink-4">{secret.setup_chapters.length
                      ? `埋点：第 ${secret.setup_chapters.join('、')} 章；同章情节先后见校验报告`
                      : '冷揭示（未登记埋点）'}</span>
                  </>}
                </Panel>)}
              </div> : null}
            </div>
          )
        }}
        </QueryStates>
      </section>
    </ScreenFrame>
  )
}
