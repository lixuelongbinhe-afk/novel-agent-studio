import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

type PresetKey =
  | "deepseek"
  | "openai"
  | "anthropic"
  | "gemini"
  | "xai"
  | "openrouter"
  | "openai_compatible";

const presets: Record<PresetKey, { label: string; baseUrl: string; model: string; env: string }> = {
  deepseek: {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    env: "DEEPSEEK_API_KEY"
  },
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-5-mini",
    env: "OPENAI_API_KEY"
  },
  anthropic: {
    label: "Anthropic",
    baseUrl: "https://api.anthropic.com",
    model: "claude-sonnet-4-5",
    env: "ANTHROPIC_API_KEY"
  },
  gemini: {
    label: "Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta",
    model: "gemini-2.5-flash",
    env: "GEMINI_API_KEY"
  },
  xai: {
    label: "xAI / Grok",
    baseUrl: "https://api.x.ai/v1",
    model: "grok-4",
    env: "XAI_API_KEY"
  },
  openrouter: {
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "openai/gpt-4.1-mini",
    env: "OPENROUTER_API_KEY"
  },
  openai_compatible: {
    label: "OpenAI 兼容服务",
    baseUrl: "https://",
    model: "",
    env: "PROVIDER_API_KEY"
  }
};

function initialForm(preset: PresetKey = "deepseek") {
  const item = presets[preset];
  return {
    preset,
    name: item.label,
    base_url: item.baseUrl,
    model: item.model,
    api_key: "",
    use_env: false,
    env_var_name: item.env
  };
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  const { data: providers = [], isLoading } = useQuery({
    queryKey: ["studio-providers"],
    queryFn: studioApi.providers
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState(initialForm());
  const [editingKey, setEditingKey] = useState<StudioProvider | null>(null);
  const [replacementKey, setReplacementKey] = useState("");
  const [testingId, setTestingId] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, { ok: boolean; message: string }>>({});
  const [error, setError] = useState("");

  const connected = useMemo(
    () => providers.filter((provider) => !["mock", "ollama", "ollama_native"].includes(provider.provider_type)),
    [providers]
  );

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["studio-providers"] });
  const create = useMutation({
    mutationFn: () => studioApi.setupProvider({
      preset: form.preset,
      name: form.name,
      base_url: form.base_url,
      model: form.model,
      api_key: form.use_env ? null : form.api_key,
      env_var_name: form.use_env ? form.env_var_name : null
    }),
    onSuccess: async () => {
      setDialogOpen(false);
      setForm(initialForm());
      setError("");
      await refresh();
    },
    onError: (reason: Error) => setError(reason.message)
  });
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

  function choosePreset(preset: PresetKey) {
    setForm(initialForm(preset));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    create.mutate();
  }

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
        {isLoading ? <div className="loading-line">正在读取服务...</div> : null}
        {!isLoading && connected.length === 0 ? (
          <button type="button" className="empty-providers" onClick={() => setDialogOpen(true)}>
            <PlugZap size={27} />
            <strong>连接第一个模型服务</strong>
            <span>DeepSeek、OpenAI、Anthropic、Gemini、xAI 或 OpenRouter</span>
          </button>
        ) : null}
        {connected.map((provider) => {
          const result = results[provider.id];
          return (
            <article className="provider-row" key={provider.id}>
              <div className="provider-icon"><PlugZap size={18} /></div>
              <div className="provider-identity">
                <strong>{provider.name}</strong>
                <span>{provider.base_url}</span>
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
                <span>{result?.message ?? "尚未测试"}</span>
              </div>
              <div className="row-actions">
                <button type="button" className="icon-button" title="测试连接" onClick={() => testProvider(provider)}>
                  {testingId === provider.id ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
                </button>
                <button type="button" className="icon-button" title="更新 API Key" onClick={() => setEditingKey(provider)}>
                  <KeyRound size={16} />
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  title="删除服务"
                  onClick={() => window.confirm(`删除 ${provider.name}？`) && remove.mutate(provider.id)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          );
        })}
      </div>

      {dialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <form className="dialog provider-dialog" onSubmit={submit}>
            <header><div><h2>添加模型服务</h2><span>创建后即可分配给创作 Agent</span></div><button type="button" className="icon-button" onClick={() => setDialogOpen(false)}><X size={17} /></button></header>
            <div className="preset-grid">
              {(Object.keys(presets) as PresetKey[]).map((key) => (
                <button key={key} type="button" className={form.preset === key ? "selected" : ""} onClick={() => choosePreset(key)}>
                  {presets[key].label}
                </button>
              ))}
            </div>
            <div className="form-grid two-columns">
              <label><span>显示名称</span><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label><span>模型名称</span><input required value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} placeholder="例如 deepseek-chat" /></label>
              <label className="wide"><span>API 地址</span><input required value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
            </div>
            <div className="segmented-control compact">
              <button type="button" className={!form.use_env ? "active" : ""} onClick={() => setForm({ ...form, use_env: false })}>保存 API Key</button>
              <button type="button" className={form.use_env ? "active" : ""} onClick={() => setForm({ ...form, use_env: true })}>读取环境变量</button>
            </div>
            {form.use_env ? (
              <label><span>环境变量名</span><input required value={form.env_var_name} onChange={(event) => setForm({ ...form, env_var_name: event.target.value.toUpperCase() })} /></label>
            ) : (
              <label><span>API Key</span><input required type="password" autoComplete="new-password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder="sk-..." /></label>
            )}
            {error ? <div className="form-error">{error}</div> : null}
            <footer><button type="button" className="secondary-button" onClick={() => setDialogOpen(false)}>取消</button><button type="submit" className="primary-button" disabled={create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={16} /> : <PlugZap size={16} />} 保存服务</button></footer>
          </form>
        </div>
      ) : null}

      {editingKey ? (
        <div className="dialog-backdrop" role="presentation">
          <form className="dialog small-dialog" onSubmit={(event) => { event.preventDefault(); updateKey.mutate(); }}>
            <header><div><h2>更新 API Key</h2><span>{editingKey.name}</span></div><button type="button" className="icon-button" onClick={() => setEditingKey(null)}><X size={17} /></button></header>
            <label><span>新的 API Key</span><input required autoFocus type="password" autoComplete="new-password" value={replacementKey} onChange={(event) => setReplacementKey(event.target.value)} /></label>
            {error ? <div className="form-error">{error}</div> : null}
            <footer><button type="button" className="secondary-button" onClick={() => setEditingKey(null)}>取消</button><button type="submit" className="primary-button" disabled={updateKey.isPending}><KeyRound size={16} /> 更新</button></footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}
