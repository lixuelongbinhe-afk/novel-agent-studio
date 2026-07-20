import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Braces,
  CheckCircle2,
  CircleAlert,
  Gauge,
  KeyRound,
  Pencil,
  Plus,
  Radio,
  RefreshCw,
  Save,
  Send,
  Server,
  Settings2,
  SlidersHorizontal,
  Square,
  Trash2,
  Wifi
} from "lucide-react";
import {
  api,
  type ModelDebugRequest,
  type ExecutionPreflight,
  type ModelProfile,
  type ModelRoute,
  type Provider,
  type ProviderConnection,
  type ProviderPreset
} from "../api/client";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";
import { useUiStore } from "../stores/ui";
import { ModelControlPanel } from "./ModelControlPanel";

type CenterTab = "providers" | "control" | "debug";
type ProviderForm = {
  presetSlug: string;
  name: string;
  provider_type: string;
  credential_env_var: string;
  base_url: string;
  enabled: boolean;
};
type ModelForm = {
  provider_account_id: number;
  name: string;
  display_name: string;
  context_window: number;
  tokenizer_name: string;
  tokenizer_source: "" | "official_tokenizer" | "compatible_tokenizer";
  enabled: boolean;
};
type PresetForm = Omit<ProviderPreset, "id" | "revision" | "options"> & {
  optionsJson: string;
};

const PROTOCOLS = [
  ["mock", "Mock（本地）"],
  ["openai_responses", "OpenAI Responses"],
  ["openai_chat", "OpenAI Chat Completions"],
  ["anthropic", "Anthropic Messages"],
  ["gemini", "Gemini 原生"],
  ["ollama", "Ollama Native"],
  ["openai_compatible", "通用 OpenAI-compatible"],
  ["anthropic_compatible", "通用 Anthropic-compatible"]
] as const;

const emptyProvider = (): ProviderForm => ({
  presetSlug: "",
  name: "",
  provider_type: "openai_compatible",
  credential_env_var: "",
  base_url: "",
  enabled: true
});

const emptyModel = (providerId = 0): ModelForm => ({
  provider_account_id: providerId,
  name: "",
  display_name: "",
  context_window: 8192,
  tokenizer_name: "",
  tokenizer_source: "",
  enabled: true
});

const emptyPreset = (): PresetForm => ({
  slug: "",
  name: "",
  protocol: "openai_chat",
  base_url: "",
  default_model: "",
  credential_env_var_hint: "",
  optionsJson: "{}"
});

export function ModelCenterPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<CenterTab>("providers");
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.listProviders });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const presetsQuery = useQuery({ queryKey: ["provider-presets"], queryFn: api.listPresets });
  const providers = providersQuery.data ?? [];
  const models = modelsQuery.data ?? [];
  const presets = presetsQuery.data ?? [];

  const [providerOpen, setProviderOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [providerForm, setProviderForm] = useState<ProviderForm>(emptyProvider);
  const [modelOpen, setModelOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<ModelProfile | null>(null);
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModel);
  const [presetOpen, setPresetOpen] = useState(false);
  const [editingPreset, setEditingPreset] = useState<ProviderPreset | null>(null);
  const [presetForm, setPresetForm] = useState<PresetForm>(emptyPreset);
  const [presetValidation, setPresetValidation] = useState("");
  const [connections, setConnections] = useState<Record<number, ProviderConnection>>({});
  const [syncMessages, setSyncMessages] = useState<Record<number, string>>({});

  const saveProvider = useMutation({
    mutationFn: () => {
      const payload = {
        name: providerForm.name.trim(),
        provider_type: providerForm.provider_type,
        credential_env_var: providerForm.credential_env_var.trim() || null,
        base_url: providerForm.base_url.trim() || null,
        enabled: providerForm.enabled
      };
      return editingProvider
        ? api.updateProvider(editingProvider, payload)
        : api.createProvider(payload);
    },
    onSuccess: async () => {
      setProviderOpen(false);
      setEditingProvider(null);
      setProviderForm(emptyProvider());
      await queryClient.invalidateQueries({ queryKey: ["providers"] });
    }
  });

  const deleteProvider = useMutation({
    mutationFn: api.deleteProvider,
    onSuccess: async (_, provider) => {
      setConnections((current) => {
        const next = { ...current };
        delete next[provider.id];
        return next;
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["providers"] }),
        queryClient.invalidateQueries({ queryKey: ["models"] })
      ]);
    }
  });

  const testProvider = useMutation({
    mutationFn: api.testProvider,
    onSuccess: (result, providerId) => {
      setConnections((current) => ({ ...current, [providerId]: result }));
    }
  });

  const syncModels = useMutation({
    mutationFn: api.syncProviderModels,
    onSuccess: async (result, providerId) => {
      setSyncMessages((current) => ({
        ...current,
        [providerId]: `发现 ${result.discovered}，新增 ${result.created}，更新 ${result.updated}`
      }));
      await queryClient.invalidateQueries({ queryKey: ["models"] });
    }
  });

  const saveModel = useMutation({
    mutationFn: () => {
      const payload = {
        provider_account_id: modelForm.provider_account_id,
        name: modelForm.name,
        display_name: modelForm.display_name,
        context_window: modelForm.context_window,
        tokenizer_name: modelForm.tokenizer_name.trim() || null,
        tokenizer_source: modelForm.tokenizer_name.trim()
          ? modelForm.tokenizer_source || "compatible_tokenizer" as const
          : null,
        enabled: modelForm.enabled
      };
      return editingModel
        ? api.updateModel(editingModel, payload)
        : api.createModel(payload);
    },
    onSuccess: async () => {
      setModelOpen(false);
      setEditingModel(null);
      setModelForm(emptyModel(providers[0]?.id));
      await queryClient.invalidateQueries({ queryKey: ["models"] });
    }
  });

  const deleteModel = useMutation({
    mutationFn: api.deleteModel,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["models"] })
  });

  const savePreset = useMutation({
    mutationFn: (options: Record<string, unknown>) => {
      const payload = {
        slug: presetForm.slug.trim(),
        name: presetForm.name.trim(),
        protocol: presetForm.protocol,
        base_url: presetForm.base_url.trim(),
        default_model: presetForm.default_model.trim(),
        credential_env_var_hint: presetForm.credential_env_var_hint.trim(),
        options
      };
      return editingPreset
        ? api.updatePreset(editingPreset, payload)
        : api.createPreset(payload);
    },
    onSuccess: async (preset) => {
      setEditingPreset(preset);
      setPresetForm(formFromPreset(preset));
      await queryClient.invalidateQueries({ queryKey: ["provider-presets"] });
    }
  });

  function openCreateProvider() {
    setEditingProvider(null);
    setProviderForm(emptyProvider());
    saveProvider.reset();
    setProviderOpen(true);
  }

  function openEditProvider(provider: Provider) {
    setEditingProvider(provider);
    setProviderForm({
      presetSlug: "",
      name: provider.name,
      provider_type: provider.provider_type,
      credential_env_var: provider.credential_env_var ?? "",
      base_url: provider.base_url ?? "",
      enabled: provider.enabled
    });
    saveProvider.reset();
    setProviderOpen(true);
  }

  function applyPreset(slug: string) {
    const preset = presets.find((item) => item.slug === slug);
    setProviderForm((current) => ({
      ...current,
      presetSlug: slug,
      provider_type: preset?.protocol ?? current.provider_type,
      base_url: preset?.base_url ?? current.base_url,
      credential_env_var: preset?.credential_env_var_hint ?? current.credential_env_var
    }));
  }

  function openCreateModel(providerId: number) {
    setEditingModel(null);
    setModelForm(emptyModel(providerId));
    saveModel.reset();
    setModelOpen(true);
  }

  function openEditModel(model: ModelProfile) {
    setEditingModel(model);
    setModelForm({
      provider_account_id: model.provider_account_id,
      name: model.name,
      display_name: model.display_name,
      context_window: model.context_window,
      tokenizer_name: model.tokenizer_name ?? "",
      tokenizer_source: model.tokenizer_source ?? "",
      enabled: model.enabled
    });
    saveModel.reset();
    setModelOpen(true);
  }

  function openPresetManager(preset?: ProviderPreset) {
    const selected = preset ?? presets[0] ?? null;
    setEditingPreset(selected);
    setPresetForm(selected ? formFromPreset(selected) : emptyPreset());
    setPresetValidation("");
    savePreset.reset();
    setPresetOpen(true);
  }

  function selectPresetForEditing(preset: ProviderPreset) {
    setEditingPreset(preset);
    setPresetForm(formFromPreset(preset));
    setPresetValidation("");
    savePreset.reset();
  }

  function submitPreset(event: FormEvent) {
    event.preventDefault();
    try {
      const parsed = JSON.parse(presetForm.optionsJson) as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("options 必须是 JSON 对象");
      }
      setPresetValidation("");
      savePreset.mutate(parsed as Record<string, unknown>);
    } catch (error) {
      setPresetValidation(error instanceof Error ? error.message : "Options JSON 无效");
    }
  }

  return (
    <section className="page-stack model-center-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">模型与协议</span>
          <h1>模型中心</h1>
        </div>
        {activeTab === "providers" ? (
          <div className="page-actions">
            <button className="secondary-button" type="button" disabled={presetsQuery.isLoading} onClick={() => openPresetManager()}>
              <Settings2 size={17} />管理预设
            </button>
            <button className="primary-button" type="button" onClick={openCreateProvider}>
              <Plus size={17} />添加 Provider
            </button>
          </div>
        ) : null}
      </header>

      <nav className="segmented-tabs" aria-label="模型中心视图">
        <button className={activeTab === "providers" ? "active" : ""} type="button" onClick={() => setActiveTab("providers")}>
          <Server size={16} />Provider 与模型
        </button>
        <button className={activeTab === "debug" ? "active" : ""} type="button" onClick={() => setActiveTab("debug")}>
          <Braces size={16} />调试台
        </button>
        <button className={activeTab === "control" ? "active" : ""} type="button" onClick={() => setActiveTab("control")}>
          <SlidersHorizontal size={16} />能力与控制
        </button>
      </nav>

      {activeTab === "providers" ? (
        <ProviderView
          providers={providers}
          models={models}
          connections={connections}
          syncMessages={syncMessages}
          loading={providersQuery.isLoading || modelsQuery.isLoading}
          error={providersQuery.error ? "无法读取 Provider 配置。" : null}
          testingId={testProvider.isPending ? testProvider.variables : null}
          syncingId={syncModels.isPending ? syncModels.variables : null}
          onAddModel={openCreateModel}
          onEditModel={openEditModel}
          onDeleteModel={(model) => {
            if (window.confirm(`删除模型“${model.display_name}”？`)) deleteModel.mutate(model);
          }}
          onEditProvider={openEditProvider}
          onDeleteProvider={(provider) => {
            if (window.confirm(`删除 Provider“${provider.name}”？`)) deleteProvider.mutate(provider);
          }}
          onTest={(providerId) => testProvider.mutate(providerId)}
          onSync={(providerId) => syncModels.mutate(providerId)}
        />
      ) : activeTab === "control" ? (
        <ModelControlPanel providers={providers} models={models} />
      ) : (
        <DebuggerView providers={providers} models={models} />
      )}

      <Dialog
        open={providerOpen}
        title={editingProvider ? "编辑 Provider" : "添加 Provider"}
        onClose={() => setProviderOpen(false)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setProviderOpen(false)}>取消</button>
            <button className="primary-button" type="submit" form="provider-form" disabled={!providerForm.name.trim() || saveProvider.isPending}>
              <Save size={17} />保存
            </button>
          </>
        }
      >
        <form id="provider-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); saveProvider.mutate(); }}>
          {saveProvider.error ? <ErrorNotice message="保存失败，请检查名称、URL 和环境变量名。" /> : null}
          {!editingProvider ? (
            <FormField label="Provider 预设">
              <select value={providerForm.presetSlug} onChange={(event) => applyPreset(event.target.value)}>
                <option value="">自定义</option>
                {presets.map((preset) => <option key={preset.id} value={preset.slug}>{preset.name}</option>)}
              </select>
            </FormField>
          ) : null}
          <FormField label="显示名称">
            <input value={providerForm.name} onChange={(event) => setProviderForm({ ...providerForm, name: event.target.value })} placeholder="例如：DeepSeek 主账号" autoFocus />
          </FormField>
          <FormField label="协议">
            <select value={providerForm.provider_type} onChange={(event) => setProviderForm({ ...providerForm, provider_type: event.target.value })}>
              {PROTOCOLS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </FormField>
          <FormField label="Base URL">
            <input value={providerForm.base_url} onChange={(event) => setProviderForm({ ...providerForm, base_url: event.target.value })} placeholder="https://api.example.com/v1" />
          </FormField>
          <FormField label="API Key 环境变量名" hint="只填写变量名，例如 DEEPSEEK_API_KEY">
            <div className="input-with-icon">
              <KeyRound size={16} />
              <input value={providerForm.credential_env_var} onChange={(event) => setProviderForm({ ...providerForm, credential_env_var: event.target.value.toUpperCase() })} placeholder="PROVIDER_API_KEY" />
            </div>
          </FormField>
          <label className="checkbox-row">
            <input type="checkbox" checked={providerForm.enabled} onChange={(event) => setProviderForm({ ...providerForm, enabled: event.target.checked })} />
            <span>启用 Provider</span>
          </label>
        </form>
      </Dialog>

      <Dialog
        open={modelOpen}
        title={editingModel ? "编辑模型" : "手动添加模型"}
        onClose={() => setModelOpen(false)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setModelOpen(false)}>取消</button>
            <button className="primary-button" type="submit" form="model-form" disabled={!modelForm.provider_account_id || !modelForm.name.trim() || !modelForm.display_name.trim() || saveModel.isPending}>
              <Save size={17} />保存
            </button>
          </>
        }
      >
        <form id="model-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); saveModel.mutate(); }}>
          {saveModel.error ? <ErrorNotice message="模型保存失败，请检查 ID 是否重复。" /> : null}
          <FormField label="Provider">
            <select disabled={Boolean(editingModel)} value={modelForm.provider_account_id || ""} onChange={(event) => setModelForm({ ...modelForm, provider_account_id: Number(event.target.value) })}>
              <option value="" disabled>请选择 Provider</option>
              {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
            </select>
          </FormField>
          <FormField label="模型 ID">
            <input disabled={Boolean(editingModel)} value={modelForm.name} onChange={(event) => setModelForm({ ...modelForm, name: event.target.value })} placeholder="模型 API 名称" />
          </FormField>
          <FormField label="显示名称">
            <input value={modelForm.display_name} onChange={(event) => setModelForm({ ...modelForm, display_name: event.target.value })} />
          </FormField>
          <FormField label="上下文窗口">
            <input type="number" min={512} value={modelForm.context_window} onChange={(event) => setModelForm({ ...modelForm, context_window: Number(event.target.value) })} />
          </FormField>
          <div className="form-row">
            <FormField label="Tokenizer" hint="例如 cl100k_base；留空使用本地近似">
              <input value={modelForm.tokenizer_name} onChange={(event) => setModelForm({ ...modelForm, tokenizer_name: event.target.value, tokenizer_source: event.target.value ? modelForm.tokenizer_source || "compatible_tokenizer" : "" })} placeholder="cl100k_base" />
            </FormField>
            <FormField label="Tokenizer 来源">
              <select disabled={!modelForm.tokenizer_name} value={modelForm.tokenizer_source} onChange={(event) => setModelForm({ ...modelForm, tokenizer_source: event.target.value as ModelForm["tokenizer_source"] })}>
                <option value="">未配置</option>
                <option value="official_tokenizer">官方 tokenizer</option>
                <option value="compatible_tokenizer">兼容 tokenizer</option>
              </select>
            </FormField>
          </div>
          <label className="checkbox-row">
            <input type="checkbox" checked={modelForm.enabled} onChange={(event) => setModelForm({ ...modelForm, enabled: event.target.checked })} />
            <span>启用模型</span>
          </label>
        </form>
      </Dialog>

      <Dialog
        open={presetOpen}
        title="Provider 预设"
        width="large"
        onClose={() => setPresetOpen(false)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setPresetOpen(false)}>关闭</button>
            <button className="primary-button" type="submit" form="preset-form" disabled={!presetForm.slug.trim() || !presetForm.name.trim() || savePreset.isPending}>
              <Save size={17} />保存预设
            </button>
          </>
        }
      >
        <div className="preset-manager">
          <aside className="preset-list" aria-label="预设列表">
            {presets.map((preset) => (
              <button key={preset.id} type="button" className={editingPreset?.id === preset.id ? "selected" : ""} onClick={() => selectPresetForEditing(preset)}>
                <strong>{preset.name}</strong><span>{preset.protocol}</span>
              </button>
            ))}
            <button className={!editingPreset ? "selected" : ""} type="button" onClick={() => { setEditingPreset(null); setPresetForm(emptyPreset()); setPresetValidation(""); }}>
              <Plus size={15} /><strong>新建预设</strong>
            </button>
          </aside>
          <form id="preset-form" className="form-grid preset-form" onSubmit={submitPreset}>
            {presetValidation ? <ErrorNotice message={presetValidation} /> : null}
            {savePreset.error ? <ErrorNotice message="预设保存失败，请检查 slug 是否重复。" /> : null}
            <div className="form-row">
              <FormField label="名称"><input value={presetForm.name} onChange={(event) => setPresetForm({ ...presetForm, name: event.target.value })} /></FormField>
              <FormField label="Slug"><input value={presetForm.slug} onChange={(event) => setPresetForm({ ...presetForm, slug: event.target.value.toLowerCase() })} /></FormField>
            </div>
            <FormField label="协议">
              <select value={presetForm.protocol} onChange={(event) => setPresetForm({ ...presetForm, protocol: event.target.value })}>
                {PROTOCOLS.filter(([value]) => value !== "mock").map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </FormField>
            <FormField label="Base URL"><input value={presetForm.base_url} onChange={(event) => setPresetForm({ ...presetForm, base_url: event.target.value })} /></FormField>
            <div className="form-row">
              <FormField label="默认模型"><input value={presetForm.default_model} onChange={(event) => setPresetForm({ ...presetForm, default_model: event.target.value })} /></FormField>
              <FormField label="环境变量提示"><input value={presetForm.credential_env_var_hint} onChange={(event) => setPresetForm({ ...presetForm, credential_env_var_hint: event.target.value.toUpperCase() })} /></FormField>
            </div>
            <FormField label="Options JSON"><textarea rows={6} spellCheck={false} value={presetForm.optionsJson} onChange={(event) => setPresetForm({ ...presetForm, optionsJson: event.target.value })} /></FormField>
          </form>
        </div>
      </Dialog>
    </section>
  );
}

type ProviderViewProps = {
  providers: Provider[];
  models: ModelProfile[];
  connections: Record<number, ProviderConnection>;
  syncMessages: Record<number, string>;
  loading: boolean;
  error: string | null;
  testingId: number | null;
  syncingId: number | null;
  onAddModel: (providerId: number) => void;
  onEditModel: (model: ModelProfile) => void;
  onDeleteModel: (model: ModelProfile) => void;
  onEditProvider: (provider: Provider) => void;
  onDeleteProvider: (provider: Provider) => void;
  onTest: (providerId: number) => void;
  onSync: (providerId: number) => void;
};

function ProviderView({
  providers,
  models,
  connections,
  syncMessages,
  loading,
  error,
  testingId,
  syncingId,
  onAddModel,
  onEditModel,
  onDeleteModel,
  onEditProvider,
  onDeleteProvider,
  onTest,
  onSync
}: ProviderViewProps) {
  if (error) return <ErrorNotice message={error} />;
  if (loading) return <p className="muted">正在读取模型配置…</p>;
  if (providers.length === 0) return <EmptyState icon={Server} title="还没有 Provider" description="添加 Provider 后即可维护模型。" />;
  return (
    <div className="provider-list">
      {providers.map((provider) => {
        const providerModels = models.filter((model) => model.provider_account_id === provider.id);
        const connection = connections[provider.id];
        const testing = testingId === provider.id;
        const syncing = syncingId === provider.id;
        return (
          <section key={provider.id} className="provider-row">
            <header>
              <div className="provider-icon"><Server size={19} /></div>
              <div><h2>{provider.name}</h2><span>{provider.provider_type}</span></div>
              <ConnectionBadge provider={provider} connection={connection} testing={testing} />
              <div className="provider-actions">
                <button className="icon-button ghost" type="button" title="测试连接" aria-label={`测试 ${provider.name}`} disabled={testing} onClick={() => onTest(provider.id)}><Wifi size={16} /></button>
                <button className="icon-button ghost" type="button" title="同步模型" aria-label={`同步 ${provider.name} 的模型`} disabled={syncing} onClick={() => onSync(provider.id)}><RefreshCw className={syncing ? "spin" : ""} size={16} /></button>
                <button className="icon-button ghost" type="button" title="添加模型" aria-label={`为 ${provider.name} 添加模型`} onClick={() => onAddModel(provider.id)}><Plus size={16} /></button>
                <button className="icon-button ghost" type="button" title="编辑 Provider" aria-label={`编辑 ${provider.name}`} onClick={() => onEditProvider(provider)}><Pencil size={16} /></button>
                <button className="icon-button ghost danger-ink" type="button" title="删除 Provider" aria-label={`删除 ${provider.name}`} onClick={() => onDeleteProvider(provider)}><Trash2 size={16} /></button>
              </div>
            </header>
            <div className="provider-details">
              <div><span>Base URL</span><strong>{provider.base_url || "内置 Mock"}</strong></div>
              <div><span>凭据</span><strong>{provider.credential_env_var || "无需密钥"}</strong></div>
              <div><span>模型</span><strong>{providerModels.length}</strong></div>
              <div><span>最近操作</span><strong>{syncMessages[provider.id] ?? connectionSummary(connection)}</strong></div>
            </div>
            <div className="model-table">
              {providerModels.length === 0 ? (
                <p className="muted">尚未配置模型。</p>
              ) : providerModels.map((model) => (
                <div key={model.id}>
                  <Boxes size={16} />
                  <strong>{model.display_name}</strong>
                  <span>{model.name}</span>
                  <small>{model.context_window.toLocaleString()} tokens</small>
                  <span className={`status-chip ${model.enabled ? "enabled" : "disabled"}`}>{model.enabled ? "启用" : "停用"}</span>
                  <div className="model-actions">
                    <button className="icon-button ghost" type="button" title="编辑模型" aria-label={`编辑模型 ${model.display_name}`} onClick={() => onEditModel(model)}><Pencil size={15} /></button>
                    <button className="icon-button ghost danger-ink" type="button" title="删除模型" aria-label={`删除模型 ${model.display_name}`} onClick={() => onDeleteModel(model)}><Trash2 size={15} /></button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ConnectionBadge({ provider, connection, testing }: { provider: Provider; connection?: ProviderConnection; testing: boolean }) {
  if (testing) return <span className="connection-badge testing"><RefreshCw className="spin" size={14} />测试中</span>;
  if (connection?.ok) return <span className="connection-badge enabled"><CheckCircle2 size={14} />{connection.latency_ms} ms</span>;
  if (connection && !connection.ok) return <span className="connection-badge error" title={connection.error?.message}><CircleAlert size={14} />连接失败</span>;
  return <span className={`connection-badge ${provider.enabled ? "enabled" : "disabled"}`}><CheckCircle2 size={14} />{provider.enabled ? "已启用" : "已停用"}</span>;
}

function DebuggerView({ providers, models }: { providers: Provider[]; models: ModelProfile[] }) {
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const enabledProviders = useMemo(() => providers.filter((provider) => provider.enabled), [providers]);
  const routesQuery = useQuery({ queryKey: ["model-routes"], queryFn: () => api.listRoutes() });
  const routes = routesQuery.data ?? [];
  const [targetMode, setTargetMode] = useState<"model" | "route">("model");
  const [providerId, setProviderId] = useState<number | null>(null);
  const [modelName, setModelName] = useState("");
  const [routeId, setRouteId] = useState<number | null>(null);
  const [manualModelId, setManualModelId] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("请为悬疑小说设计一个有明确冲突的开场场景。");
  const [responseFormat, setResponseFormat] = useState<"text" | "json">("text");
  const [output, setOutput] = useState("");
  const [streamError, setStreamError] = useState("");
  const [meta, setMeta] = useState<{ tokens: number; requestId: string; finishReason: string; tokenSource: string } | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [control, setControl] = useState<Record<string, unknown> | null>(null);
  const [preflight, setPreflight] = useState<ExecutionPreflight | null>(null);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const providerModels = useMemo(
    () => models.filter((model) => model.enabled && model.provider_account_id === providerId),
    [models, providerId]
  );
  const selectedRoute = useMemo(() => routes.find((route) => route.id === routeId) ?? null, [routeId, routes]);
  const routeModels = useMemo(
    () => selectedRoute?.entries.map((entry) => models.find((model) => model.id === entry.model_profile_id)).filter((model): model is ModelProfile => Boolean(model)) ?? [],
    [models, selectedRoute]
  );

  useEffect(() => {
    if (providerId !== null && enabledProviders.some((provider) => provider.id === providerId)) return;
    setProviderId(enabledProviders[0]?.id ?? null);
  }, [enabledProviders, providerId]);

  useEffect(() => {
    if (providerModels.some((model) => model.name === modelName)) return;
    setModelName(providerModels[0]?.name ?? "");
  }, [modelName, providerModels]);

  useEffect(() => {
    if (routeId !== null && routes.some((route) => route.id === routeId)) return;
    setRouteId(routes[0]?.id ?? null);
  }, [routeId, routes]);

  useEffect(() => {
    if (selectedRoute?.strategy !== "manual_only") {
      setManualModelId(null);
      return;
    }
    if (routeModels.some((model) => model.id === manualModelId)) return;
    setManualModelId(routeModels[0]?.id ?? null);
  }, [manualModelId, routeModels, selectedRoute]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const debug = useMutation({
    mutationFn: () => api.debugModel(currentDebugPayload()),
    onSuccess: (response) => {
      if (response.error) {
        setOutput("");
        setStreamError(`${response.error.code}: ${response.error.message}`);
      } else {
        const rendered = responseFormat === "json" && response.structured_data
          ? JSON.stringify(response.structured_data, null, 2)
          : response.text;
        setOutput(rendered);
        setStreamError("");
      }
      setWarnings(response.warnings);
      setControl(response.control);
      setMeta({ tokens: response.usage.total_tokens, requestId: response.request_id, finishReason: response.finish_reason, tokenSource: response.usage.source ?? (response.usage.estimated ? "local_approximation" : "provider_actual") });
    }
  });

  const runPreflight = useMutation({
    mutationFn: () => api.preflightModel(currentDebugPayload()),
    onSuccess: (result) => setPreflight(result)
  });

  const targetReady = targetMode === "model" ? modelName.length > 0 : Boolean(routeId) && (selectedRoute?.strategy !== "manual_only" || Boolean(manualModelId));
  const canRun = prompt.trim().length > 0 && targetReady && !debug.isPending && !streaming;
  const outputLabel = responseFormat === "json" ? "结构化 JSON" : "文本输出";

  async function runStream() {
    const controller = new AbortController();
    abortRef.current = controller;
    setOutput("");
    setMeta(null);
    setStreamError("");
    setWarnings([]);
    setControl(null);
    setStreaming(true);
    let requestId = "";
    let tokens = 0;
    try {
      await api.streamModel(
        currentDebugPayload(),
        (event) => {
          requestId = event.request_id ?? requestId;
          if (event.event === "delta") setOutput((value) => value + event.text_delta);
          if (event.event === "tool_call_delta" && event.tool_call) {
            setOutput((value) => `${value}\n[tool] ${event.tool_call?.name}: ${JSON.stringify(event.tool_call?.arguments)}`.trim());
          }
          if (event.event === "usage" && event.usage) {
            tokens = event.usage.total_tokens;
            setMeta({ tokens, requestId, finishReason: "streaming", tokenSource: event.usage.source ?? (event.usage.estimated ? "local_approximation" : "provider_actual") });
          }
          if (event.event === "warning" && event.warning) setWarnings((current) => [...current, event.warning as string]);
          if (event.event === "error" && event.error) setStreamError(`${event.error.code}: ${event.error.message}`);
          if (event.event === "done") setMeta((current) => ({ tokens, requestId, finishReason: event.finish_reason ?? "stop", tokenSource: current?.tokenSource ?? "local_approximation" }));
        },
        controller.signal
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setStreamError(error instanceof Error ? error.message : "流式请求失败");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
    }
  }

  function stopStream() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }

  function currentDebugPayload(): ModelDebugRequest {
    const selectedModel = providerModels.find((model) => model.name === modelName);
    const base = debugPayload(providerId, modelName || "route-selected", prompt, responseFormat);
    return targetMode === "model"
      ? { ...base, model_profile_id: selectedModel?.id ?? null, project_id: selectedProjectId, allow_degradation: true }
      : { ...base, provider_account_id: null, model_profile_id: null, route_id: routeId, manual_model_profile_id: selectedRoute?.strategy === "manual_only" ? manualModelId : null, project_id: selectedProjectId, allow_degradation: true };
  }

  return (
    <div className="debug-workbench">
      <section className="debug-input-pane">
        <header>
          <div><span className="eyebrow">模型请求</span><h2>调试参数</h2></div>
          <div className="segmented-control" aria-label="响应格式">
            <button type="button" className={responseFormat === "text" ? "active" : ""} onClick={() => setResponseFormat("text")}>文本</button>
            <button type="button" className={responseFormat === "json" ? "active" : ""} onClick={() => setResponseFormat("json")}>JSON</button>
          </div>
        </header>
        <div className="debug-request-fields">
          <div className="debug-target-fields">
            <div className="segmented-control" aria-label="模型选择方式">
              <button type="button" className={targetMode === "model" ? "active" : ""} onClick={() => { setTargetMode("model"); setPreflight(null); }}>直连模型</button>
              <button type="button" className={targetMode === "route" ? "active" : ""} onClick={() => { setTargetMode("route"); setPreflight(null); }}>Route</button>
            </div>
            {targetMode === "model" ? (
              <div className="form-row">
                <FormField label="Provider">
                  <select value={providerId ?? ""} onChange={(event) => { setProviderId(Number(event.target.value)); setPreflight(null); }}>
                    <option value="" disabled>请选择 Provider</option>
                    {enabledProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
                  </select>
                </FormField>
                <FormField label="模型">
                  <select value={modelName} onChange={(event) => { setModelName(event.target.value); setPreflight(null); }}>
                    <option value="" disabled>请选择模型</option>
                    {providerModels.map((model) => <option key={model.id} value={model.name}>{model.display_name}</option>)}
                  </select>
                </FormField>
              </div>
            ) : (
              <div className="form-row">
                <FormField label="Route">
                  <select value={routeId ?? ""} onChange={(event) => { setRouteId(Number(event.target.value)); setPreflight(null); }}>
                    <option value="" disabled>请选择 Route</option>
                    {routes.map((route) => <option key={route.id} value={route.id}>{route.name} · {routeStrategyLabel(route)}</option>)}
                  </select>
                </FormField>
                {selectedRoute?.strategy === "manual_only" ? (
                  <FormField label="手动模型">
                    <select value={manualModelId ?? ""} onChange={(event) => { setManualModelId(Number(event.target.value)); setPreflight(null); }}>
                      <option value="" disabled>请选择模型</option>
                      {routeModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}
                    </select>
                  </FormField>
                ) : <FormField label="策略"><input value={selectedRoute ? routeStrategyLabel(selectedRoute) : "—"} disabled /></FormField>}
              </div>
            )}
          </div>
          <FormField label="用户消息"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={14} /></FormField>
        </div>
        {preflight ? <PreflightStrip value={preflight} /> : null}
        {runPreflight.error ? <ErrorNotice message="预检失败，请检查 Route、能力、Tokenizer 和上下文。" /> : null}
        <div className="debug-actions">
          <button className="secondary-button" type="button" disabled={!canRun || runPreflight.isPending} onClick={() => runPreflight.mutate()}><Gauge size={17} />调用预检</button>
          <button className="primary-button" type="button" disabled={!canRun} onClick={() => { setStreamError(""); setWarnings([]); setControl(null); debug.mutate(); }}><Send size={17} />普通响应</button>
          {streaming ? (
            <button className="danger-button" type="button" onClick={stopStream}><Square size={15} />停止</button>
          ) : (
            <button className="secondary-button" type="button" disabled={!canRun} onClick={runStream}><Radio size={17} />流式响应</button>
          )}
        </div>
      </section>
      <section className="debug-output-pane">
        <header>
          <div><span className="eyebrow">标准化响应</span><h2>{outputLabel}</h2></div>
          {meta ? <span>{meta.tokens} tokens · {tokenSourceLabel(meta.tokenSource)} · {meta.finishReason}</span> : null}
        </header>
        {debug.error ? <ErrorNotice message="调试请求失败。" /> : null}
        {streamError ? <div className="stream-error"><CircleAlert size={16} />{streamError}</div> : null}
        {warnings.length ? <div className="debug-warning-list">{warnings.map((warning, index) => <div key={`${warning}-${index}`}><CircleAlert size={14} />{warning}</div>)}</div> : null}
        <pre>{output || (debug.isPending || streaming ? "正在生成…" : "尚无输出")}</pre>
        {meta ? <footer>request_id: {meta.requestId || "pending"}{control ? ` · queue: ${String(control.queue_ms ?? 0)} ms · fallback: ${String(control.fallback_count ?? 0)}` : ""}</footer> : null}
      </section>
    </div>
  );
}

function PreflightStrip({ value }: { value: ExecutionPreflight }) {
  const percent = Math.round(value.context.utilization * 100);
  return (
    <div className={`preflight-strip preflight-${value.context.level}`}>
      <div><span>输入</span><strong>{value.context.input.tokens.toLocaleString()}</strong><small>{tokenSourceLabel(value.context.input.source)}</small></div>
      <div><span>预留输出</span><strong>{value.context.reserved_output_tokens.toLocaleString()}</strong><small>tokens</small></div>
      <div><span>上下文</span><strong>{percent}%</strong><small>{value.context.total_tokens.toLocaleString()} / {value.context.context_window.toLocaleString()}</small></div>
      <div><span>预计费用</span><strong>{value.estimated_cost.known && value.estimated_cost.amount !== null ? value.estimated_cost.amount.toFixed(6) : "未知"}</strong><small>{value.estimated_cost.currency}</small></div>
    </div>
  );
}

function debugPayload(
  providerId: number | null,
  model: string,
  prompt: string,
  responseFormat: "text" | "json"
): ModelDebugRequest {
  return {
    provider_account_id: providerId,
    model,
    response_format: responseFormat,
    messages: [{ role: "user", content: [{ type: "text", text: prompt }] }]
  };
}

function formFromPreset(preset: ProviderPreset): PresetForm {
  return {
    slug: preset.slug,
    name: preset.name,
    protocol: preset.protocol,
    base_url: preset.base_url,
    default_model: preset.default_model,
    credential_env_var_hint: preset.credential_env_var_hint,
    optionsJson: JSON.stringify(preset.options, null, 2)
  };
}

function connectionSummary(connection?: ProviderConnection): string {
  if (!connection) return "尚未测试";
  if (connection.ok) return `${connection.model_count} 个模型 · ${connection.latency_ms} ms`;
  return connection.error?.code ?? "连接失败";
}

function routeStrategyLabel(route: ModelRoute): string {
  return ({ ordered_fallback: "有序回退", lowest_cost: "最低费用", lowest_latency: "最低延迟", healthiest: "最健康", manual_only: "仅手动" } as Record<ModelRoute["strategy"], string>)[route.strategy];
}

function tokenSourceLabel(source: string): string {
  return ({ provider_actual: "供应商实际", provider_estimate: "供应商估算", official_tokenizer: "官方 tokenizer", compatible_tokenizer: "兼容 tokenizer", local_approximation: "本地近似" } as Record<string, string>)[source] ?? source;
}
