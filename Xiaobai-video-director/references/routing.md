# Profile、问题与镜头 Router

Router 不选择固定成片模板。AI 先从材料建立全片 `narrativeSpine`，再标注每个语义段的解释结构和连续性事实；`scripts/router.py` 只执行已经确认的策略，输出是否询问用户以及每段镜头方式。

## 分层

1. `video-director` 保存跨视频类型仍成立的输入、声音、时间轴、镜头连续性、渲染、QA 和反馈机制。
2. Profile 保存某类视频的目标、解释结构、专用 Gate 和毕业标准。当前只实现 `knowledge-explainer`。
3. 一条视频先选择 Profile，再确定全片叙事主线；每个语义段独立选择解释结构和镜头方式。

## 条件问题

潜在问题只允许使用 `focus`、`story-angle`、`voice`、`visual-language`、`capability-approval`。新 run 使用 `route-request.version=2`，并写入 `preflight` 返回的 `voice_profile_hash`。每条视频必须且只能有一个 `voice` 决策点。同时满足以下条件才询问：

- 不同选项会明显改变成片；
- 当前仍未解决；
- 没有已确认偏好可复用。

其余选择直接使用既有偏好或 AI 推荐。解释结构、语义分段、镜头、对象、布局、时间、缓动、渲染与 QA 不向用户提问。

声音有更严格的边界：`.local/voice-approval.json` 只证明某套配置已经试听，不代表以后默认使用；只有 `.local/voice-default.json` 与本次 `voiceProfileHash` 匹配时，`voice.preferenceKnown` 才能为 `true`。没有长期默认时，Router 必须返回音色问题；呈现问题前先为 2–3 个候选项生成 10–15 秒试听。用户只为当前视频选定时用 `unresolved=false, preferenceKnown=false`，Router 记录为 `resolvedBy=user`；明确保存为默认后，后续才可记为 `resolvedBy=preference`。

## 镜头策略

- 第一段建立世界。
- 世界或时间改变时切场。
- 同一世界发生主体交接时做可见交接。
- 同一世界、同一主体但焦点改变时移动镜头。
- 同一世界且焦点不变时使用局部状态变化。

镜头移动是连续关系的结果，不是全片固定风格。每个路由结论必须记录可检查的内容证据。

## 命令

```sh
uv run --locked python scripts/router.py route --request RUN/route-request.json --out RUN/route-plan.json

uv run --locked python scripts/router.py benchmark-gate --manifest BENCHMARK/manifest.json
```

Benchmark manifest 的每个 case 记录唯一材料哈希、`tuning/holdout`、领域、覆盖结构、人工 React 修改次数、自动修复轮数、硬性 Gate、用户验收和新失败类型。布尔结果只能来自对应 run 的真实产物与验收记录；Router 不替代证据采集。`benchmark-gate` 通过也不会自动修改 Profile 成熟度，避免测试数据误把 Profile 发布到工坊。
