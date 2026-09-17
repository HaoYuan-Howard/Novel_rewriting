# stage4 · Design → Grounding → Context（STR-03-S4 目标契约）

> **2026-09-17 切换批次**：正式删除 `plan_chapters.json`；Design 以有序 `steps[]` +
> `delivery_ref` 为呈现权威；Context 为 scene-centric Writer IR。Blueprint **冻结**。
> 公开 node id 仍为 `stage4.design` / `stage4.plan` / `stage4.context`。

Stage4 规划呈现，不重新抽取剧情。编译链：

```text
Stage2 Story IR（冻结）
    → Stage4SourceView（ingress 投影，非正式产物）
    → presentation graph（Design）
    → evidence_binding（Grounding）
    → writer_context（Writer IR）
    → Stage5 prose
```

## 底线

1. **Context 不重新判断、不独立维护上游**；但必须把当前写作单元所需信息**确定性展开**
   为可直接使用的 Writer IR（允许消费投影字节，禁止第二真相 / 重复决策）。
2. **每一个影响写作的编译结果**须有明确输入或确定规则；禁止从 evidence/prose 推断
   permission 或 narrator。
3. **呈现序**：章内场景序 = Design `scenes[]`；场内序 = `scenes[].steps[]`。Context
   必须保序，不得按 beat_ref / 证据位置 / 类别重排。呈现序 ≠ 故事发生序 ≠ 人物获知序。
4. **`delivery_ref`**：compiler 在 Design 快照内发号；跨快照不承诺同串同义；绑定有效性 =
   Design 快照 + `delivery_ref`。
5. **权限**：Stage2 章级 `revealed_from` / `identity_revealed_from` 为合法边界；Design 可声明
   稀疏 `permission_transitions`（自该 delivery **粘性**生效至下次 transition）。Compiler
   只做章级边界校验、有序传播、物化。
6. **`delivery_focus`**：同 beat 多 delivery 时必填；bounded 语义侧面；不裁剪 `beat.content`；
   禁止 style/camera/dialogue/pacing/旧 presentation_instruction。
7. **Grounding 失败三分**：`ambiguous`（窄解析）/ `infeasible`（约束冲突→预算内 Design 重采）/
   `input_insufficient`（缺上游→**不**重采 Design）。
8. **权重权威**：仅 `chapters[].weight`；`book_strategy.summary` 只解释；禁止双表可改。
9. **升版**：一次对外切换共用目标版本；拒读旧版；禁止空 `steps` / 默认全允许伪装迁移。
10. **Stage5**：同批切换 reader/材料；禁止把新 Writer IR 转回旧章级数据库再喂写手。

## 产物与版本（目标）

| 文件 | 载体 | 职责 |
|---|---|---|
| blind_candidate.json / presentation_graph | PresentationGraph / BlindCandidate **v7** | Design：策略摘要、有序 steps、hooks、structure、permission_transitions |
| evidence_binding.json | EvidenceBinding **v7** | `delivery_ref → evidence`；已解决绑定供下游 |
| writer_context.json | WriterContext **v11** | scene-centric Writer IR |
| ~~plan_chapters.json~~ | **退役** | 预算/对账边拆入 Design weight + Context provenance + 纯函数 |

路径与版本以 `contracts/schema/__init__.py::ARTIFACT_SPECS` 为准。

## SourceView

`contracts/schema/stage4_source.py` + `novel/stages/stage4/source_view.py::build_stage4_source_view`。
非第四产物、非节点；缺字段 → `input_insufficient`。

## 字段来源（permission）

| 层 | 职责 |
|---|---|
| Stage2 / SourceView | 事实、模态、revealed_from、identity_revealed_from |
| Design | 呈现何处/顺序/视点；稀疏 permission_transitions（不得越章级边界） |
| Grounding | delivery 证据（及必要舞台歧义） |
| Context compiler | 合并输入 → steps 内容、facts.permission、surface 快照、state_delta、target_chars |

## `plan_chapters` 职责拆迁

| 原职责 | 归属 |
|---|---|
| 章/场 weight | Design |
| 整数预算 | `allocate_budgets` → Context `target_chars` |
| context_link | Context provenance（Design+Grounding+SourceView+CompilerRevision） |
| 章集分母 | Design 章列表 |
| 结构度量 | 现场计算 |

---

以下为切换前历史说明（blind_candidate v6 / plan_chapters v7 / writer_context v10），
仅供存量盘面解码；新 clean root 按上表重建。

---

## 附录：切换前历史说明（只读解码）

# stage4 路 瀹屾暣鍓ф儏鐨勭洸璁捐銆佸垎鍦哄憟鐜颁笌璇佹嵁缁戝畾

Stage4 瑙勫垝鍛堢幇锛屼笉閲嶆柊鎶藉彇鍓ф儏銆侭eat.content 鍙湁涓€浠斤紝BeatPlacement 鏄妭鐐瑰埌鍦烘櫙鐨勫敮涓€
鍙紪杈戞槧灏勶紱灏戞暟鑺傜偣鍙敤澶氫釜鍚岀珷 delivery 鍏卞悓鍛堢幇銆傛櫘閫氬垎鍦轰笉淇敼 Blueprint锛屼笉閲嶅紑绔犲锛?涓嶆柊澧炲浐瀹氭ā鍨嬪伐浣嶃€傚瓧娈佃亴璐ｄ笌鎷嗗垎瑙勫垯瑙?[plot_blueprint.md](./plot_blueprint.md)銆?
鎵ц椤哄簭鏄?stage4.design 鈫?stage4.plan 鈫?stage4.context锛涘洓閬撲汉宸ュ闃呴椄宸查殢 2026-09-15
鍏ㄨ嚜鍔ㄨ鍐虫暣浣撻€€褰癸紝A 钀界洏锛堟満姊版湁鏁堬級鍗崇敱缁熶竴鏈烘鍑嗗叆鏀捐 B锛孊 钀界洏鍗虫斁琛?Context锛?B 鐨?typed infeasible 缁堟€佽Е鍙戦绠楀唴鑷姩 `stage4.design --replan`锛堣 BACKEND_CONTRACT R-5.20锛夈€?A 涓庣珷绾?B 浣跨敤鐙珛 session锛汣ontext 鐙珛闆惰皟鐢ㄧ敓鎴愩€係tage5 浠嶄竴娆″啓鏁寸珷銆?
鍘嗗彶 Commander 瀹￠槄鏇句娇鐢?`novel-stage4-reviewer-rulings-v3` 杩愯緭濂戠害锛堝凡闅?2026-09-15 瑙掕壊閫€褰癸紝
璐︿笂鍘嗗彶琛屽彧璇昏В鐮侊級锛氭瘡绔犲畬鏁村紩鐢ㄧ洰褰曟槧灏勪负灞€閮?鐭彞鏌勶紝鍘嗗彶绾︽潫鎸夊喕缁撶洰褰曟槧灏勪负 `hN`锛涗唬鐮佷粠涓嶅彲鍙?submission_options 鎭㈠鍘熷紩鐢ㄤ笌
鎽樿銆傚畬鏁?options SHA 涓?exact request SHA 鍚屾椂鏍￠獙锛屾湭鐭ユ垨璺ㄧ珷寮曠敤鎷掔粷銆傚彧鍏变韩瀹屽叏
鍚屾瀯鐨?schema 瀹氫箟锛屼笉缂╁噺绔犺妭銆佸垽鏂€佷慨澶嶇爜鎴栧紩鐢ㄥ熀鏁帮紱姝ｅ紡瀹℃壒浠嶇粡杩囧師 typed loader銆?鍘熷璇锋眰銆乻chema 鍜屽洖绛旂暀鍦ㄨ繍琛岃褰曪紝鏃ц繍杈撳洖绛斾笉鑷姩缁ф壙鏂?schema 鐨勫鎵广€?
## 涓€銆佷骇鐗╀笌鐗堟湰

| 鏂囦欢 | 褰撳墠杞戒綋 | 鑱岃矗 |
|---|---|---|
| blind_candidate.json | BlindCandidate v5 | A 鏁翠功绛栫暐銆佸畬鏁寸珷璁捐銆乷paque 涓讳綋鏄犲皠涓庢潵婧?|
| blind_chapter_edit.json | BlindChapterEdit v3 | 鍗曠珷瀹屾暣璁捐鐨勫厤璐?typed 缂栬緫杈撳叆 |
| evidence_binding.json | EvidenceBinding v5 | 姣忛」鍛堢幇鐨勮瘉鎹€佽垶鍙颁笌瀹為檯 B 鏉ユ簮 |
| plan_chapters.json | PlanChapters v6 | 缂栬瘧瀹屾垚鐨勬寮忓満鏅€乸lacements銆佺鐐瑰拰鏁存暟棰勭畻 |
| plan_edit.json | PlanEdit v6 | 姝ｅ紡璁″垝鐨勫畬鏁?typed 缂栬緫杈撳叆 |
| writer_context.json | WriterContext v9 | 绔犵骇鍓ф儏鐩綍銆侀€愬満鍛堢幇鍜岃〃杈捐鍙?|

璺緞瀹屾暣鍚嶄笌鐗堟湰浠?contracts/schema/__init__.py::ARTIFACT_SPECS 涓哄噯锛寃orkflow manifest
鐧昏 plot-blueprint-v1 缁勫悎銆傛棫鏁版嵁閰嶅鏃т唬鐮佷繚鐣欙紝褰撳墠骞插噣鏍归噸鏂扮敓鎴愶紱涓嶆墜琛ュ瓧娈垫垨杩佺Щ瀹℃壒銆?
## 浜屻€丼tage4A锛歜lind_candidate.json v5

### 2.1 A 鐨勮緭鍏ヨ竟鐣?
鏁翠功绛栫暐璇锋眰鍙绔犲姛鑳藉強纭畾鎬ф鍐碉紱绔犵骇璇锋眰瑙佸喕缁撶瓥鐣ャ€佸綋鍓嶇珷 PlannerTruthView 涓庣煭灏?opaque 寮曠敤銆侾lannerTruthView 鎻愪緵璁捐鎵€闇€鐨勫畬鏁磋妭鐐广€佺█鐤忕姸鎬併€佺害鏉熴€佸繀瑕佺煡璇嗕笌鏄惧紡绔偣锛?鍙互鐪嬪悗鍙扮湡鐩革紝浣嗕笉鐪嬪師鏂囥€佹簮鍧愭爣銆丒videnceAnchor銆佹簮椤哄簭缂栧彿鎴栨簮鑸炲彴銆?
local/opaque/formal 鏄犲皠鐢?HMAC 缂栬瘧鍣ㄦ寔鏈夈€傚瘑閽ヤ笉涓嬪彂锛屼笉鑳戒粎鏁ｅ垪鍙灇涓炬寮忕紪鍙凤紱璺ㄧ珷鐘舵€併€?Global 鍜屽繀瑕佽儗鏅紩鐢ㄥ悓鏍峰幓搴忋€傚叕寮€鐩綍涓庣紪璇戞槧灏勫叡鍚岀粦瀹氳姹傝韩浠斤紝涓嶈兘婕忔帀鏄犲皠鍚庡鐢ㄥ洖绛斻€?
### 2.2 椤跺眰涓庣珷鍦哄瓧娈?
鍊欓€変繚瀛?alias_algorithm銆乼ruth_sha256銆乻ubject_aliases銆乸articipant_aliases銆?reconstruction_strategy銆乷utput_chapters 鍜?_provenance銆傛槧灏勭敤浜庣紪璇戝強 B 瑙ｅ紩鐢紝涓嶅洖鐏?A銆?
姣忕珷鍚?structure_decision銆佷笁涓?goal銆亀eight銆乧hapter_rewrite_brief銆乻cenes銆?beat_placements 涓?anchor_bindings銆傚満鏅彧瀹夋帓 purpose銆乺ole銆乨elivery銆乼ransition銆?context 寮曠敤涓?turn锛涘畬鏁磋妭鐐规鏂囦粛鍦?Blueprint銆備唬鐮佹敞鍏ョ珷鍙枫€佸€欓€夊満韬唤鍜岄绠椼€?
姣忚妭鐐规湁涓€浠?placement锛宒eliveries[] 姣忛」缁?scene_ref銆乸resentation_instruction锛?鍗曟鏁翠綋鍛堢幇鍙樉寮?null锛屽娆″憟鐜板繀椤婚€愬満璇存槑鎵挎媴鐨勪换鍔°€傚悓涓€鑺傜偣鍚屼竴鍦哄彧璁镐竴娆°€?delivery 鐢ㄨ妭鐐逛笌鍦烘櫙缁勫悎瀹氫綅锛屼笉鍒涘缓鍙緵 State 寮曠敤鐨勫瓙涓讳綋鎴栧瓙 claim銆?鍗曞満绔偣鍙淳鐢燂紝澶氬満鐨勭煡璇嗚Е鍙戙€佺姸鎬佹垚绔?缁撴潫銆佽法鑺傜偣绾︽潫銆佸紑绡?缁撳熬/turn 蹇呴』鏄庣‘钀藉満銆?鐖惰妭鐐瑰叏閮ㄤ富瑕佸憟鐜板畬鎴愬墠锛屼笉寰楃敤 context_beat_refs 澶嶇敤鏁存姝ｆ枃銆?
### 2.3 璋冨害銆佹鏌ョ偣涓庡師瀛愭彁浜?
绛栫暐 session 鏄€愮珷鎵囧嚭鐨勪覆琛屽墠缃紱绛栫暐楠岃瘉骞?checkpoint 鍚庯紝N 涓€愮珷 session 鎵嶈兘鍚姩銆傝妭鐐瑰唴
瀹為檯骞跺彂鍙?`min(4, NOVEL_CONCURRENCY, 寰呰窇绔犳暟)`锛氶厤缃彲浠ヤ笅璋冿紝浣嗙‖涓婇檺濮嬬粓涓?4銆傜瓥鐣ュ拰姣忕珷鍒嗗埆
缁戝畾鍚屼竴涓?`run_id` 涓嬭嚜宸辩殑 purpose銆乸rovider session銆佽姹傝韩浠姐€?accepted attempt銆乴edger 涓庨獙璇佸悗 checkpoint銆傝繘绋嬩腑鏂悗鍙仮澶嶅尮閰嶅綋鍓嶄唬鐮?濂戠害/杈撳叆/prompt/schema
涓?ledger 闂悎鐨勫凡楠?unit锛屽彧琛ョ己澶辨垨澶辫触鍗曞厓锛涙仮澶?checkpoint 涓嶈 cache hit锛屼篃涓嶆柊澧?wire銆?姝ｅ紡 provenance 鐨?usage 涓嶅彇鈥滄湰娆￠噸鍚互鍚庘€濈殑澧為噺锛氬畠鎸夊綋鍓?`run_id` 涓?N+1 涓ǔ瀹?purpose
浠?ledger 閲嶅缓鍏ㄩ儴鎴愭湰锛屽寘鍚厛鍓嶆棤 checkpoint 鐨勫け璐ョ鏈?session锛涘洜姝ら儴鍒嗘仮澶嶃€佸畬鏁存仮澶嶄笌
aggregate prepared 灏剧獥鎭㈠蹇呴』寰楀埌鍚屼竴浠?usage锛屼笉鑳芥妸宸蹭粯璐瑰巻鍙叉樉绀烘垚 0 鎴栨紡鎺夈€?
鏅€?checkpoint 鎭㈠浠嶄弗鏍奸檺浜庡悓 run銆傛櫘閫?A 杩愯鍙︽湁閫愬崟鍏冨巻鍙查噰鐢細绛栫暐涓庡悇绔犳寜褰撳墠瀹為檯璇锋眰
鍒嗗埆鏍搁獙鏉ユ簮锛屽彲浠ュ悎鐢ㄥ涓巻鍙?run锛涘悓璇锋眰鍑虹幇涓嶅悓鏈夋晥鍥炵瓟鏃朵繚鎸佹湭鐭ワ紝涓嶆寜鏈€杩戞垨鍛戒腑鏁伴€夊彇銆?姣忎釜閫夋嫨鍐荤粨鍒板綋鍓?`design/units/{strategy|chNNNN}/historical-adoption.json`锛涙櫘閫氳仛鍚?journal v2
鍦ㄦ瘡涓?adoption 琛屼腑淇濆瓨 `source_run_id`锛屽悓鏃跺喕缁撳師 producer parent銆佸畬鏁存枃浠跺瓧鑺備笌琛ユ彁浜ゅ洖鎵с€?鏉ユ簮 run銆佸畬鏁?checkpoint 閾俱€佸洖绛斻€乻ession銆佹垚鏈处鍙?褰撳墠 compiler/validator 鍧囬渶闂悎锛涙崯鍧忎笉鍙В閲婁负鏈懡涓€傚悓涓€娆℃櫘閫氳繍琛岀殑 usage 浠嶅彧璁ゆ湰 run
瀹屾暣鎴愭湰锛屽巻鍙插凡璐垂鐢ㄧ敱鍘?ledger 琛屼笌鏂伴噰鐢ㄥ紩鐢ㄥ叧鑱旓紝涓嶅啀澶嶅埗鎴愭柊娑堣€椼€?
`stage4.design --reaggregate` 鍙湪姝ｅ紡鍊欓€夌己澶辨椂鍏嶈垂姹囧悎鍏ㄩ儴绛栫暐/绔犲搷搴旓紝涔熷彲鎸夊悓 run journal
骞傜瓑瀹屾垚鑷繁鐨勬彁浜ゃ€傚畠涓嶈В鏋愰粯璁ゆā鍨嬮厤缃€佷笉鍚姩 driver銆佷笉琛ヤ拱缂哄け鍗曞厓锛涘叏閮ㄩ噰鐢ㄤ骇鐗╀娇鐢?`BlindCandidateCompiledProvenance(origin=compiled, architecture=book_strategy_adopted_v1)`锛?`sources_sha256` 缁戝畾瀹屾暣鑱氬悎鏉ユ簮琛岋紝涓嶄吉閫犳湰娆?model/usage銆傛櫘閫氫粯璐归厤缃繍琛屽嵆浣垮叏閮ㄥ懡涓紝
浠嶄繚鐣欏悇鏉ユ簮瀹為檯妯″瀷鍜屾槑纭殑閲囩敤璐︼紱涓嬫父 repair/human evolution 璇诲彇鍚屼竴浠?v2 source 韬唤銆?
鏅€?A 鐨?prepared 鑱氬悎鐢?`stage4.design --recover-prepared <run> --subject-sha256 <proof>`
鍏嶈垂琛ュ熬銆傚師浠ｇ爜銆佸師閰嶇疆銆佺瓥鐣ヤ笌鍏ㄩ儴绔犳潵婧愬潎椤婚€氳繃褰撳墠鎶曞奖涓庡畬鏁?compiler锛涜瘉鏄庡師 owner 鍋滄鍚庯紝
鏂?committer 鎸侀攣澶嶉獙鍐欏墠鍩虹嚎锛屼粎琛ュ啓宸插皝瀛樺瓧鑺備笌缂哄け閲囩敤瀹¤銆傛仮澶嶅洖鎵у湪涓讳骇鐗╁啓鍏ュ墠棰勮棰嗭紝
鏈€鍚庤繘鍏ュ師 journal锛涗笅娓?lineage 缁х画楠岃瘉璇ュ洖鎵с€傛崯鍧忋€佺涓夋柟婕傜Щ涓庡浠?prepared 淇濇寔鏈煡銆?
typed targeted repair 浠嶄娇鐢ㄧ嫭绔嬫ˉ锛氭柊
execution epoch 鐨?target run 鍦ㄩ涓?wire 鍓嶏紝鍙噰鐢ㄥ敮涓€鏈€澶у巻鍙?run 涓笌 target 鐨?resolved profile銆?瀹屾暣璇锋眰銆佽緭鍏ャ€乻chema銆乼oken ceiling 鍜屾仮澶嶅崗璁€愬瓧娈电浉绛夛紝涓?source checkpoint/session/ledger銆?褰撳墠鍔ㄦ€?validator 涓庨棴鍚堜笟鍔＄姸鎬佸叏閮ㄩ€氳繃鐨勭洰鏍囩珷鍝嶅簲銆傚畠涓嶈鐩栫瓥鐣ャ€侀娆℃暣涔︽垨 strategy replan锛?骞跺垪鏉ユ簮涓嶇寽銆傞€夋嫨鍐欏叆 target `design/historical-checkpoint-adoption.json`锛屾寮?lineage 浣跨敤
`origin=historical_checkpoint` 鍚屾椂缁戝畾 source attempt 涓?manifest SHA銆俿ource revision 濮嬬粓鍙锛屼笉澶嶅埗
鍒?target銆佷笉缁棫 provider session锛泆sage 鎸?source/target run 鍒嗘浠?ledger 绮剧‘鍚堝苟銆?
repair/replan 鐨勬暣涔?aggregate 鍙︽湁涓€鏉′笉鍙?wire 鐨勬彁浜ゅ熬绐椼€俿ource producer 鍦ㄦ敼鍔?`stage4/blind_candidate.json` 鍓嶅厛鍐?`design/design-repair-aggregate.json` v3
prepared锛沯ournal 鍐荤粨 producer parent銆乧ode銆乺epair/replan scope銆乸arent/decision/targets銆?瀹屾暣 unit sources銆乧andidate baseline/target 涓?finalization锛屽苟鍚屾椂淇濆瓨瀹屾暣鍘熷瀛楄妭 SHA
涓?prepared self/commit SHA銆傚師 producer 宸?ended 鍚庯紝operator 鍙湪鍏ㄤ功鐗╃悊鍘嗗彶鏍逛笅鎭㈠
鍞竴銆佹棤鍏朵粬 Stage4/Stage2 prepared 鍐茬獊鐨勮繖涓€浠斤紱service/legacy 鍚岀浉瀵硅矾寰勫弻鍓湰銆?澶氫唤 prepared 鎴栦换涓€鐗╃悊鍓湰婕傜Щ閮戒笉鐚?winner銆傚敮涓€鍏ュ彛涓猴細

```text
stage4.design --recover-repair-aggregate <source_run_id> \
  --source-journal-sha256 <raw_sha256> \
  --source-commit-sha256 <prepared_self_sha256>
```

璇ヨ矾鏄?FREE/0-wire deterministic producer锛氫笉瑙ｆ瀽宸?stale review token锛屼笉鏋勯€?executor
profile/client/provider session/checkpoint锛屼笉缁棫 provider銆侫gent source 蹇呴』鑳界敱 strict-LF
`agent_runs.jsonl` 璇佹槑鍞竴 epoch 宸?`drained` 鍏抽棴銆乸arent ended 涓?owner-stopped lease
鎸佺画瑕嗙洊鏈 commit锛沝irect CLI 鍙湪 journal 鏄庤 producer parent 灏辨槸 source run 鏃舵垚绔嬨€?鎭㈠鍏堥€愭牸澶嶉獙 source checkpoint/session/attempt/ledger/code/request锛屽啀鐢ㄥ綋鍓?compiler
浠庡凡楠屽搷搴旈噸寤哄€欓€夛紝瑕佹眰鏁翠唤 typed candidate锛堝惈 usage銆乵odel銆乸rompts銆乮nputs銆乺oot/truth銆?unit lineage 涓?adoption provenance锛変笌 journal sealed payload 瀹屽叏鐩哥瓑锛屽苟閲嶈窇褰撳墠
truth/input/schema/HARD銆俵ive candidate 鍙兘鏄?journal baseline 鎴?target锛沚aseline 鎵嶅啓鐩爣锛?target 鍙獙璇侊紝绗笁鎬佹嫆缁濄€傞涓€欓€夊啓涔嬪墠蹇呴』瀵?source adoption銆乻ource-stable
recovery 鍏崇郴鍜屽綋鍓?target run/parent attempt 鍋氬叏灞€ 0-or-exact preclaim锛涗簨浠跺熬闈?LF銆?閲嶅銆佸浣欏瓧娈垫垨褰掑洜婕傜Щ閮借鍦ㄥ啓鍊欓€夊墠鎷掔粷銆傚€欓€夐獙鏀跺悗骞傜瓑琛?source adoption
鍜?recovery-run鈫抯ource-run audit锛屾渶鍚庢墠鍘熶綅灏?source journal 鎻愪负 committed銆傞噸鏉€浜?candidate/adoption/audit/mark 鍚勭偣鐢变笅涓€涓柊 recovery run 缁х画锛沜ommitted 涔嬪悗鐨勪笅娓?浠嶅繀椤诲鏍?journal 璁板綍鐨?canonical/target-attempt/adoption audit锛屽垹璐︿笉鑳借褰撴垚宸叉彁浜ゃ€?
unit checkpoint 涓嶆槸姝ｅ紡 artifact銆傚彧鏈夌瓥鐣ュ拰鍏ㄩ儴绔犺妭榻愬叏锛屼唬鐮佹墠鑳芥妸 local alias 鏄犲洖姝ｅ紡 alias銆?娉ㄥ叆绔犲彿/鏉冮噸/`candidate_scene_id`銆佹寜绔犳帓搴忓苟缁勮瀹屾暣 `BlindCandidate`锛涜仛鍚堢墿蹇呴』鍐嶈繃 strict schema銆?鍗曠珷鍔ㄦ€侀椄涓庢暣涔﹀叏閮?HARD銆傞€氳繃鍚庢墠鐢?commit journal 鍘熷瓙鍐?`blind_candidate.json`锛涗腑鏂€佺己绔犮€?鑱氬悎澶辫触鎴?HARD 鍛戒腑閮戒笉寰楃暀涓嬮儴鍒嗘寮忔枃浠讹紝涔熶笉寰楄鐩栨棫姝ｅ紡鍊欓€夈€?
### 2.4 A 璁捐瀹￠槄涓庢敹鏁涗慨璁紙宸查€€褰?2026-09-15 鍏ㄨ嚜鍔ㄨ鍐筹級

DESIGN_REVIEW_CONTRACT 鐨勭幇褰瑰啓闈㈠凡闅忓洓閬撲汉宸ラ椄閫€褰癸細璁捐瀹￠槄宸ヤ綔鍖呫€佹ā鏉裤€乼yped
accept/repair/replan 涓?review token 涓嶅啀鏈夌敓浜ц€咃紱鍘嗗彶鍐冲畾涓庡绾﹀彧璇昏В鐮併€傛満姊板噯鍏ュ彧娑堣垂
浜х墿 freshness銆両NVALID 涓?infeasible 缁堟€佺瓑鏈烘浜嬪疄銆侭 鐨?typed infeasible 缁堟€佺敱
`approvals.cast_infeasible_replan_scope` 閾告垚 `replace_reconstruction_strategy` 鍏抽棴寮忕害鏉燂紝
闃熷垪鍙戝竷棰勭畻鍐呰嚜鍔ㄩ噸閲囷紱棰勭畻鑰楀敖鍗?`WorkUnitBudgetExhausted` 缁堟€併€?
鏅€氭崲鍦恒€佸垎娈佃〃鐜板拰宸叉湁绔偣缁戝畾鍧囩暀鍦?Stage4銆傛洿鏂?placement銆佹寚浠ゃ€侀『搴忔垨绔偣浣跨浉搴?Stage4 浜х墿鎸?freshness 澶辨晥锛屼笉鍥炲啓涓婃父绔犲銆?璁捐鎺ュ彈鍙巿鏉?B锛屼笉鎵撳紑鏈€缁?s4锛屼篃涓嶉『甯︾敓鎴?Context銆?
## 涓夈€丼tage4B锛歟vidence_binding.json v5

STAGE4_BINDING_INPUT_CONTRACT=stage4-chapter-binding-input-v4銆侭 淇濇寔涓€娆￠€昏緫绔犵骇璇锋眰锛?瀹為檯杈撳叆涓哄畬鏁存湰绔犲凡鎺ュ彈璁捐銆佸繀瑕佽妭鐐?浜虹墿/鍏朵粬涓讳綋鏄犲皠銆佺姸鎬?绾︽潫鍙婅垶鍙板拰璇佹嵁闂寘銆?鍘熸枃鍙寜璇佹嵁闇€瑕佸洖鍙栵紝鍏朵粬绔犳暣娈垫鏂囦笉榛樿杩涘叆銆傛ā鍨嬩笉寰楁敼鍙?A 鐨勯『搴忋€乸lacements銆佹寚浠ゃ€?鐩爣銆佺鐐规垨鏉冮噸锛涘洖绛斾粎瑙ｅ喅褰撳墠鏈烘鍩虹嚎鐣欎笅鐨勬涔夊苟鍙櫥璁?concerns銆?
novel/stages/stage4/plot_binding.py::build_mechanical_baseline 鏄敮涓€纭畾鎬у熀绾裤€傛瘡椤?delivery
鎸夌埗鑺傜偣璇佹嵁鍜?source_contexts 鏋勯€犲彲閫夎垶鍙帮紱绛惧悕绛変环鐨勯€夋嫨瑙勮寖鍖栵紝鐪熸涔変繚鐣欏叧闂紡鍊欓€夈€?澶氫釜 delivery 鍙互鍏辩敤鍚屾璇佹嵁锛屼笉寮鸿揩鏃犻噸鍙狅紝涔熶笉鎸夊潗鏍囪鐩栧甯冭涔夊畬鏁淬€傛湭鐭ユ椂灞?鍦扮偣/
鍦ㄥ満鑰呬繚鎸?null锛屼笉鑳界敱鍙備笌浜虹墿琛ユ垚鍦ㄥ満銆侭 鐨勮瘉鎹韩浠戒笌 A 鐨勫憟鐜拌韩浠藉垎寮€銆?
鏈烘绔犻浂 wire锛屾棤闇€鍋?checkpoint锛涙涔夌珷鍚勫崰绉佹湁 session銆佺ǔ瀹?purpose銆乴edger 鍜岄獙璇佸悗
checkpoint銆傞粯璁ゅ苟鍙戝彈鍐荤粨鑺傜偣/鍏ㄤ功/渚涘簲鍟嗛檺鍒躲€傞涓け璐ュ仠姝㈠悗缁帴鏀讹紝鎺掔┖宸插湪閫旂珷骞朵繚瀛?鍚堟牸鍥炵瓟锛涘彧鏈夊叏绔犵粦瀹氶綈鍏ㄣ€佸紩鐢ㄩ棴鍚堝拰鍏ㄤ功 HARD 閫氳繃鎵嶈仛鍚堟彁浜ゃ€備笉鍚屽€欓€夊洖绛旀垨鎹熷潖鏉ユ簮
涓嶈兘鎸夋渶鏂颁竴鏉￠€夊彇锛屽凡璐€欓€変紭鍏堝厤璐规仮澶嶃€?
妯″瀷澹扮О涓嶅彲缁戝畾鏃讹紝椤荤敱鍚屼竴璇佹嵁鐩綍涓婄殑纭畾鎬?witness 鏍歌瘉锛屼笉鑳借 B 鑷閲嶈鍒掋€?binding concerns 鏄粨鏋勫闃呮潗鏂欙紝涓嶆巿浜堟柊鍓ф儏鎴栦粯璐瑰姩浣溿€備汉宸?--apply-binding 浣跨敤鍚屼竴褰撳墠
typed schema 鍜岀紪璇戝櫒锛屾潵婧愯 human锛屼笉浼€?model/usage銆?
## 鍥涖€佹寮忚鍒?v6

### 4.1 缂栬瘧涓庨《灞?
鍚屼竴缂栬瘧鍣ㄥ皢 accepted A 涓?verified B 杩樺師涓哄畬鏁存寮忕珷璁″垝锛岀珷鍙蜂笌婧愮珷涓€涓€瀵瑰簲銆?Plan 涓嶅鍒?beat.content锛屽満鏅埌鑺傜偣鐨勫垪琛ㄤ粠 placements 娲剧敓銆傜粨鏋勩€佽瘉鎹拰鑱斿悎鍛堢幇浠嶉渶
鐙珛缁撴瀯瀹￠槄锛涢€氳繃鍚?Context 鎵嶅彲缂栬瘧銆?
### 4.2 PlanChapter 涓?PlanScene

绔犱繚瀛?output_ordinal銆乫rom_source_ordinals銆乻tructure_decision銆佷笁涓?goal銆?chapter_rewrite_brief銆乻cenes銆乥eat_placements 涓?anchor_bindings锛涙潵婧愯韩浠芥斁鍦?provenance銆?鍦烘櫙淇濆瓨 slot銆乸urpose銆乺ole銆乨elivery銆乼ransition銆乥udget_chars銆乧ontext_beat_refs銆乼urn_beat_ref銆?鍏蜂綋绫诲瀷涓?contracts/schema/stage4.py 涓?contracts/schema/plot.py锛屼笉缁存姢 fact 鍒嗛厤鎴栫浜屼唤鏄犲皠銆?
### 4.3 缁撴瀯鍚戦噺涓庡鎵逛富浣擄紙宸查€€褰?2026-09-15 鍏ㄨ嚜鍔ㄨ鍐筹級

STRUCTURE_REVIEW_CONTRACT 涓庣粨鏋?subject/閫愮珷 subject 韬唤淇濈暀涓哄瓨閲忎功鍘嗗彶瑙ｇ爜涓庡睍绀烘姇褰憋紱
涓嶅啀浜х敓鏂扮殑缁撴瀯鍐冲畾锛宻4 閿棤鐜板焦鍐欓潰銆傝瘉鎹綅缃€侀噸鍙犮€佹垚鍛橀棴鍚堝彧璇佹槑缁撴瀯锛屼笉璇佹槑鍓ф儏瀹屾暣
鎴栭殣鍚煡璇嗘湭娉勬紡锛涜繖浜涜涔夌粨璁虹户缁互 `unver`/INFO 鎶湶甯搁┗锛岄殢浜や粯鎶湶銆侭 鐨?typed
infeasible 缁堟€佹槸鍞竴鏈烘璐熺粨璁猴紝鍏跺悗缁ф槸棰勭畻鍐呰嚜鍔ㄩ噸閲囷紙BACKEND_CONTRACT R-5.20/R-5.26锛夈€?
### 4.4 PlanProvenance

compiled Plan 绮剧‘缁戝畾褰撳墠 A/B 鐗堟湰鍖栨枃浠跺畬鏁?SHA 鍜屽疄闄呮秷璐逛緷璧栵紝涓嶅鍒?B 鐨勬ā鍨嬭垂鐢ㄣ€?浜哄伐鏇挎崲璁?human銆備笟鍔℃秷璐规姇褰卞彉鍖栦笌浠呮枃浠?璇佹槑鍙樺寲鍒嗗紑锛屼簩鑰呴兘涓嶈兘缁曞紑绮剧‘ A/B 閾撅紱
鏈煡鏉ユ簮涓嶄綔 fresh銆傚巻鍙查噰鐢ㄨ垂鐢ㄤ繚鐣欏師 ledger 鍙婃潵婧愬叧鑱旓紝涓嶉噸澶嶈鍏ユ柊璋冪敤銆?
## 浜斻€亀riter_context.json v10

PlannerTruthView 鐢ㄤ簬鍘绘簮搴忚璁★紱WriterVisibleView 鐢卞綋鍓嶇珷璁″垝纭畾鎬х紪璇戙€傚叡浜富浣撶殑
鍙鎬у彧鎸?Global 鐨勭珷绾ф彮绀哄瓧娈碉紙`revealed_from` 绛夛級涓庤摑鍥捐嚜韬鐐瑰垽鏂紱鍘?KnowledgeLinks
鐭ヨ瘑浜嬩欢涓庨€愬満 `knowledge_event_refs` 宸查殢 Stage2 璇箟瀹￠槄閫€褰癸紙v10 鍒犻櫎 `WriterKnowledgeEvent`锛夛紝
鍚岀珷瀹夋帓鐢?Stage4 鐜版湁璁″垝琛ㄨ揪锛屼笉鍐嶅绉版満姊拌瘉鏄庢姭闇蹭笌鐭ユ儏姝ｇ‘銆?绔犵骇 beats 鐩綍淇濆瓨涓€娆＄埗鑺傜偣姝ｆ枃锛屽悇 scenes[].deliveries 鍙紩鐢ㄨ妭鐐瑰拰鍛堢幇鎸囦护/鑸炲彴锛?骞跺甫蹇呰 state_events锛坄state_ref` + `established/ended` 绔偣锛夈€乧onstraint_refs 涓?context_beat_refs銆?
宸茬櫥璁扮姸鎬佹寜鏄惧紡绔偣缂栬瘧閫愬満璁稿彲銆傚皻鏈畬鎴愮殑鍒嗗満鑺傜偣涓嶈兘褰撳畬鏁磋儗鏅笅鍙戙€?State modality 鍙鏄庡懡棰樻€ц川锛屼汉鐗╀俊蹇?澹扮О涓嶈兘鍗囩骇涓轰笘鐣岀湡鐩搞€係hared Global 姝ｆ枃鍙湪鏈?褰撳墠浜嬪疄璁稿彲鎴栨槑纭?belief/claim/unresolved 鐘舵€佽鍙椂杩涘叆鏉愭枡銆?
Stage5 涓€娆＄敓鎴愭暣绔狅紝妯″瀷鍚屾浼氳鍚庡満鏉愭枡銆俛llowed_slots 鏄緭鍑虹害鏉燂紝涓嶆槸鐗╃悊闅旂锛?缂栬瘧鍣ㄤ笉閫氳繃瀛楃涓茶鍓殣钘忕瀵嗭紝涔熶笉澧?visible_content銆傝嚜鐢辨枃鏈仐婕忋€侀噸澶嶃€侀敊璇綊鍥犱笌
鎻愬墠鎻ず鐢辨棦鏈夊闃呭強鎴愮妫€鏌ユ壙鎷咃紝涓嶅绉版満姊版鏌ヨ兘璇佹槑璇箟瀹夊叏銆?
Context 鍙湪褰撳墠鏈€缁堢粨鏋勬帴鍙楀悗鐙珛闆舵垚鏈敓鎴愩€傚畠鐨勭洰褰曘€佸紩鐢ㄣ€佹潗鏂欏拰璁稿彲涓庡悓婧愮紪璇戠粨鏋?閫愰」瀵硅瘉锛涚粨鏋勯€氳繃涓嶈兘鏇夸唬鐙珛 writing 鎺堟潈銆?
### 5.1 韬唤涓庢姭闇叉秷璐?
鍐呴儴 `ActorRef {char_id,presentation_ref?}` 鍦?Beat 鍙備笌浜恒€丼tate 鎸佹湁鑰呫€佺煡璇嗗彈浼椼€?鍙欒堪/瑙嗙偣銆佽瀵熻€呫€佽垶鍙板湪鍦鸿€呫€佷汉鐗╃洰鏍囦笌鍏崇郴涓ょ鐨勬瘡涓疄闄呬娇鐢ㄤ綅缃棴鍚堛€備竴涓疄浣撴湁澶氫釜
琛ㄩ潰韬唤鏃跺繀椤绘寚鏄庡憟鐜帮紱鍚屼竴 Beat 鍙互鍚屾椂鎼轰袱涓笉鍚屽憟鐜般€傛櫘閫氬凡鍏紑鏄电О灞炰簬鍚屼竴涓?ActorBinding锛屼笉鍥犵О鍛间笉鍚屽垱閫犱袱涓韩浠姐€侭lindReferenceMaps 鐨?participant_aliases 鏄犲埌
瀹屾暣 ActorRef锛屼笉鑳芥寜 char_id 鍘婚噸锛汸lanGoal 鍚屾牱淇濈暀瀹屾暣鍛堢幇寮曠敤銆?
WriterContext 鍙笅鍙戠嫭绔嬬殑 opaque `actor_ref` 涓庡叕寮€绉拌皳锛屾墍鏈変汉鐗╀綅缃娇鐢ㄥ悓涓€鐩綍锛?涓嶄笅鍙?char_id銆乸resentation_ref銆佸弽鍚戞槧灏勬垨鍏卞悓瀹炰綋鍒嗙粍銆備笉鍚屽憟鐜伴粯璁や娇鐢ㄤ笉鍚?token锛?鍏崇郴鍛介鐨勫叕寮€鏍囪瘑涔熺粦瀹氬畬鏁村憟鐜帮紝涓嶈兘閫氳繃鍏卞悓 rel_ID 娉勫簳銆傛樉寮忚〃闈㈣韩浠藉彧鍙紩鐢ㄦ湁鎹?鍏紑 State 鐨勮鑹叉弿杩帮紱role 鍙湪 role_allowed_slots 璁稿彲鍦烘櫙琛ㄨ揪锛屼笉缁ф壙鐪熷疄韬唤鐨?role銆?瑙掕壊鎻忚堪杩樺彈璇?State 鐨勬垚绔嬨€佽В闄ゅ拰鏇夸唬绔偣绾︽潫锛氳В闄ゅ満鍖呭惈鍦ㄦ湁鏁堢獥鍙ｅ唴锛屼箣鍚庝笉鍐嶄綔涓?褰撳墠瑙掕壊鎻忚堪銆係tate 鏈韩浠嶆寜鎶湶璁稿彲淇濈暀涓哄巻鍙插懡棰橈紝涓嶈兘鍥?role 澶辨晥鍒犻櫎鍘嗗彶浜嬪疄銆?缂哄け/姝т箟淇濈暀鍊欓€夊苟浜?Blueprint/Plan owner 鍏嶈垂璁㈡锛屼笉鎷╅椤规垨鍥為€€ canonical銆?
璺ㄧ珷鏅€氱О璋撳彧鏈夊湪涓や釜缁戝畾鍚勮嚜鑷冲皯涓€涓櫥璁扮О璋撶殑 identity_revealed_from 涓ユ牸鏃╀簬
褰撳墠绔犮€佷笖鐧昏褰掑睘鐩稿悓鏃讹紝鎵嶅綊涓€涓哄悓涓€鍏紑 token锛涗笉瑕佹眰閲嶅鐨勫瓧闈㈢О璋擄紝涔熶笉涓烘櫘閫氭樀绉?鍒堕€犺韩浠戒簨浠躲€傚綊涓€淇濈暀鍙屾柟鏈夋嵁绉拌皳銆傛瘡涓О璋撳彟鏈?surface_allowed_slots锛氭鍓嶅師鏂囧凡鍑虹幇鐨?绉拌皳娌垮綋鍓嶄汉鐗╀娇鐢ㄨ寖鍥达紱鏈珷棣栨鍑虹幇鐨勭О璋撲粠鏀寔瀹冪殑瀹為檯 Beat 鍛堢幇鍦哄紑濮嬶紝涓嶈兘鍊熷悓涓€
actor_ref 鐨勬棭鍦轰娇鐢ㄦ彁鍓嶅紑鏀俱€傝鑹叉弿杩颁粛鏈嶄粠鍚勮嚜鍏紑 State锛屼笉鍚岃鑹叉弿杩颁笉鑳芥嫨棣栭」銆?
绉拌皳瀛楅潰鍙嚭鐜颁笉浠ｈ〃韬唤鏄犲皠宸茬煡锛涘悓浜哄叧绯诲懡棰樻寜缁戝畾鍊欓€変笌绔犵骇鎻ず瀛楁鍒ゅ畾锛屾棤鐙珛
璁稿彲绔偣銆傝法绔犺儗鏅拰涓婄珷鏀舵潫娌垮師鍏紑 token 鎼哄甫蹇呰鍚嶅唽锛屼笉闄勫唴閮ㄦ槧灏勩€?缁ф壙 State銆佽儗鏅?Beat 鍜屽巻鍙插彈浼楁寜寮曠敤鎵€灞炵珷瑙ｆ瀽缂虹渷鍛堢幇锛屽啀鍐荤粨鍏跺叕寮€韬唤锛?鍚庣珷澧炲姞鍙︿竴绉嶈〃闈㈣韩浠戒笉浼氫护鍘熸潵鍞竴鐨勬棫寮曠敤姝т箟锛屼篃涓嶄細鎶婃棫韬唤鍚告敹鍒版柊鍛堢幇銆?褰撳墠绔犵殑缂虹渷寮曠敤鍒欎娇鐢ㄦ埅鑷宠绔犲師鏂囧凡鍑虹幇鐨勭櫥璁扮О璋撳€欓€夛細鍗充究鍙湁涓€涓樉寮忕粦瀹氾紝
鑻ュ€欓€変腑浠嶆湁鏈寘鍚殑绉拌皳锛屼笖绉拌皳闂磋韩浠芥槧灏勬湰绔犳垨鏈潵鎵嶆彮绀猴紝缂虹渷寮曠敤浠嶉』鎷掔粷锛?鍙櫥璁版棫韬唤鎴栧彧鐧昏鏂拌韩浠介兘涓嶈兘鎺掗櫎鍙︿竴绉嶅凡缁忓嚭鐜扮殑鍊欓€夈€?Blueprint銆佺洸瑙勫垝銆佺粦瀹氫笌 Writer 鍏辩敤璇ュ垽鎹紱鐪熸鍞竴鐨勬鍓嶅叕寮€缁戝畾浠嶅彲鐪佺暐 presentation銆?鍘熸枃鍙緵绉佹湁鍊欓€夎В鏋愶紝涓嶅洜杩欓」鏍￠獙杩涘叆鐩叉ā鍨嬭姹傦紱鏈嚭鐜扮殑鏈潵鍒悕涓嶄娇鏃╃珷璇姤姝т箟銆?褰撳墠缁戝畾鍊欓€変粎鍚湰绔犲０鏄庡拰涓婁竴绔?carry_out 瀹為檯鎻愪緵鐨?State锛忕粦瀹氶棴鍖咃紱涓嶆妸鍏朵綑鍘嗗彶
缁戝畾鎵╂垚褰撳墠姝т箟锛屼篃涓嶅厑璁告樉寮忓紩鐢ㄦ湭鎻愪緵鐨勬棫缁戝畾銆傛惡鍏?State 鐨勫師绔?null 鑻ユ湁鍞竴缁戝畾锛?鎼哄叆鏉愭枡淇濈暀鍘熷紩鐢ㄥ苟甯﹀嚭瀹為檯缁戝畾鍙婅瘉鎹紝鍚庣画娑堣垂鑰呬粛鎸夊師绔犲喕缁撹В鏋愩€?
鍏崇郴/瑙勫垯鐨?effective_from銆乪ffective_until锛堝寘鍚绔狅級鍙弿杩颁笘鐣屾垚绔嬬獥鍙ｏ紱revealed_from
鎺у埗璇昏€呮彮绀哄弬鑰冿紝涓嶈姹備袱绉嶆椂闂村厛鍚庛€傚凡缁忕粨鏉熺殑浜嬪疄鍙互浣滀负鍘嗗彶鎶湶锛?鎻愬墠閫忛湶鏈潵浜嬪疄涔熷悎娉曘€傝鍒欏紩鐢ㄧ簿纭埌 rule_id/version_id锛泂ecret.fact_ref 灞曞紑鍏崇郴鏃跺繀椤绘湁
鍞竴鏄庣‘鐨勫疄闄呭叧绯诲憟鐜帮紝缂哄け鎴栧涔変笉鐚滅鐐广€傚畬鏁磋嚜鐢辨枃鏈笉鍋氭満姊拌瘉鏄庯紝灞炶涔夋湭瀹℃姭闇层€?
鍏崇郴鎶湶鎸夎摑鍥惧叧绯?use 鐨勪袱绔憟鐜扮粦瀹氬叕寮€绔偣锛氫紭鍏堝彇鏄庣‘鏀寔 State 鐨?global_ref锛涗粎鏈?Beat 鏀寔鏃讹紝蹇呴』鍦ㄥ叾鏉ユ簮绔犲疄闄呭叧绯?use 涓敮涓€瀹氫綅銆傞粯璁ゅ唴閮ㄥ疄浣撶鐐?涓嶈兘鍏呭綋鎶湶璇佹槑銆傚綋鍓?use 涓庡凡鍏紑绔偣涓嶅悓鏃讹紝涓ょ椤诲悇鑷弧瓒冲敮涓€缁戝畾鍒ゆ嵁鎵嶅彲鍚屼竴涓嬪彂銆?娌℃湁鏄犲皠鍒欎笉涓嬪彂璇ュ憟鐜扮殑鍏崇郴鐪熺浉锛涙敮鎸佺己澶辨垨澶氫箟浜?Blueprint/Plan owner 鍏嶈垂璁㈡銆?
`Blueprint.relation_uses` 鍙０鏄庢湰绔犲疄闄呭叧绯讳娇鐢ㄧ殑涓ょ鍛堢幇锛屼笉閲嶅鍏崇郴姝ｆ枃鎴栧己閫?State銆?浠呰褰曚簨瀹炴垚绔嬨€佹病鏈夋湰绔犲疄闄?use 鐨勪簨浠跺彲淇濈暀鍐呴儴
鏃堕棿閿氾紱鍚屼竴鍏崇郴瀛樺湪澶氫釜鏄庣‘ use 鏃跺叏閮ㄥ垎鍒紪璇戯紝涓嶈兘鎷╅椤规垨鎸夊疄浣?ID 鍚堝苟銆?
Stage5 鏈€缁堣姹傚彧娑堣垂姝ゅ叕寮€浠诲姟涔︺€傚叏閲忕瀵嗐€佷汉鐗╂娴嬪熀绾垮拰鍘熸枃淇濈暀涓虹鏈夐獙璇佽緭鍏ワ紝
涓嶈繘鍏?Writer 鎻愮ず銆傝瘉鎹?瀹¤鍙樺寲鍙互閲嶅紑鍙楀奖鍝嶅闃咃紝浣嗕笉鏀瑰彉瀹為檯鏉愭枡鏃朵笉鍒堕€犳棤鍏充粯璐归噸鎶姐€?鏂扮増 Context 缁忎弗鏍艰浇浣撳拰鍚屾簮缂栬瘧妫€鏌ワ紱鏃?prepared/鍥炵瓟鐨?schema銆佽緭鍏ユ垨浠ｇ爜韬唤涓嶇鏃?鍙繚鐣欐潵婧愶紝涓嶈兘闈犻粯璁ゅ瓧娈垫垨鏃у鎵归潤榛樻仮澶嶆垚鏂扮増鏈€?
## 鍏€佺粨鏋勫害閲忎笌浜哄伐闂?
缁撴瀯璇绘暟鎸夊畬鏁磋妭鐐瑰強瀹為檯 placements 璁＄畻锛涘鍦哄憟鐜颁笉鎸夊師瀛愬垎鍖虹畻娉曞伔鍋峰睍寮€鎴愬瓙浜嬪疄銆?鎶湶椤哄簭浣跨敤鏄庣‘绔偣锛屼弗鏍煎彲姣旇緝鐨勬簮璇佹嵁鎵嶈繘鍒嗘瘝锛涙湭鐭ャ€侀浂鍒嗘瘝鍜屼笉鍙瘮淇濈暀 unver銆?source_contexts 鏁伴噺/璇佹嵁閲嶅彔/鍦烘櫙鏁伴噺涓嶈兘浣滀负璇箟瀹屾暣鎴栭噸鏋勪紭鍔ｇ殑鑷姩闂ㄣ€?璇绘暟闅忓綋鍓嶄唬鐮佽绠楋紝涓嶅啓鍥炶鍒掋€佷笉娓呯┖鍘熸帴鍙楀巻鍙层€?
## 涓冦€佸け璐ヤ笌鎭㈠

| 鐘舵€?| 鍏佽鐨勬仮澶?| 绂佹鐨勬嵎寰?| 璇佹嵁 |
|---|---|---|---|
| A 鐨勭瓥鐣ユ垨鍗曠珷鍝嶅簲鏆傚仠/鎷掓敹 | 鍚屼竴 `run_id` 鎭㈠锛屽彧琛ュけ璐?缂哄け unit锛涘凡淇濆瓨鍥炵瓟鍙厤璐归噸鍒ゅ苟鑱氬悎锛屾棫鏁翠功鎷掔閲囩敤鍏ュ彛宸查€€褰?| 閲嶅彂宸查獙 unit銆佸叕寮€ `--chapters`銆佹嬁鏃ф暣涔﹀叆鍙ｅ啋鍏?unit adoption銆佽ˉ瀛楁鎴栭儴鍒嗚仛鍚堝啋鍏呮寮忓€欓€?| 鐮伮峰 |
| B 钀界洏 typed infeasible 缁堟€侊紙浜哄伐瀹￠槄绁ㄥ凡閫€褰癸級 | 闃熷垪鎶婇绠楀唴 `stage4.design --replan` 鎺掓垚鏅€氫粯璐?todo锛沗approvals.cast_infeasible_replan_scope` 閾?`replace_reconstruction_strategy` 鍏抽棴寮忕害鏉燂紝鐢熶骇鑰呰蛋鍚屼竴 typed 閾俱€乧heckpoint 涓庢満姊板楠岋紝鍙噸鍙?effective 杈撳叆鍙樺寲鐨勭珷锛涢绠楄€楀敖鍗?`WorkUnitBudgetExhausted` 缁堟€?| 鍦ㄤ慨鍓?producer run 鍐呯画鍐欍€佹妸鍗曠珷鑰楀敖鑷姩鍗囩骇鎴愭暣涔?replan銆佹妸榛樿妯℃澘鐞嗙敱褰撶湡瀹炲弽棣堛€佸瓧娈?patch銆佹棤 witness 闅忔満閲嶉噰鏁翠功 | 鐮伮峰 |
| A repair/replan aggregate 宸?prepared锛宻ource producer 宸茬粨鏉?| operator 鍙彂甯?raw/self 鍙?SHA 缁戝畾鐨?`--recover-repair-aggregate`锛涙柊 recovery run 鍦ㄤ换涓€鍐欏墠澶嶆牳 source 鐢熷懡鍛ㄦ湡/璐?鍝嶅簲涓庡綋鍓?compiler/HARD锛屽箓绛夎ˉ candidate/adoption/audit/committed | 閲嶈В stale review token銆侀噸鍞?Reviewer/Executor銆佺画鏃?provider銆佹寜 newest 鎸?journal銆佷粠澶氫唤/鍙屽壇鏈?绗笁鎬佷腑鐚?winner銆佸厛鏀瑰€欓€夊啀楠岃处 | 鐮伮峰 |
| B 绔犳睜閬囧埌鍙仮澶嶄氦浠樺け璐?| 褰撳墠 run 鐢变弗鏍?`delivery_recovery.json` 绯荤粺鏆傚仠骞跺彧琛?pending锛涘凡 ended 鐨勬棫 run 鐢辨柊 run 閫愮珷涓ユ牸澶嶉獙鍚庨噰鐢紝鍏佽澶氭潵婧愬拰鍏ㄩ儴閲囩敤锛沺repared 灏剧獥浼樺厛鍏嶈垂琛ュ熬 | 棰勬彁浜ゅ叏閮ㄧ珷鑺傘€佹妸鏅€?exit 2 娲楁垚 paused銆佸娲?ended parent銆佸鍒?checkpoint銆佺画鏃?provider session銆佸湪鍐茬獊涓氬姟鍥炵瓟闂寸寽涓€涓€佹妸鏈煡鏉ユ簮褰撴湭鍛戒腑銆佹紡璁板巻鍙?usage | 鐮伮峰 |
| 妯″瀷澹扮О B `infeasible` | 鍚?session 缁撴瀯鍖栧弽棣堣姹?bound锛涚湡瀹炰唬鐮?witness 鍙惤姝ｅ紡缁堟€侊紱浜﹀彲鎻愪氦 `--apply-binding` 鎴栧畬鏁?v5 浜哄伐璁″垝 | 淇′换妯″瀷鑷瘉銆丅 绉佹敼 A銆佽嚜鍔ㄧ敓鎴?A2銆佽姹傚凡鏈?Plan 鎵嶇粰棣栨鎭㈠ | 鐮伮峰 |
| 璁″垝钀?`.INVALID` | 淇 v5 `plan_edit.json` 鍚?`--apply-edit`锛屾垨 `--revalidate --actor --reason` | 鏀瑰悕鎻愬崌銆佺粫杩?schema/鍒ゆ嵁/褰掓。 | 涓夋簮 |
| Context 缂哄け鎴?stale | 鏈€缁堢粨鏋勫叏绔?accept 鍚庣敤 `stage4.context` 鍏嶈垂閲嶅缓 | B/璁″垝淇鏃舵彁鍓嶄唬鍐欙紝鎴栫敤鏃т笂涓嬫枃缁х画鍐?| 涓夋簮 |
| 鏃х増鏈?| 浠?producer 閲嶅缓锛涗汉宸ョ閲嶅啓涓?v5 | 杩佺Щ銆佺寽瀛楁銆佹墜琛ョ増鏈彿 | 鐮伮峰 |

## 鍏€佺幇琛屽垽鎹?
褰撳墠娉ㄥ唽琛ㄥ叡 **29 鏉?*锛?*15 hard銆? info銆?0 metric**銆傚敮涓€娉ㄥ唽琛ㄤ负 contracts/judges/stage4.py銆?
| 鍒嗙粍 | 褰撳墠瑙勫垯 |
|---|---|
| 绔犲満涓庡憟鐜?| `one_to_one` 路 `ordinal_seq` 路 `empty_chapter` 路 `placement_ref` 路 `scene_transition` 路 `goal_landing` |
| 棰勭畻涓庡啓娉?| `budget_int` 路 `budget_band` 路 `plan_book_band` 路 `style_words` 路 `verbatim_starved` |
| 鏉ユ簮涓?Context | `phase_artifact_link` 路 `context_link` 路 `ctx_plot_exact` 路 `ctx_name_attested` 路 `ctx_permission_exact` |
| 璇箟涓庤垶鍙版彁绀?| `presentation_semantics` 路 `causal_disclosure` 路 `stage_signature_conflict` |
| 搴﹂噺 | `scene_budget_distribution` 路 `verbatim_share` 路 `partition_change` 路 `disclosure_order` 路 `noncontiguous_regroup` 路 `anchor_migration` 路 `structure_eligibility` 路 `structure_alignment` 路 `semantic_closure_reading` 路 `ctx_source` |

### 涓婃父灞€閮ㄩ噸寤轰笌鍚堟硶鐖惰妭鐐?
褰撳墠 A producer 鍦?provenance 淇濆瓨 `design_dependencies`锛氱瓥鐣ュ拰閫愮珷鏅€氳姹傜殑瀹為檯渚濊禆鎽樿銆?瀹忚绛栫暐瀹￠槄鍚屾牱娑堣垂 `build_blind_strategy_projection`锛沗basis.truth_sha256` 淇濈暀鏁翠功璇佹嵁蹇収锛?涓嶅啀鍗曠嫭浠ゅ畯瑙傜瓥鐣ユ垨鏃犲叧绔犱箟鍔″け鏁堛€傚€欓€夈€佽瘉鎹€佺瓥鐣ャ€佸€欓€夐泦鍚堛€佽竟鐣屾垨涔夊姟瀹為檯鍙樺寲浠嶉』澶嶅銆?
姝ｅ紡鍊欓€夋槑纭?stale 鏃讹紝鏅€?A 閲嶅缓鍏堢敱鍏跺敮涓€ committed aggregate銆佸畬鏁存枃浠?SHA 鍜屽叏閮ㄦ潵婧愰摼
纭鍚堟硶鐖惰妭鐐广€傚疄闄呬緷璧栦笉鍙樼殑鍗曞厓淇濈暀璇ョ埗鑺傜偣涓殑鏉ユ簮锛屽寘鎷汉宸ョ珷鏇挎崲鍜?typed 淇锛涘彉鍖栫珷
鏀跺埌褰撳墠鐖惰璁″強鏂板墽鎯咃紝鍐嶈蛋鍚屼竴 compiler 涓?HARD銆備汉宸ユ寮忓埆鍚嶄笌妯″瀷灞€閮ㄥ埆鍚嶉€氳繃缂栬瘧寰€杩?鐩哥瓑鏍￠獙杞崲锛屽師浜哄伐鏉ユ簮銆佺悊鐢卞拰璐圭敤涓嶆敼鍐欍€傛病鏈変緷璧栧嚟鎹垨鏉ユ簮涓嶉棴鍚堟椂淇濇寔 stale/unknown锛?涓嶅彇淇鍓嶆棫缂撳瓨瑕嗙洊褰撳墠鐖惰妭鐐广€?
鑱氬悎 journal 鐨?`finalization_payload.rebuild` 鍐荤粨鐖惰妭鐐硅韩浠戒笌淇濈暀婧愬叏闆嗭紱鏅€氭柊鍥炵瓟銆佸巻鍙查噰鐢ㄥ拰
淇濈暀婧愬繀椤绘棤閲嶅彔瑕嗙洊绛栫暐鍙婂叏閮ㄧ珷銆傛彁浜ゃ€佸厤璐?prepared 鎭㈠涓庝笅娓?repair/human 娑堣垂楠岃瘉鍚屼竴鏉ユ簮
闆嗗悎锛岄噸寤轰笉娓呯┖宸叉彁浜や慨澶嶈疆鏁版垨鏈叧闂箟鍔°€備笂娓稿彉鍖栬嫢鍙戠敓浜庡悓涓€ Agent epoch锛孲upervisor 鍏?鍏抽棴骞?drain 宸叉彁浜?producer 鐨勬棫 epoch锛屾崲鏂拌韩浠藉悗閲嶈 next/token锛岃竟鐣屾湰韬笉璋冪敤 Reviewer銆?
鍘熷洖绛旇惤鐩樻棭浜?checkpoint 鐨勭獥鍙ｇ敱 `stage4.design --recover-responses` 鍏嶈垂闂悎銆俼ueue 鐨勫彧璇诲彂鐜颁紭鍏堜簬鏂扮殑鐢熸垚鍜岃仛鍚堬紱鎵ц鎸佸師 owner 鍋滄閿侊紝浠ュ師璇锋眰銆佸師瀹屾暣閰嶇疆鍜岃垂鐢ㄥ洖鎵ф仮澶嶅師 checkpoint銆傛寮忓€欓€変粛鐢变笅涓€娆＄‘瀹氭€ц仛鍚堟彁浜わ紝澶辫触鎴栨崯鍧忎繚鐣?unknown銆?
### 涓婃父鐪熷疄缂洪櫡鐨勪緥澶栬矾寰?
璁捐鎴栫粨鏋勫闃呭彲鎻愪氦 BlueprintRefinementRequest锛氬綋鍓?origin/token銆佺珷鑺傘€佺淮搴︺€佷富浣撳強鍏蜂綋鐤戠偣銆?A 鍙彲鐢ㄥ綋鍓?opaque 涓讳綋鎴?chapter锛汢 鍙敤鏈珷姝ｅ紡涓讳綋銆備唬鐮佸畾浣嶈瘉鎹苟淇濆瓨绋冲畾瀹為檯闂锛?涓嶄細鍥犱负瀹氫綅鍒拌瘉鎹氨瀹ｅ竷闂鎴愮珛銆傝浆浜ゆ湰韬浂璋冪敤锛屽彧璁╄摑鍥?owner 鐨勪慨璁㈠叆鍙ｉ噸鏂板弽鏌ヨ绔?锛堝師 Blueprint Commander 瀹￠槄宸查殢 2026-09-15 瑙掕壊閫€褰癸級銆?
浜哄伐鍙挙閿€涓嶆垚绔嬬殑鐤戠偣锛涚‘璁ゆ紡鎶姐€侀敊鎶姐€侀噸瑕佹彮绀?鐭ヨ瘑/鐙珛渚濊禆杈圭晫缂哄け鏃讹紝鎵嶆寜褰撳墠
Stage2 缂栬緫鎴栧畾鍚戠敓鎴愬叆鍙ｄ慨璁€侲xecutor 鍐欏洖涓嶈兘鑷姩瑙ｅ喅闂锛屼粛闇€鍚庣画浜哄伐瀹￠槄澶嶆牳銆?鏃犳嵁鐨勬柊鍓ф儏涓嶈兘鍊熻浆浜よ繘鍏ョ湡鐩稿眰銆俥xact 杞氦閲嶆斁鍙瘉鏄庡凡淇濆瓨锛屼笉瑕嗙洊鍚庣画鎾ら攢/瑙ｅ喅锛?涓嶉噸缃?scope 鎴栦功绾х疮璁￠绠椼€?
