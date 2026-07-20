import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Braces,
  CheckCircle2,
  CircleAlert,
  Download,
  FileJson,
  KeyRound,
  Pencil,
  Play,
  Plus,
  Radio,
  Save,
  ServerCog,
  ShieldCheck,
  Square,
  Trash2,
  Upload
} from "lucide-react";
import {
  api,
  type CredentialReference,
  type GenericAdapter,
  type GenericAdapterInput,
  type GenericAdapterManifest,
  type GenericAdapterTest,
  type GenericAuth,
  type ModelDebugRequest,
  type Provider
} from "../api/client";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";

type AdapterForm = {
  providerName: string;
  baseUrl: string;
  credential_reference_id: number | null;
  method: "GET" | "POST";
  endpoint: string;
  content_type: string;
  response_mode: "json" | "raw_text";
  stream_format: "sse" | "ndjson" | "chunked_json" | "raw_text";
  security_mode: "public_only" | "local_private";
  authType: GenericAuth["type"];
  authName: string;
  authUsername: string;
  authPrefix: string;
  queryJson: string;
  headersJson: string;
  requestTemplateJson: string;
  parameterMappingJson: string;
  responseMappingJson: string;
  streamMappingJson: string;
  errorMappingJson: string;
  capabilityDefaultsJson: string;
};

const DEFAULT_TEMPLATE = {
  model: { $var: "model" },
  messages: { $var: "messages" },
  temperature: { $var: "temperature" },
  stream: { $var: "stream" }
};
const DEFAULT_RESPONSE_MAPPING = {
  text: "$.choices[0].message.content",
  model: "$.model",
  finish_reason: "$.choices[0].finish_reason",
  request_id: "$.id",
  usage: {
    input_tokens: "$.usage.prompt_tokens",
    output_tokens: "$.usage.completion_tokens",
    total_tokens: "$.usage.total_tokens"
  }
};
const DEFAULT_STREAM_MAPPING = {
  text_delta: "$.choices[0].delta.content",
  done: "$.done",
  usage: {
    input_tokens: "$.usage.prompt_tokens",
    output_tokens: "$.usage.completion_tokens",
    total_tokens: "$.usage.total_tokens"
  }
};

function emptyForm(): AdapterForm {
  return {
    providerName: "",
    baseUrl: "",
    credential_reference_id: null,
    method: "POST",
    endpoint: "/chat",
    content_type: "application/json",
    response_mode: "json",
    stream_format: "sse",
    security_mode: "public_only",
    authType: "none",
    authName: "",
    authUsername: "",
    authPrefix: "",
    queryJson: "{}",
    headersJson: "{}",
    requestTemplateJson: pretty(DEFAULT_TEMPLATE),
    parameterMappingJson: "{}",
    responseMappingJson: pretty(DEFAULT_RESPONSE_MAPPING),
    streamMappingJson: pretty(DEFAULT_STREAM_MAPPING),
    errorMappingJson: pretty({ message: "$.error.message", code: "$.error.code" }),
    capabilityDefaultsJson: "{}"
  };
}

export function CustomApiPage() {
  const queryClient = useQueryClient();
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.listProviders });
  const adaptersQuery = useQuery({ queryKey: ["custom-adapters"], queryFn: api.listCustomAdapters });
  const credentialsQuery = useQuery({ queryKey: ["credentials"], queryFn: api.listCredentials });
  const providers = providersQuery.data ?? [];
  const adapters = adaptersQuery.data ?? [];
  const credentials = credentialsQuery.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<GenericAdapter | null>(null);
  const [form, setForm] = useState<AdapterForm>(emptyForm);
  const [formError, setFormError] = useState("");
  const [credentialOpen, setCredentialOpen] = useState(false);
  const [editingCredential, setEditingCredential] = useState<CredentialReference | null>(null);
  const [credentialForm, setCredentialForm] = useState({ name: "", env_var_name: "" });
  const [prompt, setPrompt] = useState("请写一句有悬念的雾港开场。");
  const [model, setModel] = useState("custom-model");
  const [testResult, setTestResult] = useState<GenericAdapterTest | null>(null);
  const [streamOutput, setStreamOutput] = useState("");
  const [streamError, setStreamError] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const importRef = useRef<HTMLInputElement | null>(null);

  const selected = adapters.find((adapter) => adapter.id === selectedId) ?? adapters[0] ?? null;
  const selectedProvider = providers.find((provider) => provider.id === selected?.provider_account_id) ?? null;
  const targetOrigin = selected?.approved_origin ?? displayOrigin(selectedProvider?.base_url);

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected, selectedId]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const saveAdapter = useMutation({
    mutationFn: async (input: GenericAdapterInput) => {
      if (!editing) {
        const { provider_account_id, ...adapterInput } = input;
        void provider_account_id;
        return api.setupCustomAdapter({
          ...adapterInput,
          provider_name: form.providerName.trim(),
          base_url: form.baseUrl.trim()
        });
      }
      const provider = providers.find((item) => item.id === editing.provider_account_id);
      let current = editing;
      if (provider && (provider.name !== form.providerName.trim() || provider.base_url !== form.baseUrl.trim())) {
        await api.updateProvider(provider, {
          name: form.providerName.trim(),
          base_url: form.baseUrl.trim(),
          provider_type: "generic_json_http"
        });
        current = (await api.listCustomAdapters()).find((item) => item.id === editing.id) ?? editing;
      }
      return api.updateCustomAdapter(current, input);
    },
    onSuccess: async (adapter) => {
      setEditorOpen(false);
      setSelectedId(adapter.id);
      setTestResult(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["custom-adapters"] }),
        queryClient.invalidateQueries({ queryKey: ["providers"] })
      ]);
    }
  });

  const approve = useMutation({
    mutationFn: api.approveCustomOrigin,
    onSuccess: async (adapter) => {
      setSelectedId(adapter.id);
      await queryClient.invalidateQueries({ queryKey: ["custom-adapters"] });
    }
  });

  const testAdapter = useMutation({
    mutationFn: (adapter: GenericAdapter) => api.testCustomAdapter(adapter.id, debugPayload(model, prompt)),
    onSuccess: async (result) => {
      setTestResult(result);
      setStreamOutput("");
      await queryClient.invalidateQueries({ queryKey: ["custom-adapters"] });
    }
  });

  const toggleEnabled = useMutation({
    mutationFn: async (adapter: GenericAdapter) => {
      const provider = providers.find((item) => item.id === adapter.provider_account_id);
      if (!adapter.enabled && provider && !provider.enabled) await api.updateProvider(provider, { enabled: true });
      return api.updateCustomAdapter(adapter, { enabled: !adapter.enabled });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["custom-adapters"] }),
        queryClient.invalidateQueries({ queryKey: ["providers"] })
      ]);
    }
  });

  const removeAdapter = useMutation({
    mutationFn: api.deleteCustomAdapter,
    onSuccess: async () => {
      setSelectedId(null);
      setTestResult(null);
      await queryClient.invalidateQueries({ queryKey: ["custom-adapters"] });
    }
  });

  const saveCredential = useMutation({
    mutationFn: () => editingCredential
      ? api.updateCredential(editingCredential, credentialForm)
      : api.createCredential(credentialForm),
    onSuccess: async () => {
      setEditingCredential(null);
      setCredentialForm({ name: "", env_var_name: "" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["credentials"] }),
        queryClient.invalidateQueries({ queryKey: ["custom-adapters"] })
      ]);
    }
  });

  const removeCredential = useMutation({
    mutationFn: api.deleteCredential,
    onSuccess: async () => {
      setEditingCredential(null);
      setCredentialForm({ name: "", env_var_name: "" });
      await queryClient.invalidateQueries({ queryKey: ["credentials"] });
    }
  });

  const importManifest = useMutation({
    mutationFn: api.importCustomManifest,
    onSuccess: async (result) => {
      setSelectedId(result.adapter.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["custom-adapters"] }),
        queryClient.invalidateQueries({ queryKey: ["providers"] })
      ]);
    }
  });

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFormError("");
    saveAdapter.reset();
    setEditorOpen(true);
  }

  function openEdit(adapter: GenericAdapter) {
    const provider = providers.find((item) => item.id === adapter.provider_account_id);
    setEditing(adapter);
    setForm(formFromAdapter(adapter, provider));
    setFormError("");
    saveAdapter.reset();
    setEditorOpen(true);
  }

  function submitAdapter(event: FormEvent) {
    event.preventDefault();
    try {
      const auth = buildAuth(form);
      const input: GenericAdapterInput = {
        provider_account_id: editing?.provider_account_id ?? 0,
        credential_reference_id: form.credential_reference_id,
        method: form.method,
        endpoint: form.endpoint.trim(),
        content_type: form.content_type.trim(),
        response_mode: form.response_mode,
        stream_format: form.stream_format,
        security_mode: form.security_mode,
        query: parseObject(form.queryJson, "Query"),
        headers: parseStringObject(form.headersJson, "Headers"),
        request_template: JSON.parse(form.requestTemplateJson) as unknown,
        parameter_mapping: parseStringObject(form.parameterMappingJson, "参数映射"),
        response_mapping: parseObject(form.responseMappingJson, "响应映射"),
        stream_mapping: parseObject(form.streamMappingJson, "流映射"),
        error_mapping: parseObject(form.errorMappingJson, "错误映射"),
        auth,
        capability_defaults: parseStringObject(form.capabilityDefaultsJson, "能力初值"),
        enabled: false
      };
      setFormError("");
      saveAdapter.mutate(input);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "JSON 配置无效");
    }
  }

  async function runStream() {
    if (!selected) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setStreamOutput("");
    setStreamError("");
    try {
      await api.streamCustomAdapter(
        selected.id,
        debugPayload(model, prompt),
        (event) => {
          if (event.event === "delta") setStreamOutput((value) => value + event.text_delta);
          if (event.event === "warning" && event.warning) setStreamError(event.warning);
          if (event.event === "error" && event.error) setStreamError(`${event.error.code}: ${event.error.message}`);
        },
        controller.signal
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setStreamError(error instanceof Error ? error.message : "流式请求失败");
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

  async function exportManifest(adapter: GenericAdapter) {
    const manifest = await api.exportCustomManifest(adapter.id);
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${providerName(adapter, providers)}-adapter.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }

  async function readManifest(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const manifest = JSON.parse(await file.text()) as GenericAdapterManifest;
      importManifest.mutate(manifest);
    } catch {
      importManifest.reset();
      setStreamError("Manifest 不是有效 JSON。 ");
    }
  }

  const requestPreview = testResult?.redacted_request ?? null;
  const responsePreview = testResult?.response ?? null;

  return (
    <section className="page-stack custom-api-page">
      <header className="page-header">
        <div><span className="eyebrow">安全接入</span><h1>自定义 HTTP API</h1></div>
        <div className="page-actions">
          <input ref={importRef} hidden type="file" accept="application/json,.json" onChange={readManifest} />
          <button className="secondary-button" type="button" onClick={() => importRef.current?.click()}><Upload size={17} />导入 Manifest</button>
          <button className="secondary-button" type="button" onClick={() => { saveCredential.reset(); setEditingCredential(null); setCredentialForm({ name: "", env_var_name: "" }); setCredentialOpen(true); }}><KeyRound size={17} />凭据引用</button>
          <button className="primary-button" type="button" onClick={openCreate}><Plus size={17} />新建适配器</button>
        </div>
      </header>
      {adaptersQuery.error ? <ErrorNotice message="无法读取自定义适配器。" /> : null}
      {importManifest.error ? <ErrorNotice message="Manifest 导入失败；请检查 Schema 与密钥扫描结果。" /> : null}

      <div className="custom-api-layout">
        <aside className="adapter-browser">
          <header><strong>适配器</strong><span>{adapters.length}</span></header>
          <div className="adapter-list">
            {adapters.length === 0 ? <EmptyState icon={ServerCog} title="暂无适配器" description="创建配置或导入 Manifest。" /> : adapters.map((adapter) => (
              <button key={adapter.id} type="button" className={selected?.id === adapter.id ? "selected" : ""} onClick={() => { setSelectedId(adapter.id); setTestResult(null); setStreamOutput(""); }}>
                <strong>{providerName(adapter, providers)}</strong>
                <span>{adapter.method} {adapter.endpoint}</span>
                <small>{adapter.enabled ? "已启用" : adapter.test_current ? "已测试" : "待测试"}</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="custom-api-workbench">
          {!selected ? (
            <EmptyState icon={Braces} title="选择一个适配器" description="配置、审批和调试结果会显示在这里。" />
          ) : (
            <>
              <header className="adapter-detail-header">
                <div>
                  <span className="eyebrow">{selected.security_mode === "local_private" ? "本地网络" : "公共网络"}</span>
                  <h2>{selectedProvider?.name ?? `Provider #${selected.provider_account_id}`}</h2>
                  <p>{selectedProvider?.base_url}{selected.endpoint}</p>
                </div>
                <div className="adapter-toolbar">
                  {selected.security_mode === "local_private" ? <button className="secondary-button compact" type="button" title={`审批精确 Origin：${targetOrigin}`} onClick={() => approve.mutate(selected)} disabled={approve.isPending}><ShieldCheck size={15} />{selected.approval_current ? "重新审批" : "审批 Origin"}</button> : null}
                  <button className="icon-button ghost" type="button" title="编辑适配器" onClick={() => openEdit(selected)}><Pencil size={16} /></button>
                  <button className="icon-button ghost" type="button" title="导出 Manifest" onClick={() => exportManifest(selected)}><Download size={16} /></button>
                  <button className="icon-button ghost danger-ink" type="button" title="删除适配器" onClick={() => { if (window.confirm("删除这个自定义适配器？")) removeAdapter.mutate(selected); }}><Trash2 size={16} /></button>
                </div>
              </header>
              <div className="adapter-state-strip">
                <StateItem ok={selected.security_mode === "public_only" || selected.approval_current} label="Origin" detail={selected.security_mode === "public_only" ? "公共网络" : targetOrigin} />
                <StateItem ok={selected.test_current} label="测试" />
                <StateItem ok={selected.enabled && Boolean(selectedProvider?.enabled)} label="启用" />
                <div><span>认证</span><strong>{selected.auth.type}</strong></div>
                <div><span>凭据</span><strong>{selected.credential_reference_name ?? "无"}</strong></div>
              </div>
              <section className="custom-debugger">
                <div className="custom-debug-input">
                  <div className="form-row">
                    <FormField label="模型"><input value={model} onChange={(event) => setModel(event.target.value)} /></FormField>
                    <FormField label="流格式"><input value={selected.stream_format} readOnly /></FormField>
                  </div>
                  <FormField label="用户消息"><textarea rows={7} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></FormField>
                  <div className="debug-actions">
                    <button className="primary-button" type="button" disabled={!prompt.trim() || testAdapter.isPending} onClick={() => testAdapter.mutate(selected)}><Play size={16} />测试普通响应</button>
                    {streaming ? <button className="danger-button" type="button" onClick={stopStream}><Square size={15} />停止</button> : <button className="secondary-button" type="button" disabled={!prompt.trim()} onClick={runStream}><Radio size={16} />测试流式</button>}
                    <button className="secondary-button" type="button" disabled={!selected.test_current || toggleEnabled.isPending} onClick={() => toggleEnabled.mutate(selected)}>{selected.enabled ? "停用" : "启用"}</button>
                  </div>
                  {testAdapter.error ? <ErrorNotice message="测试请求失败。" /> : null}
                  {toggleEnabled.error ? <ErrorNotice message="启用失败：需先审批当前 Origin 并完成最新测试。" /> : null}
                </div>
                <div className="custom-debug-output">
                  <div><span className="eyebrow">脱敏请求</span><pre>{requestPreview ? JSON.stringify(requestPreview, null, 2) : "尚未测试"}</pre></div>
                  <div><span className="eyebrow">标准化结果</span><pre>{streamOutput || (responsePreview ? JSON.stringify(responsePreview, null, 2) : "尚无结果")}</pre></div>
                </div>
              </section>
              {(testResult?.error || streamError) ? <div className="custom-error"><CircleAlert size={16} />{streamError || `${testResult?.error?.code}: ${testResult?.error?.message}`}</div> : null}
            </>
          )}
        </main>
      </div>

      <Dialog open={editorOpen} title={editing ? "编辑自定义适配器" : "新建自定义适配器"} width="large" onClose={() => setEditorOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setEditorOpen(false)}>取消</button><button className="primary-button" type="submit" form="custom-adapter-form" disabled={!form.providerName.trim() || !form.baseUrl.trim() || saveAdapter.isPending}><Save size={17} />保存配置</button></>}>
        <form id="custom-adapter-form" className="form-grid" onSubmit={submitAdapter}>
          {formError ? <ErrorNotice message={formError} /> : null}
          {saveAdapter.error ? <ErrorNotice message="配置保存失败；请检查 URL、环境变量引用和 JSON 映射。" /> : null}
          <div className="form-row">
            <FormField label="Provider 名称"><input value={form.providerName} onChange={(event) => setForm({ ...form, providerName: event.target.value })} /></FormField>
            <FormField label="Base URL"><input value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} placeholder="https://api.example.com/v1" /></FormField>
          </div>
          <div className="form-row three-columns">
            <FormField label="方法"><select value={form.method} onChange={(event) => setForm({ ...form, method: event.target.value as AdapterForm["method"] })}><option>POST</option><option>GET</option></select></FormField>
            <FormField label="Endpoint"><input value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} /></FormField>
            <FormField label="网络模式"><select value={form.security_mode} onChange={(event) => setForm({ ...form, security_mode: event.target.value as AdapterForm["security_mode"] })}><option value="public_only">公共网络</option><option value="local_private">本地私有网络</option></select></FormField>
          </div>
          <div className="form-row three-columns">
            <FormField label="响应模式"><select value={form.response_mode} onChange={(event) => setForm({ ...form, response_mode: event.target.value as AdapterForm["response_mode"] })}><option value="json">JSON</option><option value="raw_text">Raw Text</option></select></FormField>
            <FormField label="流格式"><select value={form.stream_format} onChange={(event) => setForm({ ...form, stream_format: event.target.value as AdapterForm["stream_format"] })}><option value="sse">SSE</option><option value="ndjson">NDJSON</option><option value="chunked_json">Chunked JSON</option><option value="raw_text">Raw Text</option></select></FormField>
            <FormField label="Content-Type"><input value={form.content_type} onChange={(event) => setForm({ ...form, content_type: event.target.value })} /></FormField>
          </div>
          <div className="form-row">
            <FormField label="认证"><select value={form.authType} onChange={(event) => setForm({ ...form, authType: event.target.value as GenericAuth["type"] })}><option value="none">None</option><option value="bearer">Bearer</option><option value="api_key_header">API Key Header</option><option value="custom_header">自定义 Header</option><option value="query">Query 参数</option><option value="basic">Basic Auth</option></select></FormField>
            <FormField label="凭据引用"><select value={form.credential_reference_id ?? ""} onChange={(event) => setForm({ ...form, credential_reference_id: event.target.value ? Number(event.target.value) : null })}><option value="">无</option>{credentials.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.env_var_name}</option>)}</select></FormField>
          </div>
          {form.authType !== "none" && form.authType !== "bearer" ? <div className="form-row"><FormField label={form.authType === "query" ? "Query 参数名" : form.authType === "basic" ? "用户名" : "Header 名称"}><input value={form.authType === "basic" ? form.authUsername : form.authName} onChange={(event) => form.authType === "basic" ? setForm({ ...form, authUsername: event.target.value }) : setForm({ ...form, authName: event.target.value })} /></FormField><FormField label="前缀"><input value={form.authPrefix} onChange={(event) => setForm({ ...form, authPrefix: event.target.value })} /></FormField></div> : null}
          <details className="advanced-config" open>
            <summary><FileJson size={16} />请求与响应映射</summary>
            <div className="form-grid">
              <FormField label="请求模板 JSON"><textarea rows={8} spellCheck={false} value={form.requestTemplateJson} onChange={(event) => setForm({ ...form, requestTemplateJson: event.target.value })} /></FormField>
              <div className="form-row"><FormField label="Query JSON"><textarea rows={5} spellCheck={false} value={form.queryJson} onChange={(event) => setForm({ ...form, queryJson: event.target.value })} /></FormField><FormField label="Headers JSON"><textarea rows={5} spellCheck={false} value={form.headersJson} onChange={(event) => setForm({ ...form, headersJson: event.target.value })} /></FormField></div>
              <FormField label="参数映射 JSON"><textarea rows={5} spellCheck={false} value={form.parameterMappingJson} onChange={(event) => setForm({ ...form, parameterMappingJson: event.target.value })} /></FormField>
              <FormField label="响应映射 JSON"><textarea rows={8} spellCheck={false} value={form.responseMappingJson} onChange={(event) => setForm({ ...form, responseMappingJson: event.target.value })} /></FormField>
              <FormField label="流映射 JSON"><textarea rows={8} spellCheck={false} value={form.streamMappingJson} onChange={(event) => setForm({ ...form, streamMappingJson: event.target.value })} /></FormField>
              <div className="form-row"><FormField label="错误映射 JSON"><textarea rows={6} spellCheck={false} value={form.errorMappingJson} onChange={(event) => setForm({ ...form, errorMappingJson: event.target.value })} /></FormField><FormField label="能力初值 JSON"><textarea rows={6} spellCheck={false} value={form.capabilityDefaultsJson} onChange={(event) => setForm({ ...form, capabilityDefaultsJson: event.target.value })} /></FormField></div>
            </div>
          </details>
        </form>
      </Dialog>

      <Dialog open={credentialOpen} title="凭据引用" onClose={() => setCredentialOpen(false)} footer={<button className="secondary-button" type="button" onClick={() => setCredentialOpen(false)}>关闭</button>}>
        <form className="inline-credential-form" onSubmit={(event) => { event.preventDefault(); saveCredential.mutate(); }}>
          <FormField label="名称"><input value={credentialForm.name} onChange={(event) => setCredentialForm({ ...credentialForm, name: event.target.value })} /></FormField>
          <FormField label="环境变量名"><input value={credentialForm.env_var_name} onChange={(event) => setCredentialForm({ ...credentialForm, env_var_name: event.target.value.toUpperCase() })} placeholder="CUSTOM_API_KEY" /></FormField>
          <div className="credential-form-actions">
            {editingCredential ? <button className="secondary-button" type="button" onClick={() => { saveCredential.reset(); setEditingCredential(null); setCredentialForm({ name: "", env_var_name: "" }); }}>取消</button> : null}
            <button className="primary-button" type="submit" disabled={!credentialForm.name.trim() || !credentialForm.env_var_name.trim() || saveCredential.isPending}>{editingCredential ? <Save size={16} /> : <Plus size={16} />}{editingCredential ? "更新" : "添加"}</button>
          </div>
        </form>
        {saveCredential.error ? <ErrorNotice message="凭据引用保存失败。" /> : null}
        {removeCredential.error ? <ErrorNotice message="正在被适配器使用的凭据引用不能删除。" /> : null}
        <div className="credential-list">
          {credentials.map((item) => <div key={item.id}><KeyRound size={16} /><strong>{item.name}</strong><span>{item.env_var_name}</span><button className="icon-button ghost" type="button" title="编辑凭据引用" onClick={() => { saveCredential.reset(); setEditingCredential(item); setCredentialForm({ name: item.name, env_var_name: item.env_var_name }); }}><Pencil size={15} /></button><button className="icon-button ghost danger-ink" type="button" title="删除凭据引用" onClick={() => removeCredential.mutate(item)}><Trash2 size={15} /></button></div>)}
        </div>
      </Dialog>
    </section>
  );
}

function StateItem({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  const value = detail || (ok ? "就绪" : "待处理");
  return <div className={ok ? "state-ok" : "state-pending"}>{ok ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}<span>{label}</span><strong title={value}>{value}</strong></div>;
}

function debugPayload(model: string, prompt: string): ModelDebugRequest {
  return {
    provider_account_id: null,
    model,
    response_format: "text",
    messages: [{ role: "user", content: [{ type: "text", text: prompt }] }]
  };
}

function formFromAdapter(adapter: GenericAdapter, provider?: Provider): AdapterForm {
  const authName = adapter.auth.type === "query" ? adapter.auth.query_name ?? "" : adapter.auth.header_name ?? "";
  return {
    providerName: provider?.name ?? "",
    baseUrl: provider?.base_url ?? "",
    credential_reference_id: adapter.credential_reference_id,
    method: adapter.method,
    endpoint: adapter.endpoint,
    content_type: adapter.content_type,
    response_mode: adapter.response_mode,
    stream_format: adapter.stream_format,
    security_mode: adapter.security_mode,
    authType: adapter.auth.type,
    authName,
    authUsername: adapter.auth.username ?? "",
    authPrefix: adapter.auth.prefix ?? "",
    queryJson: pretty(adapter.query),
    headersJson: pretty(adapter.headers),
    requestTemplateJson: pretty(adapter.request_template),
    parameterMappingJson: pretty(adapter.parameter_mapping),
    responseMappingJson: pretty(adapter.response_mapping),
    streamMappingJson: pretty(adapter.stream_mapping),
    errorMappingJson: pretty(adapter.error_mapping),
    capabilityDefaultsJson: pretty(adapter.capability_defaults)
  };
}

function buildAuth(form: AdapterForm): GenericAuth {
  const auth: GenericAuth = { type: form.authType, prefix: form.authPrefix };
  if (form.authType === "query") auth.query_name = form.authName;
  if (form.authType === "api_key_header" || form.authType === "custom_header") auth.header_name = form.authName;
  if (form.authType === "basic") auth.username = form.authUsername;
  return auth;
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label}必须是 JSON 对象`);
  return parsed as Record<string, unknown>;
}

function parseStringObject(value: string, label: string): Record<string, string> {
  const parsed = parseObject(value, label);
  if (Object.values(parsed).some((item) => typeof item !== "string")) throw new Error(`${label}的值必须都是字符串`);
  return parsed as Record<string, string>;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function providerName(adapter: GenericAdapter, providers: Provider[]): string {
  return providers.find((provider) => provider.id === adapter.provider_account_id)?.name ?? `Provider #${adapter.provider_account_id}`;
}

function displayOrigin(baseUrl?: string | null): string {
  if (!baseUrl) return "未配置";
  try {
    return new URL(baseUrl).origin;
  } catch {
    return "无效 Origin";
  }
}
