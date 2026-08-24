import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { chNo } from '@/constants/vocabulary'
import { lookupAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSampleCoverage } from '@/hooks/useSample'
import { useSid } from '@/hooks/useRouteParams'
import { QueryStates } from '@/components/state/QueryStates'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { GateChip } from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

/**
 * 进 stage5 之前先挑要深度重构的章节（默认**全书闭卷**），其余走浅层。
 * 深度 = 闭卷重构（写手看不到原文）；浅层 = 开卷改写（源章原文在下发面里）。
 *
 * ⚠ **两条臂都是「一章一次调用」**。这里曾写着「深度按拍多次调用」，那是假的：
 * `novel/stages/stage5/write.py::_write_chapter` 首轮只有一次 `ask_json`
 *（第二次只在 `--revise` 判定退化时才发），盘上 23 章 = 23 次实付，两条臂各一次。
 * 单章成本的真实差别在**下发面里有没有源章原文**：闭卷 prompt 更小、推理更多。
 * 选错了钱就花在错的章上 —— 所以这一步单独成屏，不塞在写作屏的角落里。
 *
 * ⚠⚠ 默认曾是「前 3 章」，2026-08-22 改成全书闭卷（后端
 * `novel/orchestrator/approvals.py::default_deep`）。依据是成书级实测：
 * 开卷臂的重合度比闭卷臂高一个数量级，而它此前是绝大多数章的默认路径。
 * ⚠ 「恢复默认」这颗按钮是这条默认的**第二个落点**——它曾硬编码 `n <= 3`，
 * 后端改了而它没改就会静默把整本退回旧行为。它现在按当前索引全选，
 * 不再自己算一个「前 N 章」。
 *
 * 本屏刻意不包 ScreenFrame：depth 是 stage4 → stage5 之间的选择屏，
 * 自己没有校验报告也没有自己的闸门（CHECK_KINDS 里没有 depth，
 * ScreenFrame 挂的 CheckPanel / GateBar 无处可指）；
 * 它依赖的两道闸门（s3 文风 / s4 计划）已在头部用 GateChip 摆出。
 */
export function DepthScreen() {
  const sid = useSid()
  const nav = useNavigate()
  const { doc, setDeep, startWriting } = useApprovals()
  const enums = useEnums()
  const ask = useAskJob()

  // 深度选择只需要全书索引，随便哪一章的响应都带着它 ——
  // 但得等「哪一章取得到」这件事从数据层回来，key 和 fn 必须用同一个章号
  const { coverage, defaultOrdinal } = useSampleCoverage()
  const planOrdinal = defaultOrdinal(sid)
  const q = useQuery({
    queryKey: qk.stage4(sid, planOrdinal),
    queryFn: () => api.getStage4(sid, planOrdinal),
    enabled: coverage !== undefined,
    retry: false,
  })

  const deep = doc?.deep ?? []
  const ready = !!doc && doc.gate.s3 && doc.gate.s4

  const writeAction = lookupAction(enums.data, {
    kind: 'stage5.write',
    mode: null,
    extra: ['--deep', '--shallow-rest'],
  })

  return (
    <section className="flex flex-col gap-16 px-30 py-26">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">挑深度重构章节</h1>
          <div className="text-body text-ink-3">
            深度 = 闭卷重构，写手看不到原文；浅层 = 开卷改写，源章原文在下发面里。两条臂都是一章一次调用。
          </div>
        </div>
        <div className="flex items-center gap-8">
          <GateChip ok={!!doc?.gate.s3} label="stage3 机械基线" />
          <GateChip ok={!!doc?.gate.s4} label="stage4 计划" />
        </div>
      </header>

      <QueryStates q={q} loadingLabel="读全书计划索引">
        {(d) => (
          <Panel>
            <PaneHeader
              title="全书章节"
              meta={d.index.length + ' 章'}
              right={<LinkButton onClick={() => setDeep.mutate(
                d.index.map((i) => i.output_ordinal),
              )}>恢复默认（全书闭卷）</LinkButton>}
            />
            <div className="grid gap-8 border-b border-line-soft bg-sunken-2 px-18 py-9 text-tag text-ink-4" style={{ gridTemplateColumns: '64px 1fr 96px 92px' }}>
              <span>章</span>
              <span>目标 / op</span>
              <span>预算</span>
              <span className="text-center">这一章走哪条臂</span>
            </div>
            {d.index.map((i) => {
              const on = deep.includes(i.output_ordinal)
              return (
                <button
                  key={i.output_ordinal}
                  type="button"
                  onClick={() =>
                    setDeep.mutate(
                      on
                        ? deep.filter((x) => x !== i.output_ordinal)
                        : [...deep, i.output_ordinal].sort((a, b) => a - b),
                    )
                  }
                  className={cx(
                    'grid w-full cursor-pointer items-center gap-8 border-b border-line-row px-18 py-11 text-left',
                    on ? 'bg-spend-bg' : 'bg-transparent',
                  )}
                  style={{ gridTemplateColumns: '64px 1fr 96px 92px' }}
                >
                  <Mono className="text-body">{chNo(i.output_ordinal)}</Mono>
                  <span className="flex min-w-0 flex-col gap-2">
                    <span className="text-body text-ink-2">{i.chapter_goal}</span>
                    <Mono className="text-micro text-ink-5">{i.beats + ' 拍'}</Mono>
                  </span>
                  <Mono className="text-body text-ink-3">
                    {i.budget_chars === null ? '—' : i.budget_chars + ' 字'}
                  </Mono>
                  <span
                    className={cx(
                      'rounded-chip py-3 text-center text-tag font-semibold',
                      on ? 'bg-spend text-card' : 'bg-inset text-ink-4',
                    )}
                  >
                    {on ? '深度' : '浅层'}
                  </span>
                </button>
              )
            })}

            <div className="flex flex-wrap items-center gap-14 bg-sunken-2 px-18 py-14">
              <div className="flex min-w-0 flex-col gap-3">
                <span className="text-body font-semibold">
                  深度重构 {deep.length} 章：
                  {deep.length ? deep.map((n) => chNo(n)).join(' ') : '一章都没选'}；其余走浅层
                </span>
                <span className="text-tag text-ink-4">
                  两条臂都是一章一次调用；差别在下发面给不给源章原文。选之前先看清计划——选错了钱花在错的章上。
                </span>
                {/* 选中直接画自服务端验收记录，不做乐观更新——写失败时图上就是服务端值，无需回滚 */}
                {setDeep.isError ? (
                  <span className="rounded-block border border-warn-line bg-warn-bg px-10 py-6 text-tag text-warn-ink">
                    保存失败：{errorBody(setDeep.error)}
                    <ErrorHint error={setDeep.error} />
                  </span>
                ) : null}
              </div>
              <ActionButton
                className="ml-auto"
                action={writeAction}
                disabled={!ready}
                disabledReason="stage3 机械基线与 stage4 计划都确认之后才允许下笔"
                onClick={() =>
                  ask(
                    {
                      kind: 'stage5.write',
                      label: 'stage5 写作 · 深度 ' + deep.length + ' 章',
                      extra: null,
                      mode: null,
                      args: ['--deep', deep.length ? deep.join(',') : 'none', '--shallow-rest'],
                      sid,
                      ordinal: null,
                    },
                    () => {
                      startWriting.mutate(undefined, {
                        onSuccess: () => nav('/n/' + sid + '/stage5/' + defaultOrdinal(sid)),
                      })
                    },
                    // ⚠⚠ **开写前必须先把当前这份分配落成 proof，否则后端必然拒发。**
                    // `deep` 的初值是服务端给的默认（前 3 章），而 `deep_proof`
                    // **只在显式保存时才写**——于是「用户认可默认值、一章都没点，
                    // 直接开写」这条最顺的路上，`begin_writing` 会以
                    // 「save a current depth selection before writing」拒绝，
                    // 而那正是本屏最常见的走法。
                    // ⚠ 放 `before` 不放 `after`：它是**启动作业的前置条件**
                    // （后端 `POST /jobs` 的 prepare 就在校验它），不是发起后的收尾。
                    // ⚠ 保存当前选择本身 0 成本、幂等、可反悔，而用户点的这颗按钮
                    // 写的就是「按这个分配开写」——把它落盘是忠实于那次点击的。
                    async () => { await setDeep.mutateAsync(deep) },
                  )
                }
              >
                {ready ? '按这个分配开写' : '等 stage3 / stage4 确认'}
              </ActionButton>
            </div>
          </Panel>
        )}
      </QueryStates>
    </section>
  )
}
