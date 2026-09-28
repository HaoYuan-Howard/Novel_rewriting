import { useParams } from 'react-router-dom'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader, SectionTitle } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { LoadFailed } from '@/components/state/States'
import { useStr06Monitor } from '@/hooks/useStr06Monitor'
import type {
  Str06AssemblyReportView,
  Str06BodyFailuresView,
  Str06OwnerIssuesView,
  Str06RunView,
} from '@/types/str06'

type Monitor = ReturnType<typeof useStr06Monitor>

/**
 * STR-06 候选链只读监控屏（F7d）。
 *
 * 与现役 Stage 屏的分界：这一屏看的是**隔离候选根**（`outputs/<candidate>/run`），
 * 不是已切换的产品链——F8 激活前它没有 check 面板、没有写按钮、不进左导航。
 * 页面顶部常驻「候选链 · 未切换」标注，不把监控面伪装成现役产品。
 *
 * 数据全部经 `api.*`（mock 与真实后端同一条路）；判定字段一个都没有——
 * 「跑到哪了」是 workflow 状态与产物在不在的盘面事实，「合不合格」归现役
 * check 报告与机械回执名单，本屏只并排呈现。
 */
export function Str06MonitorScreen() {
  const { candidate = '' } = useParams<{ candidate: string }>()
  const m = useStr06Monitor(candidate)

  if (m.candidates.isError) {
    return <LoadFailed error={m.candidates.error} onRetry={() => void m.candidates.refetch()} />
  }
  if (m.run.isError) {
    return <LoadFailed error={m.run.error} onRetry={() => void m.run.refetch()} />
  }

  return (
    <section className={'flex flex-col gap-16 px-30 py-24'}>
      <header className={'flex flex-wrap items-start justify-between gap-16'}>
        <div>
          <h1 className={'m-0 text-screen font-semibold'}>STR-06 候选链 · 只读监控</h1>
          <p className={'m-0 mt-6 text-tag text-ink-4'}>
            隔离候选根（F8 激活前不进现役注册表）：判定只有现役 check 报告与机械回执名单，
            本屏只呈现盘面事实。
          </p>
        </div>
        <Mono className={'text-tag text-ink-4'}>{candidate}</Mono>
      </header>

      {m.run.data ? <RunPane run={m.run.data} /> : null}
      {m.characters.data ? <CharactersPane chars={m.characters.data} /> : null}
      {m.blueprint.data ? <BlueprintPane blueprint={m.blueprint.data} /> : null}
      {m.design.data ? <DesignPane design={m.design.data} /> : null}
      {m.ir.data ? <IrPane ir={m.ir.data} /> : null}
      <ChapterPane
        ordinals={m.ir.data?.chapters.map((c) => c.chapter_ordinal) ?? []}
        selected={m.selected}
        onSelect={m.select}
        chapter={m.chapter.data}
      />
      {m.hook.data ? <HookPane hook={m.hook.data} /> : null}
      {m.naming.data ? <NamingPane naming={m.naming.data} /> : null}
      {m.delivery.data ? <DeliveryPane delivery={m.delivery.data} onBook={m.openBook} /> : null}
    </section>
  )
}

function RunPane({ run }: { run: NonNullable<Monitor['run']['data']> }) {
  const cost = run.cost
  return (
    <Panel>
      <PaneHeader
        title={'Run · 节点图与成本'}
        meta={<><Mono>{run.status ?? 'unknown'}</Mono>{run.failed_node
          ? <Mono className={'text-ink-3'}> · 失败归属 {run.failed_node}</Mono>
          : null}</>}
      />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        {run.failure_kind ? <FailureShape run={run} /> : null}
        {run.owner_issues ? <OwnerIssues issues={run.owner_issues} /> : null}
        {run.body_failures ? <BodyFailures failures={run.body_failures} /> : null}
        {run.assembly_report ? <AssemblyReport report={run.assembly_report} /> : null}
        <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
          <span>epoch <Mono>{run.epoch ? run.epoch.ordinal : '—'}</Mono>（{run.epoch?.reason ?? 'unknown'}）</span>
          <span>accepted_body <Mono>{String(run.accepted_body)}</Mono></span>
          <span data-testid={'str06-produced-nodes'}>
            产物节点 {run.nodes.filter((n) => n.produced).length}/{run.nodes.length}
          </span>
        </div>
        <ul className={'m-0 grid list-none grid-cols-2 gap-6 p-0 text-tag'}>
          {run.nodes.map((node) => (
            <li key={node.node} className={'flex items-center justify-between gap-8 rounded-btn bg-sunken px-12 py-8'}>
              <Mono className={'text-ink-2'}>{node.node}</Mono>
              <span className={'text-ink-4'}>
                {node.paid ? '付费' : '免费'} · {node.produced ? '有产物' : '未产物'}
              </span>
            </li>
          ))}
        </ul>
        {cost ? (
          <div className={'flex flex-col gap-6'}>
            <SectionTitle note={'实付与缓存分开；usage 缺失单列，不并零'}>ledger 成本</SectionTitle>
            <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
              <span>行 <Mono>{cost.rows}</Mono>（实付 <Mono>{cost.new_paid_rows}</Mono> · 缓存 <Mono>{cost.cached_rows}</Mono>）</span>
              <span>prompt <Mono>{cost.prompt_tokens ?? 'unknown'}</Mono></span>
              <span>completion <Mono>{cost.completion_tokens ?? 'unknown'}</Mono></span>
              <span>usage 缺失行 <Mono>{cost.unknown_cost_rows}</Mono></span>
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
  )
}

/** 失败形状是 workflow_status.json 的直投影：类别、code／phase／item 定位与 context 原样呈现。 */
function FailureShape({ run }: { run: Str06RunView }) {
  const context = run.error_context ?? {}
  return (
    <div data-testid={'str06-run-failure'} className={'flex flex-col gap-4 rounded-btn bg-sunken px-12 py-10'}>
      <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-2'}>
        <span>失败类别 <Mono>{run.failure_kind}</Mono></span>
        {run.error_code ? <span>code <Mono>{run.error_code}</Mono></span> : null}
        {run.error_phase ? <span>phase <Mono>{run.error_phase}</Mono></span> : null}
        {run.error_stage ? <span>stage <Mono>{run.error_stage}</Mono></span> : null}
        {run.error_node ? <span>node <Mono>{run.error_node}</Mono></span> : null}
        {run.error_item ? <span>item <Mono>{run.error_item}</Mono></span> : null}
      </div>
      {Object.keys(context).length ? (
        <span className={'text-tag text-ink-4'}>
          context <Mono>{JSON.stringify(context)}</Mono>
        </span>
      ) : null}
      {run.detail ? <span className={'text-tag text-ink-3'}>{run.detail}</span> : null}
    </div>
  )
}

/**
 * 修订轮（R3 跨节点回派）：`runtime/issues/<owner>.json` 的只读投影。
 * open 与 resolved 都在（resolved 是历史留账，轮数不复位）；轮数上限
 * rounds_cap 由后端投影，前端不抄契约常量。停止原因不在本块推导——它在
 * 失败形状（workflow_status.json）里，两面并排呈现。
 */
function OwnerIssues({ issues }: { issues: Str06OwnerIssuesView }) {
  return (
    <div data-testid={'str06-owner-issues'} className={'flex flex-col gap-6'}>
      <SectionTitle note={'只修 typed 机械问题；轮数是终身额度，复验通过不复位'}>
        修订轮 · 跨节点回派
      </SectionTitle>
      {issues.records.length ? (
        <ul className={'m-0 flex list-none flex-col gap-8 p-0 text-tag'}>
          {issues.records.map((record) => (
            <li key={record.owner} className={'flex flex-col gap-4'}>
              <div className={'flex flex-wrap items-baseline gap-x-16 gap-y-4'}>
                <span className={'text-ink-2'}>
                  <Mono>{record.owner}</Mono> · 修订轮{' '}
                  <Mono>{record.rounds_total}</Mono>／<Mono>{issues.rounds_cap}</Mono> ·{' '}
                  <Mono>{record.status}</Mono>
                </span>
                <span className={'text-ink-4'}>
                  回派 {record.node}（由 {record.raised_by} 发现）
                </span>
              </div>
              <ul className={'m-0 flex list-none flex-col gap-2 p-0 text-ink-3'}>
                {record.issues.map((issue, index) => (
                  <li key={`${issue.code}-${issue.item}-${index}`}>
                    <Mono className={'text-ink-4'}>{issue.code}</Mono>
                    {issue.item ? <Mono className={'text-ink-4'}>[{issue.item}]</Mono> : null}
                    {' '}
                    {issue.message}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : (
        <span className={'text-tag text-ink-4'}>没有回派账记录（目录在、账为空）。</span>
      )}
    </div>
  )
}

/** 章级失败留证（B4）：逐章类别与详情；缺席（null）与空清单是两回事。 */
function BodyFailures({ failures }: { failures: Str06BodyFailuresView }) {
  return (
    <div data-testid={'str06-body-failures'} className={'flex flex-col gap-4'}>
      <SectionTitle note={'局部失败不吞其他章的合法提交（I-31）'}>章级失败留证</SectionTitle>
      <ul className={'m-0 flex list-none flex-col gap-4 p-0 text-tag'}>
        {failures.chapters.map((chapter) => (
          <li key={chapter.chapter_ordinal} className={'flex flex-col gap-2'}>
            <span>
              第 {chapter.chapter_ordinal} 章 · <Mono>{chapter.failure_kind}</Mono>
            </span>
            <span className={'text-ink-4'}>{chapter.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Stage7 集合核对报告：五态分开、不并零；not_ready 的 reason 与逐条问题原样呈现。 */
function AssemblyReport({ report }: { report: Str06AssemblyReportView }) {
  const access = report.body_access
  const list = (ordinals: number[]) => ordinals.join('、') || '无'
  return (
    <div data-testid={'str06-assembly-report'} className={'flex flex-col gap-6'}>
      <SectionTitle note={report.assembly?.status ? `assembly ${report.assembly.status}` : undefined}>
        Stage7 集合核对
      </SectionTitle>
      {access ? (
        <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
          <span>有效 <Mono>{list(access.accepted)}</Mono></span>
          <span>缺失 <Mono>{list(access.missing)}</Mono></span>
          <span>拒绝 <Mono>{list(access.rejected)}</Mono></span>
          <span>未知 <Mono>{list(access.unknown)}</Mono></span>
          <span>失效 <Mono>{list(access.stale)}</Mono></span>
        </div>
      ) : null}
      {report.chain_problems.length ? (
        <ul className={'m-0 flex list-none flex-col gap-2 p-0 text-tag text-ink-3'}>
          {report.chain_problems.map((problem, index) => <li key={index}>{problem}</li>)}
        </ul>
      ) : null}
      {report.assembly && report.assembly.status !== 'ready' && report.assembly.reason ? (
        <span className={'text-tag text-ink-3'}>not_ready：{report.assembly.reason}</span>
      ) : null}
    </div>
  )
}

function CharactersPane({ chars }: { chars: NonNullable<Monitor['characters']['data']> }) {
  return (
    <Panel>
      <PaneHeader title={'Characters v4 · 源名称结构'} meta={<Mono>{chars.contract_version ?? 'unknown'}</Mono>} />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        {chars.characters.map((c) => (
          <div key={c.char_id} className={'flex flex-col gap-4'}>
            <SectionTitle note={<Mono>{c.char_id}</Mono>}>{c.canonical}</SectionTitle>
            <span className={'text-tag text-ink-3'}>
              {c.surfaces.map((s) => `${s.surface}（第${s.from_chapter}章起${
                s.until_chapter ? `·至第${s.until_chapter}章` : ''}）`).join('、')}
            </span>
          </div>
        ))}
        {chars.name_structure ? (
          <span className={'text-tag text-ink-4'}>
            家族 {Object.entries(chars.name_structure.families).map(([id, literal]) => `${id}=${literal}`).join('、') || '无'}
          </span>
        ) : null}
      </div>
    </Panel>
  )
}

function BlueprintPane({ blueprint }: { blueprint: NonNullable<Monitor['blueprint']['data']> }) {
  return (
    <Panel>
      <PaneHeader title={'Blueprint v13 · 源骨架'} meta={<Mono>{blueprint.chapters.length} 章</Mono>} />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        {blueprint.chapters.map((chapter) => (
          <div key={chapter.chapter} className={'flex flex-col gap-6'}>
            <SectionTitle note={<Mono>{chapter.beats.length} 拍</Mono>}>第 {chapter.chapter} 章</SectionTitle>
            <ul className={'m-0 flex list-none flex-col gap-4 p-0 text-tag text-ink-2'}>
              {chapter.beats.map((beat) => (
                <li key={beat.beat_id} className={'flex flex-col gap-2'}>
                  <span>
                    <Mono className={'text-ink-4'}>{beat.beat_id}</Mono>{' '}
                    {beat.scene_boundary === 'new' ? <span className={'text-ink-4'}>〔开场景〕</span> : null}
                    {beat.temporal_mode === 'iterative' ? <span className={'text-ink-4'}>〔重复〕</span> : null}{' '}
                    {beat.event}
                  </span>
                  {beat.effects.length ? (
                    <span className={'pl-12 text-tag text-ink-3'}>
                      事实：
                      {beat.effects.map((eff, i) => (
                        <span key={i}>
                          {i ? '；' : ''}
                          <Mono>{String((eff as Record<string, unknown>).target && JSON.stringify((eff as Record<string, unknown>).target))}</Mono>
                          {' '}
                          <Mono>{String((eff as Record<string, unknown>).key)}</Mono>
                          {'='}
                          {String((eff as Record<string, unknown>).op)}
                          {' '}
                          {String((eff as Record<string, unknown>).value ?? '')}
                          {((eff as Record<string, unknown>).disclosure as Record<string, unknown> | undefined)?.visibility === 'concealed'
                            ? '（隐蔽）' : ''}
                        </span>
                      ))}
                    </span>
                  ) : null}
                  {beat.cognition.length ? (
                    <span className={'pl-12 text-tag text-ink-3'}>
                      认知：
                      {beat.cognition.map((cog, i) => (
                        <span key={i}>
                          {i ? '；' : ''}
                          <Mono>{String((cog as Record<string, unknown>).proposition && JSON.stringify((cog as Record<string, unknown>).proposition))}</Mono>
                          {' → '}
                          {String((cog as Record<string, unknown>).holder)}
                          {' '}
                          {String((cog as Record<string, unknown>).mode)}
                        </span>
                      ))}
                    </span>
                  ) : null}
                  {beat.loops.length ? (
                    <span className={'pl-12 text-tag text-ink-3'}>
                      悬念：
                      {beat.loops.map((loop, i) => (
                        <span key={i}>
                          {i ? '；' : ''}
                          <Mono>{String((loop as Record<string, unknown>).ref)}</Mono>
                          {' '}
                          {String((loop as Record<string, unknown>).op)}
                          {' '}
                          {String((loop as Record<string, unknown>).question ?? (loop as Record<string, unknown>).resolution ?? '')}
                        </span>
                      ))}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {blueprint.world_constraints.length
          ? <span className={'text-tag text-ink-4'}>世界约束 {blueprint.world_constraints.length} 条</span>
          : null}
      </div>
    </Panel>
  )
}

function DesignPane({ design }: { design: NonNullable<Monitor['design']['data']> }) {
  return (
    <Panel>
      <PaneHeader title={'Target Design · 导演说明与章目的'} meta={<Mono>{design.scenes.length} 场</Mono>} />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        {design.chapters.map((chapter) => (
          <div key={chapter.chapter_ordinal} className={'flex flex-col gap-4'}>
            <SectionTitle>第 {chapter.chapter_ordinal} 章 · {chapter.purpose}</SectionTitle>
            <span className={'text-tag text-ink-4'}>{chapter.scene_ids.join('、')}</span>
          </div>
        ))}
        {design.hook ? (
          <div className={'flex flex-col gap-4'}>
            <SectionTitle note={<Mono>{design.hook.person} 人称</Mono>}>Hook 选择</SectionTitle>
            <span className={'text-tag text-ink-2'}>材料序：{design.hook.materials.join(' → ')}</span>
            <span className={'text-tag text-ink-4'}>
              隐藏人物 {design.hook.concealed.join('、') || '无'} · 禁止揭示 {design.hook.forbidden_reveals} 条
            </span>
            <Prose className={'text-tag text-ink-3'} text={design.hook.design_brief} />
          </div>
        ) : null}
        {design.binding ? (
          <span className={'text-tag text-ink-4'}>
            Binding：义务 {design.binding.obligations} · 承载 {design.binding.entries.length} · 未承载 {design.binding.unbound.length}
          </span>
        ) : null}
      </div>
    </Panel>
  )
}

function IrPane({ ir }: { ir: NonNullable<Monitor['ir']['data']> }) {
  return (
    <Panel>
      <PaneHeader
        title={'WriterBookIR · 导航'}
        meta={<Mono>{ir.target_chapter_count ?? '—'} 章 · 总篇幅 {ir.total_length_budget ?? '—'}</Mono>}
      />
      <div className={'flex flex-col gap-10 px-18 py-16'}>
        {ir.chapters.map((chapter) => (
          <div key={chapter.chapter_ordinal} className={'flex flex-col gap-4'}>
            <SectionTitle note={<Mono>预算 {chapter.length_budget} 字</Mono>}>
              第 {chapter.chapter_ordinal} 章
            </SectionTitle>
            <span className={'text-tag text-ink-2'}>{chapter.purpose}</span>
            <ul className={'m-0 flex list-none flex-col gap-2 p-0 text-tag text-ink-4'}>
              {chapter.scene_lines.map((line, index) => <li key={chapter.scene_ids[index]}>{line}</li>)}
            </ul>
          </div>
        ))}
        {ir.release_schedule.length ? (
          <div className={'flex flex-col gap-4'}>
            <SectionTitle note={'未到位置不得使用对应字面'}>称谓释放位置</SectionTitle>
            <ul className={'m-0 flex list-none flex-col gap-2 p-0 text-tag text-ink-2'}>
              {ir.release_schedule.map((entry) => (
                <li key={`${String(entry.char_id)}@${String(entry.from_chapter)}`}>
                  <Mono className={'text-ink-4'}>{String(entry.char_id)}</Mono>{' '}
                  {(entry.surfaces as string[]).join('、')}（第{String(entry.from_chapter)}章起）
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Panel>
  )
}

function ChapterPane({ ordinals, selected, onSelect, chapter }: {
  ordinals: number[]
  selected: number | null
  onSelect: (ordinal: number) => void
  chapter: Monitor['chapter']['data']
}) {
  return (
    <Panel>
      <PaneHeader title={'章正文与章回执'} meta={<span>机械范围 · 语义未评 · 篇幅披露</span>} />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        <div className={'flex flex-wrap gap-6'}>
          {ordinals.map((ordinal) => (
            <button
              key={ordinal}
              type={'button'}
              onClick={() => onSelect(ordinal)}
              className={
                'rounded-btn border px-12 py-6 text-tag ' +
                (selected === ordinal ? 'border-ink bg-ink text-card' : 'border-line-btn bg-card text-ink-2')
              }
            >
              第 {ordinal} 章
            </button>
          ))}
        </div>
        {chapter ? (
          chapter.present && chapter.prose ? (
            <div className={'flex flex-col gap-8'}>
              <Prose text={chapter.prose} />
              {chapter.receipt ? (
                <div
                  data-testid={'str06-chapter-receipt'}
                  className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}
                >
                  <span>回执 <Mono>{chapter.receipt.status}</Mono></span>
                  <span>semantic <Mono>{chapter.receipt.semantic}</Mono></span>
                  <span>
                    篇幅 <Mono>{chapter.receipt.length.char_count}</Mono>／目标{' '}
                    <Mono>{chapter.receipt.length.target_chars}</Mono>
                    （带 [{chapter.receipt.length.band_low}, {chapter.receipt.length.band_high}]）
                  </span>
                  <span>篇幅披露 <Mono>{String(chapter.receipt.length_disclosed)}</Mono></span>
                  <span>机械检查 {chapter.receipt.mechanical_checks.join('、')}</span>
                  <span>请求身份 <Mono>{chapter.receipt.request_identity.slice(0, 12)}</Mono></span>
                </div>
              ) : null}
            </div>
          ) : (
            <span className={'text-tag text-ink-4'}>这一章还没有提交的正文与回执（present=false，不是空章）。</span>
          )
        ) : null}
      </div>
    </Panel>
  )
}

function HookPane({ hook }: { hook: NonNullable<Monitor['hook']['data']> }) {
  return (
    <Panel>
      <PaneHeader title={'Hook · 成文与机械回执'} />
      <div className={'flex flex-col gap-8 px-18 py-16'}>
        {hook.present && hook.prose ? <Prose text={hook.prose} />
          : <span className={'text-tag text-ink-4'}>Hook 尚未成文。</span>}
        {hook.receipt ? (
          <span className={'text-tag text-ink-3'}>
            回执 <Mono>{String(hook.receipt.status)}</Mono> · semantic{' '}
            <Mono>{String(hook.receipt.semantic ?? 'unverified')}</Mono> · 机械检查{' '}
            {(hook.receipt.mechanical_checks as string[] | undefined)?.join('、') ?? '—'}
          </span>
        ) : null}
      </div>
    </Panel>
  )
}

function NamingPane({ naming }: { naming: NonNullable<Monitor['naming']['data']> }) {
  return (
    <Panel>
      <PaneHeader title={'NameMap v5 · 交付变换契约'} meta={<Mono>{naming.entries.length} 人</Mono>} />
      <ul className={'m-0 flex list-none flex-col gap-6 px-18 py-16 text-tag'}>
        {naming.entries.map((entry) => (
          <li key={entry.char_ref} className={'flex flex-wrap items-baseline gap-8'}>
            <Mono className={'text-ink-4'}>{entry.char_ref}</Mono>
            <span className={'text-ink-2'}>{entry.full}</span>
            {entry.given ? <span className={'text-ink-4'}>名 {entry.given}</span> : null}
            {entry.keep_reason ? <span className={'text-ink-4'}>保留：{entry.keep_reason}</span> : null}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function DeliveryPane({ delivery, onBook }: {
  delivery: NonNullable<Monitor['delivery']['data']>
  onBook: () => void
}) {
  return (
    <Panel>
      <PaneHeader
        title={'成书交付 · manifest'}
        meta={<Mono>{(delivery.delivery_identity ?? '—').slice(0, 12)}</Mono>}
      />
      <div className={'flex flex-col gap-8 px-18 py-16'}>
        {delivery.present ? (
          <>
            <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
              <span>成书 hash <Mono>{(delivery.final_text_sha256 ?? '—').slice(0, 12)}</Mono></span>
              {delivery.length_reading ? (
                <span>
                  全书 <Mono>{delivery.length_reading.total_chars}</Mono> 字 · 偏差{' '}
                  <Mono>{delivery.length_reading.whole_book_deviation_ratio}</Mono>
                </span>
              ) : null}
            </div>
            <ul className={'m-0 flex list-none flex-col gap-2 p-0 text-tag text-ink-4'}>
              {delivery.chapters.map((chapter) => (
                <li key={chapter.chapter_ordinal}>
                  第 {chapter.chapter_ordinal} 章 · {chapter.length?.char_count ?? '—'} 字
                  {chapter.length_disclosed ? ' · 篇幅披露' : ''}
                </li>
              ))}
            </ul>
            {delivery.disclosure ? <Prose className={'text-tag text-ink-4'} text={delivery.disclosure} /> : null}
            <div>
              <button
                type={'button'}
                onClick={onBook}
                className={'rounded-btn border border-line-btn bg-card px-12 py-6 text-tag text-ink-2'}
              >
                查看成书全文
              </button>
            </div>
          </>
        ) : (
          <span className={'text-tag text-ink-4'}>尚未交付（跑到 stage7 assemble 且 manifest 提交后才有）。</span>
        )}
      </div>
    </Panel>
  )
}
