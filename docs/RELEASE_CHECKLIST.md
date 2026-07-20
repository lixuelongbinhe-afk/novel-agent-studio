# Release Checklist

版本：2.2.2
发布日期：2026-07-21
状态：GO，本机单用户 Windows 发布。

## 质量门禁

- [x] 后端 `180 passed`；唯一警告来自 Starlette TestClient 的上游弃用提示。
- [x] Ruff 全部通过。
- [x] strict mypy：72 个应用源文件无问题。
- [x] 前端 16 个测试文件、41 个测试全部通过；当前桌面路由 104 个按钮声明均有真实点击或提交处理器，并包含删除、模型、自定义 API、侧栏、正文审核、上下文压缩、右栏滚动和半成品续写回归。
- [x] TypeScript strict 检查通过。
- [x] Vite 生产构建通过，1,648 modules transformed。
- [x] Playwright 最终 E2E：5 passed，覆盖创建、独立审核、正文审核写入、半成品导入续写、长回复右栏滚动，以及项目删除后的刷新和接口持久化验证。
- [x] PyInstaller GUI/Console 构建通过。
- [x] 打包目录控制台自检通过。
- [x] 打包目录 GUI 生命周期自检 10 秒通过。

## 安装版与便携版

- [x] 安装器 `--silent` 覆盖安装成功并返回退出码 0。
- [x] 实际安装目录控制台自检通过。
- [x] 实际安装目录 GUI 生命周期自检 15 秒通过。
- [x] 最终便携 ZIP 实际解压后控制台自检通过。
- [x] 最终便携 ZIP 实际解压后 GUI 生命周期自检 15 秒通过。
- [x] 安装版产品版本为 2.2.2。
- [x] 安装数据位于 `%LOCALAPPDATA%\NovelAgentStudioV2\data`。
- [x] 便携数据位于解压目录的 `NovelAgentStudio\data`。
- [x] 安装版和便携版数据目录互不混用。
- [x] 关闭窗口支持托盘继续或停止退出，并记住选择。

## 产物

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `NovelAgentStudio-Setup-2.2.2.exe` | 60,411,904 | `4d19afd7e89ab981e00807bd3081c24e477cb3a2dca6d58cc3f1eed3b4eb3927` |
| `NovelAgentStudio-Portable-2.2.2.zip` | 60,407,422 | `364c3d9aaabbf046d847135f9efa36d8e79e73eb46175c514c064caea96f0e72` |

哈希同时写入 `outputs/SHA256SUMS.txt`，并已与产物重新计算结果比对一致。

## 发布边界

- [x] README 说明安装、便携启动、数据位置、Mock 演示和 Provider 配置。
- [x] 安全、性能、最终审计、逐项需求验收和已知限制文档已更新。
- [x] 自动测试不调用付费 API。
- [x] 第三方 Provider 只由用户显式配置，不会静默切换模型。
- [x] 本次安装器未进行代码签名，Windows 可能显示未知发布者提示；哈希可用于完整性核验。
- [x] 不声称支持公网服务、多租户或自动合规。
