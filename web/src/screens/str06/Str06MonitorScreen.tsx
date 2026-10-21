import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader, SectionTitle } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { LoadFailed, LoadingPane, NotRunYet } from '@/components/state/States'
import { useStr06Monitor } from '@/hooks/useStr06Monitor'
import type {
  ChapterPacketData, HookPacketData, SourceStoryData, StoryArtifactView, StoryProseReceipt,
  TargetStoryData, Str06AssemblyReportView, Str06ChapterFailuresView,
  Str06OwnerIssuesView, Str06RunView,
} from '@/types/str06'

type Monitor = ReturnType<typeof useStr06Monitor>

/** 三层并排核读；所有状态、章包、正文与成本来自当前后端，只读不补判定。 */
export function Str06MonitorScreen() {
  const { candidate = '' } = useParams<{ candidate: string }>()
  const m = useStr06Monitor(candidate)
  if (m.candidates.isError) return <LoadFailed error={m.candidates.error} onRetry={() => void m.candidates.refetch()} />
  return (
    <section className="flex flex-col gap-18 px-30 py-24">
      <header className="flex flex-wrap items-start justify-between gap-16">
        <div>
          <h1 className="m-0 text-screen font-semibold">小说重写 · 三层只读监控</h1>
          <p className="m-0 mt-6 text-tag text-ink-4">源事实 → 目标故事 → 正文执行。机械检查与阅读质量分别呈现。</p>
        </div>
        <Mono className="text-tag text-ink-4">{candidate}</Mono>
      </header>
      {m.run.isPending ? <LoadingPane label="读取运行状态" /> : m.run.isError
        ? <LoadFailed error={m.run.error} onRetry={() => void m.run.refetch()} />
        : m.run.data ? <RunPane run={m.run.data} /> : null}
      <div className="grid grid-cols-1 items-start gap-18 xl:grid-cols-2">
        <Panel><PaneHeader title="Stage2 · 源事实层" />
          <ArtifactBody query={m.sourceStory} missing="源事实尚未发布，等待事实抽取与编译。">
            {(story) => <SourceStoryPane story={story} />}
          </ArtifactBody>
        </Panel>
        <Panel><PaneHeader title="Stage4 · 目标故事层" />
          <ArtifactBody query={m.targetStory} missing="目标故事尚未发布，等待目标建模与编译。">
            {(story) => <TargetStoryPane story={story} />}
          </ArtifactBody>
        </Panel>
      </div>
      <Panel>
        <PaneHeader title="Stage5 · 正文执行层" meta="按章顺序写作 · 章包与前章短尾绑定" />
        <div className="flex flex-col gap-12 px-18 py-16">
          <div className="flex flex-wrap gap-6" aria-label="选择章节">
            {m.ordinals.map((ordinal) => <button key={ordinal} type="button" onClick={() => m.select(ordinal)}
              aria-pressed={m.selected === ordinal}
              className={'rounded-btn border px-12 py-6 text-tag ' +
                (m.selected === ordinal ? 'border-ink bg-ink text-card' : 'border-line-btn bg-card text-ink-2')}>
              第 {ordinal} 章
            </button>)}
          </div>
          {m.selected === null ? <NotRunYet compact title="尚无可写作章节" body="目标故事发布后，这里显示当前章包与正文。" /> :
            <ArtifactBody query={m.chapter} missing="本章材料尚未生成，等待自动流水线推进。">
              {(chapter) => <>
                {chapter.packet ? <PacketPane packet={chapter.packet} /> : <p className="text-tag text-ink-4">本章章包尚未生成。</p>}
                {chapter.body !== null ? <Prose text={chapter.body} /> : <p className="text-tag text-ink-4">本章尚无已提交正文。</p>}
                {chapter.receipt ? <ReceiptPane receipt={chapter.receipt} /> : <p className="text-tag text-ink-4">尚无正文机械回执。</p>}
              </>}
            </ArtifactBody>}
        </div>
      </Panel>
      <Panel><PaneHeader title="Hook · 开篇成文" />
        <ArtifactBody query={m.hook} missing="Hook 材料尚未生成。">
          {(hook) => <>
            {hook.packet ? <PacketPane packet={hook.packet} /> : null}
            {hook.body !== null ? <Prose text={hook.body} /> : <p className="text-tag text-ink-4">Hook 尚未成文。</p>}
            {hook.receipt ? <ReceiptPane receipt={hook.receipt} /> : null}
          </>}
        </ArtifactBody>
      </Panel>
      <Panel><PaneHeader title="Stage7 · 成书交付" />
        <div className="flex flex-col gap-10 px-18 py-16">
          {m.delivery.isPending ? <LoadingPane label="读取交付状态" /> : m.delivery.isError
            ? <LoadFailed error={m.delivery.error} onRetry={() => void m.delivery.refetch()} />
            : m.delivery.data?.present ? <>
              <span className="text-tag text-ink-3">成书摘要 <Mono>{m.delivery.data.final_text_sha256 ?? 'unknown'}</Mono></span>
              <span className="text-tag text-ink-4">源章号与正文回执已由后端核对；文学质量仍需阅读验收。</span>
              {m.delivery.data.disclosure ? <Prose text={m.delivery.data.disclosure} className="text-tag text-ink-4" /> : null}
              <button type="button" onClick={() => void m.openBook()} disabled={m.downloading}
                className="self-start rounded-btn border border-line-btn bg-card px-12 py-6 text-tag text-ink-2">
                {m.downloading ? '正在读取成书' : '下载成书全文'}
              </button>
            </> : <NotRunYet compact title="尚未交付" body="最终正文、Hook 与回执通过后端当前来源核对后才开放下载。" />}
          {m.downloadError ? <LoadFailed compact error={m.downloadError} /> : null}
        </div>
      </Panel>
    </section>
  )
}

interface ArtifactQuery<T> {
  isPending: boolean
  isError: boolean
  error: unknown
  data: StoryArtifactView<T> | undefined
  refetch: () => Promise<unknown>
}

function ArtifactBody<T>({ query, missing, children }: {
  query: ArtifactQuery<T>; missing: string; children: (data: T) => ReactNode
}) {
  if (query.isPending) return <LoadingPane label="读取正式材料" />
  if (query.isError) return <LoadFailed error={query.error} onRetry={() => void query.refetch()} />
  const view = query.data
  if (!view) return <LoadFailed error={new Error('后端没有返回材料状态。')} />
  if (view.status === 'missing') return <NotRunYet compact title="尚未生成" body={missing} />
  if (view.status === 'invalid') return <div role="alert" className="flex flex-col gap-6 px-18 py-16">
    <span className="text-tag text-ink-2">正式材料不可用</span>
    {view.errors.map((error, index) => <p key={index} className="m-0 text-tag text-ink-3">{error}</p>)}
  </div>
  if (view.data === null) return <LoadFailed error={new Error('后端报告ready，但缺少正式材料。')} />
  return <div className="flex flex-col gap-12 px-18 py-16">{children(view.data)}</div>
}

function SourceStoryPane({ story }: { story: SourceStoryData }) {
  const entity = (id: string) => story.entities[id]?.label ?? id
  return <>
    <Prose className="text-tag text-ink-3" text={story.expression.statement} />
    <div className="flex flex-wrap gap-x-16 gap-y-6 text-tag text-ink-2">
      {Object.entries(story.entities).map(([id, person]) => <span key={id}>{person.label}
        <span className="text-ink-4">（{person.kind}）</span></span>)}
    </div>
    <SectionTitle>事件与故事时间</SectionTitle>
    <ol className="m-0 flex list-none flex-col gap-10 p-0 text-tag">
      {Object.entries(story.events).map(([id, event]) => <li key={id}>
        <span className="text-ink-4">第{event.chapter_ordinal}章 · {event.temporal.frame} · 场景 {event.scene_boundary}</span>
        <Prose text={event.summary} className="text-tag text-ink-2" />
      </li>)}
    </ol>
    <details><summary className="cursor-pointer text-tag text-ink-3">命题、认知与读者揭示</summary>
      <div className="mt-8 flex flex-col gap-8 text-tag">
        {Object.entries(story.propositions).map(([id, fact]) => <p key={id} className="m-0 text-ink-2">
          {fact.statement} <Mono className="text-ink-4">{fact.world_status}</Mono>
        </p>)}
        {story.knowledge_updates.map((update) => <p key={update.update_id} className="m-0 text-ink-3">
          {entity(update.holder)} · {update.mode} · {update.polarity}：{story.propositions[update.proposition_ref]?.statement ?? update.proposition_ref}
          {update.supersedes.length ? '（纠正既有认知）' : ''}
        </p>)}
        {story.exposures.map((exposure, index) => <p key={index} className="m-0 text-ink-4">
          读者 · {exposure.mode}：{story.propositions[exposure.proposition_ref]?.statement ?? exposure.proposition_ref}
        </p>)}
      </div>
    </details>
    <details><summary className="cursor-pointer text-tag text-ink-3">状态、因果与悬念</summary>
      <div className="mt-8 flex flex-col gap-8 text-tag text-ink-3">
        {story.state_updates.map((update, index) => {
          const slot = story.state_slots[update.slot_ref]
          return <p key={index} className="m-0">{slot ? slot.subjects.map(entity).join('／') + ' · ' + slot.aspect + ' · ' + slot.focus : update.slot_ref}
            ：{update.value ?? update.op}（{update.effective_time} · {update.modality}）</p>
        })}
        {story.causal_links.map((cause, index) => <p key={index} className="m-0">
          {story.events[cause.from_event]?.summary ?? cause.from_event} → {story.events[cause.to_event]?.summary ?? cause.to_event}
          <span className="text-ink-4">（{cause.kind} · {cause.support}）</span>
        </p>)}
        {Object.entries(story.threads).map(([id, thread]) => <div key={id}>
          <p className="m-0 text-ink-2">{thread.question}</p>
          {thread.updates.map((update, index) => <p key={index} className="m-0 text-ink-4">{update.op} · {update.resolution ?? '尚无答案'}</p>)}
        </div>)}
      </div>
    </details>
    <details><summary className="cursor-pointer text-tag text-ink-3">世界规则与源文歧义</summary>
      {Object.entries(story.world_rules).map(([id, rule]) => <p key={id} className="text-tag text-ink-3">{rule.kind} · {rule.statement}</p>)}
      {story.conflicts.length ? story.conflicts.map((conflict, index) => <p key={index} className="text-tag text-ink-3">{conflict.status} · {conflict.statement}</p>)
        : <p className="text-tag text-ink-4">没有登记的源文歧义；不等于已穷尽全部语义问题。</p>}
    </details>
    <details><summary className="cursor-pointer text-tag text-ink-3">源文证据</summary>
      {Object.entries(story.evidence).map(([id, evidence]) => <div key={id} className="mt-10">
        <Mono className="text-tag text-ink-4">第{evidence.chapter_ordinal}章 · 自然段{evidence.paragraph_ordinal}</Mono>
        <Prose text={evidence.text} className="text-tag text-ink-3" />
      </div>)}
    </details>
  </>
}

function TargetStoryPane({ story }: { story: TargetStoryData }) {
  const name = (id: string) => id === 'reader' ? '读者' : story.initial_displays[id] ?? story.names[id]?.primary ?? id
  return <>
    <Prose text={story.premise} className="text-tag text-ink-2" />
    <p className="m-0 text-tag text-ink-4">{story.narration} · {story.style}</p>
    <span className="text-tag text-ink-4">保留源章数 {story.source_chapter_count} · 总目标篇幅 {story.total_length_budget}</span>
    {story.character_arcs.map((arc) => <div key={arc.entity_ref} className="flex flex-col gap-4">
      <SectionTitle>{story.names[arc.entity_ref]?.primary ?? arc.entity_ref} · 人物转变</SectionTitle>
      <p className="m-0 text-tag text-ink-3">{arc.starting_position} → {arc.end_position}</p>
      <p className="m-0 text-tag text-ink-4">压力：{arc.pressure} · 选择：{arc.choice}</p>
    </div>)}
    <SectionTitle>章节目的与承接</SectionTitle>
    {story.chapters.map((chapter) => <div key={chapter.chapter_ordinal} className="flex flex-col gap-4">
      <span className="text-tag text-ink-2">第{chapter.chapter_ordinal}章 · {chapter.purpose}</span>
      <span className="text-tag text-ink-4">入场：{chapter.entry} → 离场：{chapter.exit} · 预算 {chapter.length_budget}</span>
    </div>)}
    <details><summary className="cursor-pointer text-tag text-ink-3">目标事件的触发、选择与结果</summary>
      {story.events.map((event) => <div key={event.event_id} className="mt-10 flex flex-col gap-4 text-tag">
        <Prose text={event.summary} className="text-tag text-ink-2" />
        <span className="text-ink-4">触发：{event.trigger}</span>
        <span className="text-ink-3">选择：{event.choice}</span>
        <span className="text-ink-3">结果：{event.result}</span>
      </div>)}
    </details>
    <details><summary className="cursor-pointer text-tag text-ink-3">目标初态、认知与世界规则</summary>
      <div className="mt-8 flex flex-col gap-8 text-tag text-ink-3">
        {story.initial_state.map((state, index) => <p key={index} className="m-0">
          {state.subject_ids.map(name).join('／')} · {state.dimension} · {state.qualifier}：{state.value ?? (state.op === 'unset' ? '已明确解除' : '状态值未知')}（{state.modality}）
        </p>)}
        {story.initial_knowledge.map((knowledge, index) => <p key={index} className="m-0">
          {name(knowledge.holder)} · {knowledge.mode}：{story.propositions[knowledge.proposition_ref]?.statement ?? knowledge.proposition_ref}
        </p>)}
        {story.world_rules.map((rule) => <p key={rule.rule_id} className="m-0">{rule.statement}</p>)}
      </div>
    </details>
  </>
}

const PACKET_SECTIONS = [
  ['must_realize', '必须写出'], ['must_respect', '必须遵守'], ['continuity', '连续性'],
  ['creative_guidance', '表达空间'], ['background', '背景'], ['guard_only', '禁止越界'],
] as const

function PacketPane({ packet }: { packet: ChapterPacketData | HookPacketData }) {
  const boundaries = 'entry_state' in packet
    ? [{ label: '入场', states: packet.entry_state }, { label: '离场', states: packet.exit_state }] : []
  return <details><summary className="cursor-pointer text-tag text-ink-3">当前正式写作材料 · 六分区</summary>
    <p className="text-tag text-ink-4">当前称谓：{Object.values(packet.names).join('、')} · 目标 {packet.length_budget} 非空白字符（{packet.min_chars}～{packet.max_chars}）</p>
    {'actions' in packet ? <div className="flex flex-col gap-6 text-tag text-ink-3">
      <SectionTitle>本章行动与状态</SectionTitle>
      {packet.actions.map((action, index) => <Prose key={'action-' + index} text={action} className="text-tag text-ink-3" />)}
      {boundaries.map(({ label, states }) =>
        <p key={label} className="m-0 text-ink-4">{label}：{states.join('；') || '无登记状态'}</p>)}
    </div> : null}
    <div className="mt-10 grid grid-cols-1 gap-10 lg:grid-cols-2">
      {PACKET_SECTIONS.map(([field, title]) => <div key={field}>
        <SectionTitle>{title}</SectionTitle>
        {packet[field].length ? packet[field].map((line, index) => <Prose key={index} text={line} className="text-tag text-ink-3" />)
          : <p className="text-tag text-ink-4">本区为空</p>}
      </div>)}
    </div>
  </details>
}

function ReceiptPane({ receipt }: { receipt: StoryProseReceipt }) {
  return <div data-testid="story-prose-receipt" className="flex flex-wrap gap-x-20 gap-y-6 rounded-btn bg-sunken px-12 py-10 text-tag text-ink-3">
    <span>机械 {receipt.mechanical} · 语义 {receipt.semantic}</span>
    <span>{receipt.provider} / {receipt.model}</span>
    <span>前章短尾 <Mono>{receipt.prior_tail_sha256.slice(0, 12)}</Mono></span>
    <span>正文摘要 <Mono>{receipt.body_sha256.slice(0, 12)}</Mono></span>
    <span>请求 <Mono>{receipt.request_id.slice(0, 12)}</Mono></span>
    <span>开篇窄修 {receipt.patches.length} 次</span>
  </div>
}
function RunPane({ run }: { run: NonNullable<Monitor['run']['data']> }) {
  const cost = run.cost
  return (
    <Panel>
      <PaneHeader
        title={'运行状态与成本'}
        meta={<><Mono>{run.status ?? 'unknown'}</Mono>{run.failed_node
          ? <Mono className={'text-ink-3'}> · 失败归属 {run.failed_node}</Mono>
          : null}</>}
      />
      <div className={'flex flex-col gap-12 px-18 py-16'}>
        {run.failure_kind ? <FailureShape run={run} /> : null}
        {run.owner_issues ? <OwnerIssues issues={run.owner_issues} /> : null}
        {run.chapter_observation_failures || run.body_failures ? (
          <div className={'grid grid-cols-2 gap-12'}>
            <ChapterFailures failures={run.chapter_observation_failures} title={'章观察'}
                             testId={'str06-chapter-observation-failures'} />
            <ChapterFailures failures={run.body_failures} title={'正文'}
                             testId={'str06-body-failures'} />
          </div>
        ) : null}
        {run.assembly_report ? <AssemblyReport report={run.assembly_report} /> : null}
        <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
          <span>epoch <Mono>{run.epoch ? run.epoch.ordinal : '—'}</Mono>（{run.epoch?.reason ?? 'unknown'}）</span>
          <span>accepted_body <Mono>{String(run.accepted_body)}</Mono></span>
          <span data-testid={'str06-produced-nodes'}>
            产物节点 {run.nodes.filter((n) => n.produced).length}/{run.nodes.length}
          </span>
        </div>
        <details><summary className="cursor-pointer text-tag text-ink-3">节点产物明细</summary>
        <ul className={'m-0 mt-8 grid list-none grid-cols-2 gap-6 p-0 text-tag'}>
          {run.nodes.map((node) => (
            <li key={node.node} className={'flex items-center justify-between gap-8 rounded-btn bg-sunken px-12 py-8'}>
              <Mono className={'text-ink-2'}>{node.node}</Mono>
              <span className={'text-ink-4'}>
                {node.paid ? '付费' : '免费'} · {node.produced ? '有产物' : '未产物'}
              </span>
            </li>
          ))}
        </ul></details>
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
/**
 * 逐章失败留证（D3/W2）：「最近一次完成执行」的请求范围、实际执行章与失败
 * 子集原样呈现。合法空清单＝完成执行且无失败（与「查过没事」的缺席 null 是
 * 两件事，I-42）；局部失败不吞其他章的合法提交（I-31）。
 */
function ChapterFailures({ title, failures, testId }: {
  title: string
  failures: Str06ChapterFailuresView | null
  testId: string
}) {
  const list = (ordinals: number[]) => ordinals.join('、') || '无'
  if (!failures) {
    return (
      <div data-testid={testId} className={'flex flex-col gap-4 rounded-btn bg-sunken px-12 py-10'}>
        <SectionTitle note={'最近一次完成执行'}>{title} · 逐章失败留证</SectionTitle>
        <span className={'text-tag text-ink-4'}>
          这个节点还没有一次完成执行（present=null，不是空清单）。
        </span>
      </div>
    )
  }
  return (
    <div data-testid={testId} className={'flex flex-col gap-4 rounded-btn bg-sunken px-12 py-10'}>
      <SectionTitle note={'最近一次完成执行'}>{title} · 逐章失败留证</SectionTitle>
      <div className={'flex flex-wrap gap-x-24 gap-y-6 text-tag text-ink-3'}>
        <span>请求 <Mono>{list(failures.requested)}</Mono></span>
        <span>实际执行 <Mono>{list(failures.attempted)}</Mono></span>
        <span>记录 <Mono>{failures.recorded_at ?? '—'}</Mono></span>
      </div>
      <ul className={'m-0 flex list-none flex-col gap-4 p-0 text-tag'}>
        {failures.chapters.map((chapter) => (
          <li key={chapter.chapter_ordinal} className={'flex flex-col gap-2'}>
            <span>
              第 {chapter.chapter_ordinal} 章 · <Mono>{chapter.failure_kind}</Mono>
            </span>
            <span className={'text-ink-4'}>{chapter.detail}</span>
          </li>
        ))}
        {failures.chapters.length === 0 ? (
          <li className={'text-ink-4'}>无失败章（完成执行，合法空清单）</li>
        ) : null}
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
