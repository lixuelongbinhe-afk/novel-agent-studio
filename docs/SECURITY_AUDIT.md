# Security Audit

审计日期：2026-07-28
版本：以仓库根目录 `VERSION` 为唯一来源
适用边界：受信任 Windows 账户下的本机单用户应用。

## 威胁模型

防护重点是恶意或错误的模型响应、自定义 Adapter 配置、跨 Origin 写请求、SSRF、重定向、凭据泄漏、ZIP/备份篡改、超大响应、恢复失败和 HTML 注入。拥有当前 Windows 账户、数据库文件或进程环境读取能力的攻击者位于本地信任边界内。

## 已验证控制

- 桌面后端只绑定 `127.0.0.1`，并限制 Host；非同 Origin 的写请求被拒绝。
- 每次桌面启动生成新的 256-bit 随机会话令牌；API 要求 Bearer Token，前端从启动 URL 片段接收后仅保存在 `sessionStorage`，令牌不写入日志或长期配置。
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
- 备份限制压缩与解压大小、条目数和压缩比，拒绝路径穿越、额外条目、Schema/哈希篡改、错误 MIME 和 Secret 命中；用户可选择 AES-256-GCM 密码加密备份。
- 恢复先预览并要求 SHA-256，一次事务替换数据并重建 FTS；模拟磁盘错误验证原数据库完整回滚。
- Python 运行、开发和发布依赖分别使用带哈希锁文件，CI 与 Release 强制 `--require-hashes`；前端使用冻结 lockfile，GitHub Actions 固定完整 commit SHA。
- CI 执行 Python/前端依赖漏洞扫描并生成 CycloneDX SBOM；发布产物带 SHA-256 校验文件。
- Windows 打包脚本支持对桌面程序、卸载器和安装器签名并验证；正式发布可通过 `NAS_REQUIRE_CODE_SIGNING=1` 禁止未签名构建。

## 验证结果

测试数量、通过率、构建状态、安全扫描和性能基准不再手工复制，统一由 CI 生成到 `docs/generated/`。发布导出测试确认诊断包不含正文或凭据，Adapter/Workflow 导出不含密钥值；当前依赖扫描未发现已知漏洞。

## 残余风险

- 随机启动令牌只保护本地 API 会话，不是用户身份系统；没有 TLS、多用户权限或服务端租户隔离，禁止公开监听、端口转发和公网部署。
- SQLite 数据库与日志默认未加密；未设置备份密码时备份也不加密。Windows 账户和文件权限仍是主要本地边界。
- 环境变量密钥可被同一账户下具备进程检查权限的软件读取。
- 当前本地产物没有商业代码签名证书，Windows SmartScreen 可能提示未知发布者；正式发布流程已提供强制签名开关，但证书必须由发布者配置。
- 未进行独立第三方渗透测试，也不替代第三方 Provider 的合规、保留、训练和内容安全审查。
- 第三方 Provider 响应仍是不可信输入；本地验证降低风险，但无法证明上游服务行为。

发布结论只适用于 README 描述的本机单用户模式。
