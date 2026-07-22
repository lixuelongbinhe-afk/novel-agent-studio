# Novel Agent Studio

Novel Agent Studio（小说智能体工作室）v2.2.3 是面向长篇小说创作的本地优先多 Agent 工作台。它同时支持“从创意或大纲创作新小说”和“导入半成品小说继续写作”，包含真实的 React 前端、FastAPI 后端、SQLite 数据库和 Windows 桌面程序，不是静态原型。

## Windows 直接使用

安装版：

1. 双击 `outputs/NovelAgentStudio-Setup-2.2.3.exe`。
2. 安装完成后，从开始菜单打开“小说智能体工作室”。
3. 实际程序位于 `%LOCALAPPDATA%\Programs\NovelAgentStudio\NovelAgentStudio.exe`。

免安装版：

1. 完整解压 `outputs/NovelAgentStudio-Portable-2.2.3.zip`，不要只拖出单个 EXE。
2. 双击解压目录中的 `NovelAgentStudio\NovelAgentStudio.exe`。

从 v2.1.2 升级后，如果旧项目把“章节规划师”显示成正文且缺少后续章节，请打开项目并点击顶部的“修复章节结构”。程序会先创建永久特殊快照，再将错误占位章移入回收状态，并按项目设定补齐缺失章节。

两个版本都自带 Python 运行时，不要求另外安装 Python，也不会打开外部浏览器。程序在随机本地端口启动后端并打开独立桌面窗口。关闭窗口时可选择“转入托盘继续”或“停止并退出”，并可记住选择；异常退出遗留的生成任务会在下次启动时明确标记为已中断。Windows 需要可用的 Microsoft Edge 或 WebView2 Runtime。

## 主要功能

- 三种创作入口：从题材创意开始、导入 TXT/Markdown/Word 大纲，或导入半成品小说续写。
- 半成品支持 TXT、Markdown、Word、PDF、粘贴正文和已有项目；完整原文作为永久只读副本和永久快照保留，正文工作副本可编辑与恢复。
- 续写流程按导入解析、资料审核、大纲补建、续写规划、正文续写、全文审阅和完成推进；AI 自动提取章节、世界观、人物关系、时间线、伏笔、文风与未完剧情线，每项均需作者审核。
- 可手动设定或让 AI 建议总字数、总章节和总卷数；后续方向支持作者大纲与 AI 提案切换，进入正文时由作者选择接续当前章或从下一章开始。
- 原文冲突会在当前任务结束后暂停；AI 修改已有正文只提交差异提案，批准后才写入，接续当前章时只追加而不覆盖已有内容。
- 定位主题、世界规则、文风边界、人物关系、剧情时间线、伏笔、分卷、章节和场景均为独立审核项；任何未批准规划都会阻止正文生成。
- 章级或场景级审核；直接编辑、局部修改、全文重写、多个方案、审核批注、双栏版本比较、恢复旧版。
- 手动、立即自动或可暂停倒计时续写；默认批准当前章后等待 10 秒生成下一章。
- 上传用户自有 TXT/Markdown/Word 参考文本，提取抽象文风规则后再由作者审核。
- 轻微设定冲突自动校正并标记；重大冲突必须选择保留正文、保留设定或手工合并。
- AI 对话自动读取项目/阶段/章节/选区上下文，所有修改提案需作者确认才写入。
- 项目级质量/成本/速度/均衡路由、模型选择原因、Token 与费用；70% 提醒，110% 在当前任务完成后暂停并等待确认。
- 自动保存、手动保存、三个普通快照、AI 修改前快照，以及永久保留的重要剧情转折快照。
- TXT、Markdown、PDF 导出；项目首页显示书名、阶段、完成字数、待审核数量和最后编辑时间。
- 项目、卷、章节、场景、资料库、时间线、伏笔、风格指南、自动保存与版本恢复。
- OpenAI Responses、OpenAI Chat Completions、Anthropic Messages、Gemini Native、Ollama Native 和安全自定义 HTTP 协议。
- Provider、模型能力、价格、Route、限流、预算、熔断、费用和调用账本。
- 可版本化 Agent、React Flow DAG、并行节点、Condition、Merge、重试、取消、SSE 断线续传和历史派生运行。
- SQLite FTS、实体/别名/关系/状态、时间线、伏笔、Pin、数据分类和 Provider 数据边界组成的可解释上下文。
- 不可变审批快照、正文 Diff、结构化状态提取、逐项变更、冲突处理和单事务安全写回。
- 完整备份、恢复前预览和哈希确认，以及正文、资料、时间线、伏笔、Agent、Workflow、Adapter 和诊断导出。

## 无密钥演示

在“模型中心”创建 `Mock` Provider 和 Mock 模型即可测试普通响应、流式输出、结构化 JSON、usage、延迟、超时、限流与错误，不会访问付费 API。开发环境也可执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli migrate
.\.venv\Scripts\python.exe -m app.cli seed
```

Seed 会创建中文悬疑示例和 11-Agent 并行工作流。

## 常用 Provider

在“模型与 API”点击“添加服务”，选择预设后可编辑模型名和 Base URL。可将 API Key 保存到 Windows 凭据管理器，或只保存环境变量名；密钥明文不进入 SQLite。

| Provider | 协议 | 默认 Base URL | 默认凭据变量 |
| --- | --- | --- | --- |
| DeepSeek | OpenAI Chat | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| xAI / Grok | OpenAI Chat | `https://api.x.ai/v1` | `XAI_API_KEY` |
| OpenAI / Anthropic / Gemini / OpenRouter | 对应官方或兼容协议 | 界面预填 | 按服务设置 |
| OpenAI 兼容服务 | OpenAI Chat | 用户填写 | 用户填写 |

设置或修改环境变量后需完全退出并重新打开桌面程序。模型 ID、价格、上下文窗口和服务条款可能变化，应以用户账户和供应商当前文档为准。

## 数据与备份

安装版数据：`%LOCALAPPDATA%\NovelAgentStudioV2\data`

免安装版数据：解压目录中的 `NovelAgentStudio\data`

目录内包含 `studio-v2.db`、轮转日志和专用 WebView 配置。卸载器默认询问是否保留数据；`--silent` 卸载会保留数据。完整备份含未发布正文且未加密，请像保护原稿一样保护 `.nasbackup.zip` 文件。

## 本地开发：首次初始化

源码运行需要 Python 3.12、Node.js 20+ 和 pnpm 11。Windows 首次 clone 后，在仓库根目录执行：

```powershell
py -3.12 -m venv backend\.venv
cd backend
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m app.cli migrate
cd ..\frontend
corepack enable
pnpm install --frozen-lockfile
cd ..
```

macOS/Linux 使用 `python3.12 -m venv backend/.venv` 和 `backend/.venv/bin/python` 完成同样步骤。仓库以 `pnpm-lock.yaml` 为唯一前端锁文件；开发、测试和构建统一使用 pnpm。

## 本地开发：启动

Windows 首选：

```powershell
.\scripts\dev.ps1
```

打开 `http://127.0.0.1:5173`，后端默认是 `http://127.0.0.1:8000`。生产预览：

```powershell
cd frontend
pnpm run build
cd ..
.\scripts\start.ps1
```

构建 Windows 交付：

```powershell
.\scripts\package-desktop.ps1
```

脚本会构建前端和 PyInstaller 程序，执行控制台自检及真实 GUI 生命周期自检，再生成安装包、便携 ZIP 和 SHA-256 文件。

## 质量检查

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests ..\desktop ..\scripts
.\.venv\Scripts\python.exe -m mypy --strict app tests

cd ..\frontend
pnpm test --run
pnpm run typecheck
pnpm run build
pnpm run e2e
```

最终发布记录见 `docs/FINAL_AUDIT.md`、`docs/SECURITY_AUDIT.md`、`docs/PERFORMANCE_AUDIT.md`、`docs/RELEASE_CHECKLIST.md` 和 `docs/KNOWN_LIMITATIONS.md`。

## 安全边界

本软件面向本机单用户，不提供身份认证、TLS 或公网多租户能力，不应把端口转发到局域网或互联网。数据库和备份默认未加密，安装包也尚未进行商业代码签名。完整边界和残余风险见 `docs/SECURITY_AUDIT.md`。
