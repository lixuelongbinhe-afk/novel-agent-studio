# Security Audit

审计日期：2026-07-21
版本：2.2.1
适用边界：受信任 Windows 账户下的本机单用户应用。

## 威胁模型

防护重点是恶意或错误的模型响应、自定义 Adapter 配置、跨 Origin 写请求、SSRF、重定向、凭据泄漏、ZIP/备份篡改、超大响应、恢复失败和 HTML 注入。拥有当前 Windows 账户、数据库文件或进程环境读取能力的攻击者位于本地信任边界内。

## 已验证控制

- 桌面后端只绑定 `127.0.0.1`，并限制 Host；非同 Origin 的写请求被拒绝。
- 生产模式关闭 OpenAPI/Swagger/Redoc，API 响应使用 `no-store`。
- CSP、`X-Frame-Options: DENY`、`nosniff`、无 Referrer、同源资源策略和禁用相机/麦克风/定位权限。
- React 默认转义模型 HTML；正文富文本走 TipTap 数据模型，不把模型字符串当脚本执行。
- V2 模型设置将 API Key 保存到 Windows Credential Manager 或读取明确绑定的环境变量；数据库、Manifest、诊断包和日志不保存明文值。
- Header 和错误文本脱敏并限长；响应体、连接池、超时和重定向均有边界。
- Generic Adapter 禁止 `eval`、脚本、动态 import、不受控 Jinja、Cookie 导入和浏览器 Token 抓取。
- SSRF 仅允许 HTTP/HTTPS；默认阻止 localhost、loopback、私网、link-local、metadata、reserved 和 DNS rebinding；本地 Origin 需精确审批，相关配置变化会撤销审批。
- 每个 Adapter 只能读取显式绑定的 CredentialReference，Manifest 导入默认禁用且不含凭据。
- Workflow Prompt、Condition、Mapping 和 Transform 使用受限语法，不执行用户 Python、JavaScript 或 Shell。
- 写回只接受白名单模型和字段，审批快照不可变；写回前重新检查 revision，并在一个事务中创建保护版本、写数据、审计和 FTS。
- 备份限制压缩与解压大小、条目数和压缩比，拒绝路径穿越、额外条目、Schema/哈希篡改、错误 MIME 和 Secret 命中。
- 恢复先预览并要求 SHA-256，一次事务替换数据并重建 FTS；模拟磁盘错误验证原数据库完整回滚。
- 依赖版本记录于 `backend/requirements.lock` 和前端 lockfile；生产包不包含测试服务器入口。

## 验证结果

- 179 个后端测试通过，包括 SSRF、凭据隔离、Secret 扫描、审批、正文审核写回、章节修复快照、半成品续写、上下文压缩重试、事务回滚、备份篡改和安全 Header。
- 32 个前端测试与 4 个最终 Playwright E2E 通过。
- 发布导出测试确认诊断包不含正文或凭据，Adapter/Workflow 导出不含密钥值。
- 未发现当前支持边界内的已知 Critical 或 High 级阻断项。

## 残余风险

- 没有身份认证、TLS、多用户权限或服务端租户隔离，禁止公开监听、端口转发和公网部署。
- SQLite 数据库、日志和完整备份默认未加密；Windows 账户和文件权限是主要本地边界。
- 环境变量密钥可被同一账户下具备进程检查权限的软件读取。
- 安装包未做商业代码签名，Windows SmartScreen 可能提示未知发布者。
- 未进行独立第三方渗透测试，也不替代第三方 Provider 的合规、保留、训练和内容安全审查。
- 第三方 Provider 响应仍是不可信输入；本地验证降低风险，但无法证明上游服务行为。

发布结论只适用于 README 描述的本机单用户模式。
