# 独立执行说明 v2.0

## 环境与配置

所有项目依赖位于本 Skill 内，不需要工坊目录、数据库、队列或服务。Python 的 `scripts/speech.py` 和 `scripts/tencent.py` 从已验证的语音执行路径提取，保留腾讯 TC3 签名、分段、原生时间戳、音频合并和语速映射。没有携带 FastAPI、SQLAlchemy、应用 Settings 或其他供应商实现。

本机需要 uv、Python 3.11+、Node、pnpm 10.33.2、ffmpeg/ffprobe；依赖版本由两个锁文件固定。首次安装，或锁文件更新后，在 Skill 目录运行：

```sh
uv sync --locked
pnpm install --frozen-lockfile --ignore-scripts
```

不升级依赖来解决本次内容问题。Remotion 使用已有可用浏览器或首次下载所需浏览器；如果要离线渲染，事先准备好浏览器、字体和全部资源。Node 和 ffmpeg 属于本机工具，不依赖工坊。

以下命令均在 `Xiaobai-video-director` 文件夹中执行。输入输出可使用相对此文件夹的路径，也可使用绝对路径：

```sh
uv run --locked python scripts/director.py preflight
```

`preflight` 只检查依赖、配置和声音确认状态，不发送 TTS 请求、不读取数据库。复制 `.env.example` 到本机 `.env` 后填入腾讯凭证，权限设为 600；不要整体展示、source 或分享。每位使用者自行配置凭证，本发布包不包含开发者的账户或声音确认记录。

| 配置 | 用途 |
|---|---|
| `TTS_SECRET_ID` / `TTS_SECRET_KEY` | 本机腾讯云凭证，只由配音脚本读取；用户不在聊天中粘贴 |
| `TTS_PROTOCOL` / `TTS_BASE_URL` / `TTS_MODEL` | 腾讯官方 HTTPS 接口、`tencent_speech`、模型 `1` |
| `TTS_VOICE_ID` | 当前默认 `1001`；改变前先试听 |
| `TTS_SPEED` / `PLAYBACK_RATE` | 合成倍速 / 成片播放速度，当前 1.1 / 0.92 |
| `TTS_LANGUAGE` | `zh-CN`，支持中文及中英混读 |

不再使用 `DIRECTOR_WORKSPACE`，不支持 `TTS_CREDENTIAL_SOURCE=workshop`。更换账户或服务仍遵循用户授权。腾讯 ModelType、音色 ID 和 Speed 是不同参数，倍速转换由提取后的适配器处理。更换音色时查 [官方音色列表](https://cloud.tencent.com/document/product/1073/92668) 和 [TextToVoice 文档](https://cloud.tencent.com/document/product/1073/37995)，实测原生时间戳支持。

## 声音试听与复用

第一次使用声音，或音色、模型、合成速度、播放速度变化时，先生成 10–15 秒代表片段。没有长期默认音色时，导演从官方可用列表预选 2–3 个差异明确且适合材料的候选配置，分别生成试听后再呈现一次选择。默认试听文本含中文、AI 和数字；专名可用 `--text` 指定，最多 140 字。

```sh
uv run --locked python scripts/director.py sample --out runs/voice-01
```

输出原始 MP3、原生对齐与应用播放速度的 `audition.wav`。听过并由用户认可后，才运行 `approve-voice --evidence 试听文件绝对路径 --note 实际用户确认依据`；它只批准当前声音配置用于合成，记录存于 `.local/voice-approval.json`。用户同时明确要求以后默认使用时，才增加 `--remember-default --label '用户看到的音色名称'`，另写 `.local/voice-default.json`。两个文件都只含公开配置、证据哈希和确认依据，不随 Skill 分享。

当前配置已获试听批准时可以直接合成当前视频；只有匹配的长期默认记录才能让下一条视频跳过音色选择。测试夹具和“上一条听着没问题”不能替新用户建立长期默认。

## Profile、路由与输入

每条视频先在 `brief.json` 记录 `profile` 和全片唯一的 `narrativeSpine`。当前只实现 `knowledge-explainer`；旧的 v4 状态表未写 `profile` 时只为兼容而按它处理，新 run 必须显式填写。新 `route-request.json` 使用版本 2，必须复制 `preflight.voice_profile_hash` 到 `voiceProfileHash` 并包含一个 `voice` 决策点。

复制 `assets/route-request.example.json` 到 run，按材料填写条件问题与语义段事实，再生成可复核的路由结果：

```sh
uv run --locked python scripts/router.py route --request RUN/route-request.json --out RUN/route-plan.json
```

`questions` 为空时直接继续；不为空时只呈现 Router 返回的 2–3 个选项与推荐。`segments[].shotMode` 是内容连续性的结论，不是固定模板：同一世界优先镜头移动、局部变化或交接，世界或时间真正变化时才切场。

## 时间轴

每条视频建一个独立目录 `Skill根目录/runs/版本号/`。保存 `brief.json` 和 `storyboard.json`，来源由用户文件、材料片段或授权 URL 提供，不从工坊自动取数。正文和来源事实校对完成后运行：

```sh
uv run --locked python scripts/director.py prepare --story runs/run-01/storyboard.json --out runs/run-01
```

先参考 `assets/storyboard.example.json` 理解字段，它是自拟演示，不是替换标题的成片模板：

- `id`、`title`、`focus.question/viewer_takeaway/visual_subject`。
- `sources` 的每项有稳定 `id` 和 `path/url/excerpt`，可加页码等位置；`beats[].source_refs` 引用来源。
- `beats` 按口播顺序保存稳定 `id`、显示文本 `text`、具体动作 `visual`；示例可标 `is_example`。
- `anchors` 可把稳定动作名称映射到全文唯一出现的口播词组，不能与 beat 重名。

输出 `narration.txt`、`narration.mp3`、`alignment.json`、`props.json`、`storyboard.json`。原生时间戳驱动字幕、beat cue 与动作 anchor。朗读输入仅清理中英交界空格，字幕保留原文；不支持 SSML 或朗读/字幕异文的自动映射。

字幕长句在写稿时按语义分段，不切开供应商的同一个词。时间统一为 `画面毫秒 = 原生音频毫秒 / PLAYBACK_RATE + 600`；30 fps、18 帧前导、1.6 秒尾部。不要在场景里二次除播放速度，也不要把全局 cue 当成局部 Sequence 帧。

缓存同时核对完整朗读输入、公开声音配置与音频 SHA-256。相同输入复用缓存，不调用 TTS；不同输入或损坏缓存拒绝覆盖。换目录保留新旧版本；音频输入相同时可以复制 `narration.mp3` 和 `alignment.json`，脚本会再次校验。

## 编译、候选片与交付

`prepare` 后按 [可执行导演谱](executable-score.md) 建立 `score.json`，并使用其中四个 `score-*` 命令。唯一生产入口为共享 `ScoreEntry.tsx`；旧的自写主题 React、声明式 `release` 已停止用于新交付，旧 Gate 保留作历史诊断。

复用相同配音时不再请求 TTS。模型、音效或渲染器变更后，必须以新候选版本重渲染，重新提取真实证据。未完成实际连续播放复核时可展示“验证样片”，但状态保持 `awaiting-perceptual-review`，不得标为 final 或自动通过。

本地最小回归：

```sh
uv run --locked python scripts/check_score.py
uv run --locked python scripts/check.py
```

前者验证可执行模型和失败阻断，后者保留语音、路由及历史规则兼容检查。通过不能替代成片观察。工坊接入与跨材料毕业仍需另行验证。
