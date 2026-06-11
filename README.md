# Piano Sheet Fetcher

一个 Codex skill / 脚本工具包：根据歌曲名在公开网络来源中查找其他用户上传的、非付费的完整钢琴谱资源，并在存在公开直链时下载到本地。

当前目标不是“爬正版付费资源”，也不是绕过登录、网盘客户端、验证码或平台限制；它做的是：

- 能公开直接下载的完整谱文件：自动下载并归档；
- 能确认“登录后可下载”的页面：列出来，等待用户正常登录下载；
- 需要人工动作、付费、私信/进群、证据不足的候选：分状态记录，不冒充成功。

## 快速使用

默认入口：

```powershell
python .\scripts\find_piano_scores.py "<song-title>" --out ".\sheet-music"
```

带歌手名、别名时召回会更好：

```powershell
python .\scripts\find_piano_scores.py "<song-title>" --artist "<artist>" --alias "<alias>" --out ".\sheet-music"
```

选择目标 profile：

```powershell
python .\scripts\find_piano_scores.py "<song-title>" --profile piano_score --out ".\sheet-music"
python .\scripts\find_piano_scores.py "<song-title>" --profile midi --out ".\sheet-music"
```

可用 profile 由 `profiles/*.yaml` 动态加载。新增 profile 时只新增 yaml；不要为 profile 成功/排除规则修改 `classify_candidates.py` 或 prompt 代码。

例子：

```powershell
python .\scripts\find_piano_scores.py "ただ君に晴れ" --artist "Yorushika" --out ".\sheet-music"
```

## 功能

- 多渠道搜索公开页面、视频简介、谱站页面和公开文件链接；
- 只对公开非付费的谱面直链尝试下载；
- 下载成功前会检查文件签名/扩展名，避免把网页或 `.bin` 冒充成谱；
- 输出 Markdown 报告和 JSON 中间产物；
- 区分已下载、可下载但需登录、需要人工动作、付费/商店排除、私信/进群门槛、MIDI-only、非完整谱、未知候选等情况。
- 用 `profiles/<profile>.yaml` 的 `success_formats` / `auxiliary_formats` 做格式级确定性判断；只有页面语义/证据冲突进入一次性 step7 裁决。

成功格式包括：

- PDF
- MSCZ
- MXL
- MusicXML
- 明确包含上述格式的谱包

MIDI-only 在“完整钢琴谱”目标下只算辅助线索，不算成功。

## 输出结构

```text
sheet-music/
  query-title/
    RESULT.md
    RESULT.json
    raw_candidates.json
    classified.json
    unknown_candidates.original.json
    STEP7_PROMPT.md
    unknown_candidates.json
    STEP7_DECISIONS.json
    ranked.json
    downloads/
      title-source.pdf
      title-source.pdf.source.json
```

其中：

- `RESULT.md`：给人看的结果摘要；
- `RESULT.json`：完整结构化结果；
- `raw_candidates.json`：原始候选；
- `classified.json`：确定性分类后的候选；
- `unknown_candidates.original.json`：确定性分类后仍需 step7 的原始小批量候选；
- `STEP7_PROMPT.md` / `STEP7_DECISIONS.json`：rubric 裁决提示和合并后的裁决留痕；
- `unknown_candidates.json`：保守兜底或裁决后仍未解决的候选；
- `ranked.json`：去重排序后的候选；
- `downloads/`：实际下载到本地的谱文件和来源 metadata。

## 候选状态

最终 `status` 统一来自 `scripts/status_enum.py`：

- `downloaded`：已下载并通过校验；
- `download_candidate`：公开非付费直链候选；
- `login_required_downloadable`：页面明确列出完整谱文件，但下载需要正常登录；
- `manual_action_required`：需要浏览器点击、网盘客户端、验证码、提取码核对、评论区检查等人工动作；
- `paid_or_store_excluded`：付费商店/赞助/会员售卖页，只记录不下载；
- `private_gate`：私信、进群、邀请制，不自动化；
- `not_full_piano_score`：简谱、瀑布流、Synthesia-only、display-only、非钢琴或无谱证据；
- `midi_only_auxiliary`：只有 MIDI，作为辅助线索；
- `page_candidate`：普通页面/谱站/视频/索引，还不能确认可下载；
- `UNKNOWN`：规则冲突或证据不足，留给一次性 AI/人工裁决；
- `failed`：探测或下载失败。

## 安全边界

脚本不会绕过：

- 登录墙；
- 付费/会员/赞助；
- CAPTCHA；
- DRM；
- 网盘客户端限制；
- 私信、进群、邀请制；
- 隐藏 API、cookie 复用、下载 token 猜测。

付费资源只记录为排除项。登录后可下载但页面已经明确列出文件的资源，会作为 `login_required_downloadable` 列给用户正常处理。

完整流程契约见：

- `SKILL.md`
- `references/source-policy.md`

## 主要脚本

- `scripts/find_piano_scores.py`：产品入口；
- `scripts/classify_candidates.py`：硬门控 + 读取 profile yaml 的格式级确定性分类；
- `scripts/dedupe_candidates.py`：去重、排序、报告辅助；
- `scripts/fetch_sheet.py`：公开直链下载器；
- `scripts/bilibili_full_score_hunt.py`：B站 source probe，只抽取证据，不做最终分类；
- `scripts/status_enum.py`：最终候选状态枚举。

## 当前定位

这个仓库目前是“rubric 化的曲谱查找 skill v0.2”：

- `piano_score` 已完成一轮受限真实回归（10 首、默认多源但降低单首探测上限）：可用 6/10，危险误判 0；仍建议在发布前跑默认高召回复验。
- `midi` 已完成格式级分类与 profile 查询词配置，但来源召回仍偏弱；5 首代表样本重跑仅 `ただ君に晴れ` 直接下载到 `.mid`，因此仍应视为 experimental recall。
- `numbered_notation` / `guitar_tab` / `full_score` / `any_score` 已有 rubric 草案，但仍应视为 experimental，点亮前需要各自跑真实样本回归。
- source plugin 架构已有雏形，新增来源应注册 source plugin，而不是继续往核心分类器里堆 `if/elif`。
