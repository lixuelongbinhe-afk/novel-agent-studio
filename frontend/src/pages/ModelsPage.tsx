import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  Braces,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  PlugZap,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X
} from "lucide-react";
import { Link } from "react-router-dom";
import { StudioProvider, studioApi } from "../api/studio";
import { ProviderDialogForm } from "../components/ProviderDialogForm";
import { dialogBackdrop, dialogCard } from "../utils/motion";

export function ModelsPage() {
  const queryClient = useQueryClient();
  const { data: providers = [], isLoading } = useQuery({
    queryKey: ["studio-providers"],
    queryFn: studioApi.providers
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<StudioProvider | null>(null);
  const [replacementKey, setReplacementKey] = useState("");
  const [testingId, setTestingId] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, { ok: boolean; message: string }>>({});
  const [error, setError] = useState("");

  const connected = useMemo(() => providers.filter((provider) => provider.provider_type !== "mock"), [providers]);
  const providerGroups = useMemo(() => {
    const local = connected.filter((provider) => ["ollama", "ollama_native"].includes(provider.provider_type) || /localhost|127\.0\.0\.1/i.test(provider.base_url));
    const cloud = connected.filter((provider) => !local.includes(provider));
    return [
      { label: "本地服务", items: local },
      { label: "云端服务", items: cloud }
    ].filter((group) => group.items.length > 0);
  }, [connected]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["studio-providers"] });
  const remove = useMutation({
    mutationFn: studioApi.deleteProvider,
    onSuccess: refresh
  });
  const updateKey = useMutation({
    mutationFn: () => studioApi.updateProviderSecret(editingKey!.id, replacementKey),
    onSuccess: async () => {
      setEditingKey(null);
      setReplacementKey("");
      await refresh();
    },
    onError: (reason: Error) => setError(reason.message)
  });

  async function testProvider(provider: StudioProvider) {
    setTestingId(provider.id);
    setResults((current) => ({ ...current, [provider.id]: { ok: false, message: "正在连接" } }));
    try {
      const result = await studioApi.testProvider(provider.id);
      const message = result.ok
        ? `连接正常 · ${Math.round(result.latency_ms)} ms · ${result.model_count} 个模型`
        : result.error?.message ?? "连接失败";
      setResults((current) => ({ ...current, [provider.id]: { ok: result.ok, message } }));
    } catch (reason) {
      setResults((current) => ({
        ...current,
        [provider.id]: { ok: false, message: reason instanceof Error ? reason.message : "连接失败" }
      }));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section className="models-page">
      <header className="page-toolbar">
        <div>
          <h1>模型与 API</h1>
          <span>{connected.length} 个已配置服务</span>
        </div>
        <div className="toolbar-actions">
          <Link className="secondary-button" to="/advanced-api"><Braces size={16} /> 自定义 HTTP</Link>
          <button type="button" className="primary-button" onClick={() => setDialogOpen(true)}>
            <Plus size={16} /> 添加服务
          </button>
        </div>
      </header>

      <div className="provider-summary">
        <ShieldCheck size={18} />
        <span>API Key 保存在 Windows 凭据管理器中，项目数据仅存于本机。</span>
      </div>

      <div className="provider-list">
        <div className="provider-list-head" aria-hidden="true">
          <span>服务</span><span>默认模型</span><span>凭据</span><span>健康状态</span><span>操作</span>
        </div>
        {isLoading ? <div className="loading-line">正在读取服务...</div> : null}
        {!isLoading && connected.length === 0 ? (
          <button type="button" className="empty-providers" onClick={() => setDialogOpen(true)}>
            <PlugZap size={27} />
            <strong>连接第一个模型服务</strong>
            <span>DeepSeek、OpenAI、Anthropic、Gemini、xAI 或 OpenRouter</span>
          </button>
        ) : null}
        {providerGroups.map((group) => (
          <section className="provider-group" key={group.label}>
            <header>{group.label}<span>{group.items.length}</span></header>
            {group.items.map((provider) => {
              const result = results[provider.id];
              return (
              <article className="provider-row" key={provider.id}>
              <div className="provider-identity">
                <span className="provider-name"><i className={`health-dot ${result?.ok ? "ok" : result ? "failed" : "idle"}`} /><strong>{provider.name}</strong></span>
                <span title={provider.base_url}>{provider.base_url}</span>
              </div>
              <div className="provider-model">
                <span>默认模型</span>
                <strong>{provider.model ?? provider.models?.[0]?.name ?? "未设置"}</strong>
              </div>
              <div className="provider-secret">
                <span>{provider.env_var_name ? "环境变量" : "Windows 凭据"}</span>
                <strong className={provider.secret_stored || provider.env_var_name ? "ok" : "warn"}>
                  {provider.env_var_name ?? (provider.secret_stored ? "已保存" : "未保存")}
                </strong>
              </div>
              <div className={`provider-test ${result?.ok ? "ok" : result ? "failed" : ""}`}>
                {result?.ok ? <CheckCircle2 size={14} /> : null}
                <span>{result?.message ?? "待测试"}</span>
              </div>
              <div className="provider-actions">
                <button type="button" title="更新 API Key" onClick={() => setEditingKey(provider)}>
                  <KeyRound size={13} />设置
                </button>
                <button type="button" title="测试连接" onClick={() => testProvider(provider)}>
                  {testingId === provider.id ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}测试
                </button>
                <button
                  type="button"
                  className="danger"
                  title="删除服务"
                  onClick={() => window.confirm(`删除 ${provider.name}？`) && remove.mutate(provider.id)}
                >
                  <Trash2 size={13} />删除
                </button>
              </div>
            </article>
            );})}
          </section>
        ))}
      </div>

      <AnimatePresence>
        {dialogOpen ? (
          <motion.div className="dialog-backdrop" role="presentation" {...dialogBackdrop}>
            <motion.section className="dialog provider-dialog" role="dialog" aria-modal="true" {...dialogCard}>
              <header><div><h2>添加模型服务</h2><span>创建后即可分配给创作 Agent</span></div><button type="button" className="icon-button" aria-label="关闭添加模型服务" title="关闭" onClick={() => setDialogOpen(false)}><X size={17} /></button></header>
              <ProviderDialogForm
                onSuccess={() => setDialogOpen(false)}
                onCancel={() => setDialogOpen(false)}
              />
            </motion.section>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {editingKey ? (
          <motion.div className="dialog-backdrop" role="presentation" {...dialogBackdrop}>
            <motion.form className="dialog small-dialog" onSubmit={(event) => { event.preventDefault(); updateKey.mutate(); }} {...dialogCard}>
              <header><div><h2>更新 API Key</h2><span>{editingKey.name}</span></div><button type="button" className="icon-button" aria-label="关闭更新 API Key" title="关闭" onClick={() => setEditingKey(null)}><X size={17} /></button></header>
              <label><span>新的 API Key</span><input required autoFocus type="password" autoComplete="new-password" value={replacementKey} onChange={(event) => setReplacementKey(event.target.value)} /></label>
              {error ? <div className="form-error">{error}</div> : null}
              <footer><button type="button" className="secondary-button" onClick={() => setEditingKey(null)}>取消</button><button type="submit" className="primary-button" disabled={updateKey.isPending}><KeyRound size={16} /> 更新</button></footer>
            </motion.form>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}
