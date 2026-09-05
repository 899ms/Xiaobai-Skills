# Xiaobai Video Director

开发者：**Xiaobai** · 版本：2.0.0（调优版）

一个独立运行的 AI 导演 Skill：理解材料、确定讲解主线、设计对象与动作，生成配音和字幕，通过共享 Remotion 执行器渲染并检查视频。当前内置 `knowledge-explainer`，主要用于知识讲解。

它包含导演规则、执行脚本、依赖锁文件和回归测试；不包含大模型、云服务额度或开发者的私人配置。无需工坊、数据库、消息队列或其他私人项目。

## 运行条件

| 条件 | 说明 |
|---|---|
| AI Agent | 能读取 Skill、读写文件、运行命令、查看图片/视频；模型账号由使用者提供。无法实际观察音画时，产物保持待复核，不能自动宣称验收通过 |
| Python / uv | Python 3.11+，使用 uv 安装锁定依赖 |
| Node / pnpm | 建议 Node.js 24 LTS；pnpm 10.33.2；Remotion 已锁定为 4.0.518 |
| 媒体环境 | `ffmpeg`、`ffprobe` 位于 PATH；Remotion 需要可用浏览器，首次渲染可能下载浏览器 |
| 配音和字体 | 自己的腾讯云 TTS 凭证、服务权限及额度；系统安装中文字体 |

目前在 macOS 验证。Linux / Windows WSL 需自行安装浏览器所需系统库及中文字体；尚未完成这些系统的完整验证，不承诺直接复制即可跨平台运行。当前字体依次回退到 PingFang SC、Noto Sans CJK SC、Noto Sans SC、Microsoft YaHei。Linux 可安装 Noto CJK；不随包分发系统字体。

新配音需要访问腾讯云；下载依赖、浏览器通常也需要联网。音频、原生时间戳、字体和所有素材齐备后，渲染与本地测试可离线执行。AI 是否需要联网取决于使用者的 Agent。

## 安装与配置

```sh
git clone https://github.com/0x-IHRR/Xiaobai-Skills.git
cd Xiaobai-Skills/Xiaobai-video-director
uv sync --locked
pnpm install --frozen-lockfile --ignore-scripts
```

如果已经下载此文件夹，直接进入文件夹执行后两条命令即可。后文命令都在本文件夹执行。

将 `.env.example` 复制为本机 `.env`，已有配置不要覆盖。在编辑器中填写：

| 配置项 | 内容 |
|---|---|
| `TTS_SECRET_ID` / `TTS_SECRET_KEY` | 使用者自己的腾讯云凭证，不在聊天中粘贴 |
| `TTS_PROTOCOL` / `TTS_BASE_URL` | 保留 `tencent_speech` / `https://tts.tencentcloudapi.com` |
| `TTS_MODEL` / `TTS_VOICE_ID` | 当前验证模型为 `1`，示例音色为 `1001`；使用其他音色前先试听 |
| `TTS_SPEED` / `PLAYBACK_RATE` | 合成速度 / 成片播放速度，示例为 `1.1` / `0.92` |
| `TTS_LANGUAGE` | 当前为 `zh-CN`，支持中文及中英混读 |

```sh
chmod 600 .env
uv run --locked python scripts/director.py preflight
```

预检只检查本机环境、配置及声音确认状态，不调用付费配音。`ready: true` 不代表凭证已通过服务端鉴权；首次试听才会实际验证。新用户的 `voice_approved: false` 是正常状态。

`.env` 只供 Python 配音脚本读取，Remotion 使用公开空配置 `.env.render`。不要把密钥写进 `.env.render`，也不要提交 `.env`、`.local/`、`runs/` 或生成缓存。更换 TTS 服务不能只改 URL：目前只实现腾讯适配器，替换服务需同时适配并验证原生时间戳。

## 交给 Agent 制作

让 Agent 读取本目录的 [SKILL.md](SKILL.md)，提供材料、目标受众和期望时长即可。也可以把整个文件夹安装到客户端的 Skills 目录；安装目录和调用名使用小写 `xiaobai-video-director`，已有同名安装先备份。仓库里的品牌文件夹保持 `Xiaobai-video-director`。

示例请求：

> 使用 $xiaobai-video-director，把我提供的材料做成约一分钟的中文讲解视频。先选一条主线，画面随口播逐步出现，用对象动作说明机制，并合成字幕和必要的动作音效。

Agent 按 Skill 完成必要的音色试听与确认，然后执行：材料与分镜 → 原生配音/对齐 → `score.json` → `score-check` → `score-render` → `score-review` → `score-release`。详见 [执行说明](references/execution.md) 和 [可执行导演谱](references/executable-score.md)。

配音只负责人声；需要的动作音效使用本条视频 `runs/<版本>/sfx/` 内的本地文件，由 Agent 准备或由使用者提供，不会自动从陌生网站下载素材。每条视频的输入和产物都放在独立 run 中，修改时保留旧版本。没有完成音画复核的 MP4 是候选片；正式交付必须通过 release Gate。

## 不花配音额度的自检

```sh
uv run --locked python scripts/check_score.py
uv run --locked python scripts/check.py
pnpm exec tsc --noEmit
```

这些检查无需 `.env` 或真实密钥，使用自拟 JSON 和一句通用合成语音夹具，不联网请求 TTS，也不代表使用者已批准某个音色。包含对象/时间/状态模型、共享 JSX 数字格式和装饰穿字、语音对齐、声音确认及失败阻断回归。

当前仍是调优版，图形与动作能力有边界。规则通过不能保证任意题材一次达到理想效果；最多两轮有依据的自动修复，仍不合格应报告具体问题。真人配音、完整离线 TTS、其他语言及其他视频 Profile 尚未提供现成实现。

## 许可证

本目录原创代码和文档使用 [MIT License](LICENSE)，开发者为 Xiaobai。第三方依赖、系统字体及云服务遵循各自条款；不会随本项目重新授权。Remotion 使用其自己的 [许可证](https://www.remotion.dev/license)，部分商业使用需要单独授权。通用测试音频由腾讯 TTS 合成，仅作回归夹具；实际配音服务及输出使用遵循供应商条款。
