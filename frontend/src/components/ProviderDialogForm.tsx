import { FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, PlugZap } from "lucide-react";
import { StudioProvider, studioApi } from "../api/studio";

export type PresetKey =
  | "deepseek"
  | "openai"
  | "anthropic"
  | "gemini"
  | "xai"
  | "openrouter"
  | "openai_compatible";

export const providerPresets: Record<
  PresetKey,
  { label: string; baseUrl: string; model: string; env: string }
> = {
  deepseek: {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-v4-flash",
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
  const item = providerPresets[preset];
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

export function ProviderDialogForm({
  onSuccess,
  onCancel
}: {
  onSuccess: (provider: StudioProvider) => void;
  onCancel: (() => void) | null;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(initialForm());
  const [error, setError] = useState("");

  const create = useMutation({
    mutationFn: () =>
      studioApi.setupProvider({
        preset: form.preset,
        name: form.name,
        base_url: form.base_url,
        model: form.model,
        api_key: form.use_env ? null : form.api_key,
        env_var_name: form.use_env ? form.env_var_name : null
      }),
    onSuccess: async (provider) => {
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["studio-providers"] });
      onSuccess(provider);
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

  return (
    <form className="provider-dialog-form" onSubmit={submit}>
      <div className="preset-grid">
        {(Object.keys(providerPresets) as PresetKey[]).map((key) => (
          <button
            key={key}
            type="button"
            className={form.preset === key ? "selected" : ""}
            onClick={() => choosePreset(key)}
          >
            {providerPresets[key].label}
          </button>
        ))}
      </div>
      <div className="form-grid two-columns">
        <label>
          <span>显示名称</span>
          <input
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </label>
        <label>
          <span>模型名称</span>
          {form.preset === "deepseek" ? (
            <select
              required
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
            >
              <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
              <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
            </select>
          ) : (
            <input
              required
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
              placeholder="输入供应商提供的模型 ID"
            />
          )}
        </label>
        <label className="wide">
          <span>API 地址</span>
          <input
            required
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
          />
        </label>
      </div>
      <div className="segmented-control compact">
        <button
          type="button"
          className={!form.use_env ? "active" : ""}
          onClick={() => setForm({ ...form, use_env: false })}
        >
          保存 API Key
        </button>
        <button
          type="button"
          className={form.use_env ? "active" : ""}
          onClick={() => setForm({ ...form, use_env: true })}
        >
          读取环境变量
        </button>
      </div>
      {form.use_env ? (
        <label>
          <span>环境变量名</span>
          <input
            required
            value={form.env_var_name}
            onChange={(event) =>
              setForm({ ...form, env_var_name: event.target.value.toUpperCase() })
            }
          />
        </label>
      ) : (
        <label>
          <span>API Key</span>
          <input
            required
            type="password"
            autoComplete="new-password"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            placeholder="sk-..."
          />
        </label>
      )}
      {error ? <div className="form-error" role="alert">{error}</div> : null}
      <footer>
        {onCancel ? (
          <button type="button" className="secondary-button" onClick={onCancel}>
            取消
          </button>
        ) : null}
        <button type="submit" className="primary-button" disabled={create.isPending}>
          {create.isPending ? <LoaderCircle className="spin" size={16} /> : <PlugZap size={16} />}
          保存服务
        </button>
      </footer>
    </form>
  );
}
