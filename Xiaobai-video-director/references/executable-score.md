# 可执行导演谱 v2

目标：从“写一份计划、再自由画另一份视频”改为“模型产生状态，演出与检查消费同一状态”。成功参考是消息队列中同一 FIFO 模型同时决定请求位置、占用、阻塞和结果；复制的是这种执行关系，不是模板外观。

## 数据流与责任

材料 → 聚焦/故事与声音选择 → 短句口播与动作设计 → prepare 的原生音轨/对齐 → score.json → score-check 编译逐帧状态 → score-render 的共享 Remotion/单次实测混音 → 实片抽帧与连续播放复核 → score-release。

- Agent 判断内容含义、隐喻、主线、画面重点、镜头与音效是否有用。
- 编译器校验身份、时序、机制前置条件、不变量、显隐、连续位置和边界；不判断观众是否理解。
- 共享渲染器只显示编译状态；不允许 run 自写 JSX 或独立计时来绕过执行器。
- 审查者必须观察真实结果；不能把自报状态当成观看凭据。Agent 能看听时自动复核；无法观察或两轮仍失败时如实停在候选状态。

## 唯一输入

可运行字段示例在 `assets/score.example.json`，包含 `score` 和不联网的测试 `props`。生产使用 `prepare` 产生的真实 props，不能拿夹具时间代替原生对齐。没有 Schema 生成器或额外运行时。

`score.json` 顶层为 `version:2`、`title`、`model`、`objects`、`clauses`、`actions`、`sounds`。

### 机制与对象

`model.initial` 保存有限数值初态；`derived` 按依赖顺序用字段名、数字、`["+"|"-"|"*", a, b]` 计算，不接受代码执行。计数或读取快照可在 `model.discrete` 声明，直到动作完成才提交新值，避免把整数读数画成浮点噪声。`invariants` 每项有 `name/after/expression/equals`，在指定动作完成后的所有帧检查。例如两个等量反向仓位同时成立后，净方向仓位和假设同幅价格变化的合计盈亏应为零。

`objects` 用稳定 id 索引：

- `noun` 与 `label` 必须一致，名词来自原口播；不能把“代码”标成“项目”。
- `shape` 当前支持 `market/robot/ticket/meter/brace`；这是已有 Sprite 能力，不是题材分类。含义不匹配就补一个共享能力和失败回归，不硬套。
- `box:[x,y,w,h]`、`color:teal/gold/red/ink`、`labelPlacement:above/inside`、`layer`。文字槽由共享 Sprite 和编译器统一计算。
- 容器的 `receiver:[dx,dy,w,h]` 是相对自身的真实接收区；运输根据区中心计算落点，不另抄终点。
- `value` 绑定 `field`，可有 `showField/prefix/suffix/signed/decimals`；后来才成立的信息由模型揭示。`statusField` 绑定已发生的完成状态；机器人 `enabledField` 控制眼睛与电源开关，仪表 `validField` 显示核对通过/异常。字段必须是模型中的0–1状态。数值内部揭示通过同一字段渐进展开。

当前只支持同一个视觉对象一次出生、一次完整退场；重入要明确新实例或扩展共享生命周期，不能悄悄写渲染特例。逻辑存在与视觉存在分开：退出市场图形不等于把仓位清零。

### 分句与注意力

每个 `clauses` 对应一条真实 caption，`text` 完全一致，每句当前限32字；超出先拆语义或改稿，不拉长静态画面。

- `id/focus/visible`：一个焦点，包含背景最多五个逻辑对象。五个是上限，不是目标。
- `relations` 为 `{from,to,type}`，类型为 `observes/executes/contains/compares/offsets/produces`；所有可见对象必须通过关系连到焦点。声明连通不能替代实片中关系是否直观的复核。
- `camera:{x,y,zoom}`：共享执行器在句界连续过渡；仅在真正转移注意力时改变目标。
- `coverage` 按原句顺序逐字覆盖。每段为 `{text,kind}`：`entity` 绑定 `object` 和可选出生 `action`；`action` 绑定执行动作 id；`context` 必须写排除理由且不能吞掉大半正文。每句至少一个执行过程或有理由的停留。修辞可以不新增物体，但不能把核心名词/动词藏进 context。

### 动作

每个动作有 `id/kind/cue/targets/duration`，`cue` 来自供应商时间轴；`delay` 非负，不能提前抢跑。时长0.25–4秒，动作在所属分句内落定。

- `reveal/exit`：出生与完整卸载，仅承担出现/退场。
- `transfer`：给 `source/destination`，从来源接收区到目标接收区，主角保持身份。要改机制就同时声明 `effects`，到达后才提交，途中不算成交。
- `change`：必须通过 `effects:{field:{from,to}}` 改真实模型，且字段被目标图形属性实际消费；不接受仅换字。执行器必须让变化有可辨认过程，不能用更新一个不可见字段冒充动作。
- `move`：`fromBox/toBox/reason` 明确注意力重排，起点必须继承上一姿态；它不能冒充“存入”。
- `connect`：真实连接两个已存在对象，可用 `valueField` 显示模型派生结果，`valueLabel/valueSuffix` 明确合计或差额及单位；数字统一限制精度，不能露出程序浮点尾数。
- `hold`：最多2秒并说明为什么要停留，不能靠停顿凑片长。

清场或聚焦动作可用 `stagingFor` 指向本句已有的语义动作，并写明理由；只允许 move/exit，不能修改机制字段，也不能替代名词/动词覆盖。

`after` 指向必须已完成的动作；`requires` 写前置模型字段值。比如“买入对冲”必须等空单成交，不能把“计划发单”画成“双腿已持有”。不支持的字段或动作直接失败，不能静默忽略。

## 声音

TTS 只产生人声。每个 `sounds` 项绑定 `action/phase:start|end/src/relativeDb`；源文件在本 run 内，不另写时间。只给有意义的状态变化配声，默认不为淡入、文字或镜头移动配声。

脚本实测附近口播与源音效 RMS，一次计算到低于口播6–16dB；检查峰值、长度及0.8秒事件间距。先混成一个 PCM master；Remotion 输出画面，ffmpeg 将 master 一次编码封装进 MP4，避免二次衰减和音频拼接的编码延迟。交付时比较最终视频解码音轨与 master，并从已知人声中分离出音效贡献，检查每个音效窗口。数值匹配不能代替实际可听性与风格匹配。

## 四个命令

在 Skill 目录运行，RUN 替换为具体绝对路径：

```sh
uv run --locked python scripts/director.py score-check --score RUN/score.json --props RUN/props.json
uv run --locked python scripts/director.py score-render --score RUN/score.json --props RUN/props.json --candidate RUN/candidate-v1.mp4
uv run --locked python scripts/director.py score-review --score RUN/score.json --props RUN/props.json --candidate RUN/candidate-v1.mp4
uv run --locked python scripts/director.py score-release --score RUN/score.json --props RUN/props.json --candidate RUN/candidate-v1.mp4 --qa RUN/candidate-v1-review/review.json --final RUN/final-v1.mp4
```

`score-render` 编译所有帧并绑定模型、原生音频、props、渲染器、音效和混音。旧 `release` 已阻断，旧 Gate 仅用于历史诊断。

`score-review` 提取每个动作的 before/mid/settled 帧，检查像素存在、标签槽及安全区，生成 **pending** 的审查文件。必须逐句填写实际观察及为何解释口播，逐项填写全部已有经验关联动作与观察，并真实连续播放检查动作衔接、注意力和声音。禁止自动生成通过文案。

`score-release` 重新编译并验证哈希、完整审查、实际帧像素一致性、最终嵌入音轨及媒体可解码性；失败不复制最终文件。新源码、新模型、新声音或经验规则变了，旧验收失效。当前哈希是防止误用旧产物的完整性绑定，不是对抗本机有写权限者伪造审查的签名系统。

## 能力边界与复用验收

这版仍是 tuning。机械 Gate 可以证明已定义错误被阻断，不能证明隐喻准确、故事好或达到消息队列观感。也不保证每个重要名词都能靠字符串规则自动识别。真正的完成标准仍是无需大量人工微调的新材料成片，通过真实观察且没有重复旧问题。

已有规则不再只是提醒：新路径自动加载全部核心/Profile 经验并要求对应真实动作复核；未知的新经验 id 未接入时阻断。程序可判定的问题留故障输入到 `check_score.py`；只能看听判定的规则必须保留待复核状态，不能伪装成自动化保证。先验证一条新机制，再做多题材盲测，不拿累计条款数量当稳定性指标。
