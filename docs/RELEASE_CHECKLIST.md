# Release Checklist

版本：2.2.6
发布日期：2026-07-22
状态：候选版本；完成干净标签构建后方可标记 GO。

## 质量门禁

- [x] 后端全量 pytest 通过（204 项）。
- [x] Ruff `app tests` 全部通过。
- [x] strict mypy `app tests` 零错误。
- [x] 前端 Vitest 全量通过（43 项）。
- [x] TypeScript strict 检查通过。
- [x] Vite 从当前源码完整生产构建通过。
- [x] Playwright 桌面与移动视口关键流程通过（6 项）。
- [x] 空库与旧库 Alembic 升级通过，旧场景和正文无丢失。
- [ ] PyInstaller GUI/Console 从干净标签提交完整构建通过。
- [x] 本地候选包的打包目录控制台自检和 GUI 生命周期自检通过。

## 可复现发布约束

- [x] 根目录 `VERSION` 是发布版本基准，脚本会核对 Python、前端、桌面、安装器和 Windows 文件版本。
- [x] 正式打包默认拒绝脏工作区和未带精确 `v2.2.6` 标签的提交。
- [x] 正式打包禁止跳过前端重建；本地跳过构建时必须通过源码哈希戳校验。
- [x] 包内 `build-provenance.json` 记录 commit、标签、源码哈希、前端锁文件哈希和 dirty 状态。
- [x] GitHub Actions 对 push/PR 执行后端和前端质量门，对版本标签执行 Windows 打包。

## 安装版与便携版

- [x] 本地候选安装器编译通过，内嵌 payload SHA-256 校验通过，产品版本为 2.2.6.0。
- [x] 本地候选便携 ZIP 解压后控制台自检通过。
- [x] 本地候选便携 ZIP 解压后 GUI 生命周期自检通过。
- [x] 安装数据位于 `%LOCALAPPDATA%\NovelAgentStudioV2\data`。
- [x] 便携数据位于解压目录的 `NovelAgentStudio\data`。
- [x] 安装版和便携版数据目录互不混用。

## 产物

产物必须由 `scripts/package-desktop.ps1` 在干净的 `v2.2.6` 标签提交上生成：

- `NovelAgentStudio-Setup-2.2.6.exe`
- `NovelAgentStudio-Portable-2.2.6.zip`
- `SHA256SUMS.txt`

文件大小和 SHA-256 不在源码文档中手填；以同次构建生成的 `SHA256SUMS.txt` 和包内来源清单为准。

## 发布边界

- [x] 自动测试不调用付费 API。
- [x] 第三方 Provider 只由用户显式配置，不会静默切换模型。
- [x] 安装器尚未商业代码签名，Windows 可能显示未知发布者；用户应核对 SHA-256。
- [x] 不声称支持公网服务、多租户或自动合规。
