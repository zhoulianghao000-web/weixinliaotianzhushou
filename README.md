# IM 侧边栏助手（QQ 版）

可贴在 **QQ 或微信 4.x** 窗口右侧的浮窗：读取当前聊天内容 → 大模型生成建议回复 → 人工确认后填入 / 发送。

**形态**：独立进程 + 独立窗口跟随 IM 窗口（不注入、不修改 QQ 进程）。

```
┌─────────────────┬──────────────┐
│                 │ 侧边栏 UI    │ ← PySide6 无焦点置顶窗（不抢 IM 焦点）
│  QQ / 微信窗口   │ 消息 / AI 建议│
│                 │ 一键填入/发送 │
└─────────────────┴──────────────┘
        ↑ win32 贴边跟随         ↓ HTTP / WebSocket (127.0.0.1:8765)
┌────────────────────────────────────────┐
│  Python 后端（打包后内嵌在同一 exe）      │
│  FastAPI + SQLite + Adapter 抽象层      │
│  adapters/qq_adapter（QQ NT）            │
│  adapters/wechat4_adapter（微信 4.x OCR）│
│  adapters/wxauto_adapter（微信 3.9 兼容）│
│  adapters/mock_adapter（演示数据）       │
└────────────────────────────────────────┘
```

## 技术栈

Python 3.10+（本机 3.14.7）｜ PySide6 6.12.0（UI）｜ FastAPI 0.151 + uvicorn（后端）｜ SQLite（标准库，零运维）｜ DeepSeek / OpenAI 兼容 API

> 完整规范见 [`CLAUDE.md`](./CLAUDE.md)（本项目唯一规范，所有开发会话必须遵循）。

## 直接用打包好的 exe（推荐）

```powershell
# 1) 把 .env 放到 exe 旁边（配 DeepSeek Key；不配也能用，走规则兜底）
Copy-Item .env dist\.env

# 2) 双击 dist\SidebarIM.exe
```

后端已内嵌在 exe 里，双击即用，**不需要另开终端跑服务**。日志在 `dist\logs\app.log`，数据在 `dist\data\sidebar.db`。
未签名 → Windows SmartScreen 会提示"未知发布者"，选"更多信息 → 仍要运行"。

## 从源码运行

```powershell
# 1) 后端依赖
cd backend
python -m pip install -r requirements.txt

# 2) UI 依赖
python -m pip install PySide6==6.12.0

# 3) 配置（可选，留空走规则兜底）
Copy-Item .env.example .env    # 填入 OPENAI_API_KEY

# 4) 启动后端（终端 A）
python server.py               # http://127.0.0.1:8765

# 5) 启动侧边栏 UI（终端 B）
python desktop/app.py
```

使用产品库解析 PDF、图片和 Office 文件时，在 `backend` 目录安装可选依赖：

```powershell
python -m pip install -r requirements-products.txt
```

MinerU 在本机解析文件；扫描 PDF 和图片首次 OCR 时需要下载本地模型。未安装 MinerU 时，TXT、Markdown、CSV、DOCX、PPTX、XLSX 及可提取文字的 PDF 仍可索引；扫描 PDF 和图片只保留原件并标记待处理。导入的原件复制到应用数据目录，外部源文件不修改；只有生成回复时，检索出的文字片段会随请求发送给已配置的大模型。

## 重新打包

```powershell
python -m pip install PyInstaller
python -m PyInstaller packaging/build.spec --noconfirm --clean
# 产物 dist/SidebarIM.exe（约 55MB，单文件，不签名）
```

## 功能说明

| 功能 | 说明 |
|---|---|
| 连接状态页 | 后端未启动 / QQ 未运行 / 演示模式 三态清晰提示，不白屏 |
| 消息展示 | 按 `friend / self / sys / time / recall` 五类气泡渲染 |
| 生成回复 | 调 DeepSeek（OpenAI 兼容），可加"补充要求"；失败自动降级规则模板 |
| 一键填入 | 粘贴到 QQ 输入框，**不回车**（发送前可手改） |
| 发送 | 粘贴并回车；**3 秒幂等**，连点不会重复发送 |
| 贴边跟随 | win32 无焦点贴附 QQ 右侧，拖动/最大化自动跟随（已真机验证） |
| 实时监听 | 默认关闭；开启后新消息自动出现在侧边栏 |
| 设置页 ⚙ | 改 API Key / 模型 / 窗口宽度 / 适配器，**保存即时生效**（适配器热切换 + 自动关监听） |
| 话术库 💬 | 常用话术按分类筛选，点一条插入回复框；支持新增 / 删除（不做编辑） |
| 产品库 ▦ | 建立产品条目并导入本机资料文件夹；原件留在 RAW 目录，提取的文字进入 SQLite 检索；生成回复前可选一个产品库并查看命中来源 |
| 关闭 ✕ | 右上角一键退出整个应用（含内嵌后端） |

## 微信 4.x 模式

顶部平台选择器可切换“自动 / QQ / 微信”。微信 4.x 使用本地 RapidOCR 识别按窗口比例裁出的聊天区域，只保留左侧浅色好友气泡并排除右侧绿色气泡、时间和输入工具区；OCR 结果用于生成回复。截图在本机处理，不会单独上传；回复生成后仍需你手动确认填入或发送。请保持微信窗口可见，窗口移动或缩放后截图范围会按窗口尺寸重新计算。

微信当前聊天名和完整会话列表不能从 4.x 的渲染面板读取，侧边栏会显示“微信当前窗口”。OCR 对低清、遮挡或特殊气泡主题可能识别不准，建议发送前核对草稿。

## QQ 模式

QQ NT 的聊天文本由 Chromium/D3D 渲染，无法从控件树直接读取。侧边栏通过本地 RapidOCR 识别窗口中可见的聊天区域，只保留左侧白色来信气泡，右侧自发消息、时间和输入区不参与识别；截图只在本机处理。QQ 不提供可读会话列表，因此不显示虚构的“QQ 当前窗口”会话行，内部仍以当前 QQ 窗口作为回复目标。请保持 QQ 窗口可见，并在发送前核对 OCR 草稿。

## 环境变量（`.env`，全部可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | 空 | DeepSeek Key；留空走本地规则模板兜底 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `OPENAI_MODEL` | `deepseek-chat` | 模型名 |
| `SIDEBAR_PORT` | `8765` | 后端端口 |
| `SIDEBAR_TITLE` | `WeChat Sidebar` | 侧边栏窗口标题（贴边靠它找窗口，改名需同步） |
| `SIDEBAR_ADAPTER` | `auto` | `auto` / `qq` / `wechat4` / `wxauto` / `mock` |
| `SIDEBAR_TARGET_CLASS` | 空 | 贴边目标窗口类名（微信 3.9 填 `WeChatMainWndForPC`） |
| `SIDEBAR_TARGET_TITLE` | `QQ` | 贴边目标窗口标题（类名找不到时按标题匹配） |
| `SIDEBAR_WIDTH` | `380` | 侧边栏宽度 |

## 验证

```powershell
python ../_verify_e2e.py flow      # 主流程（真实 HTTP）
python ../_verify_e2e.py boundary  # 边界（含重复发送幂等）
python ../_verify_qq.py read       # QQ 链路：状态/会话/剪贴板读消息
python ../_verify_qq.py fill      # QQ 真机粘贴（不发送）
python ../_verify_dock.py          # 贴边跟随真机验证（真实窗口几何）
python ../_verify_input.py         # 输入可编辑性（可获焦点/补充要求/回复可改）
python ../_verify_v23.py           # V2/V3 接口（设置热生效 + 话术增删）
python ../_verify_ui_v23.py        # V2/V3 UI 烟测（页面跳转 + 宽度即时生效）
```

最新结果见 [`docs/验证报告.md`](./docs/验证报告.md)；截图在 `docs/screenshots/`。

## 合规提醒

个人 IM 自动化违反《QQ 软件许可协议》/《微信个人账号使用规范》，存在限制功能或封号风险。
本项目仅限**本机自用与技术学习**，请勿用于群发、营销或任何商业用途。
生产场景请走企业微信 / QQ 官方机器人 API。
