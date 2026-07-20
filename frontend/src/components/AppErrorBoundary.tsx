import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, Home, RefreshCw } from "lucide-react";

type State = { error: Error | null };

export class AppErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Application render failure", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="fatal-error" role="alert">
        <AlertTriangle size={34} />
        <h1>页面遇到错误</h1>
        <p>{this.state.error.message || "界面无法继续渲染，本地数据没有因此被修改。"}</p>
        <div>
          <button className="primary-button" type="button" onClick={() => window.location.reload()}>
            <RefreshCw size={16} />重新载入
          </button>
          <a className="secondary-button" href="/">
            <Home size={16} />返回项目首页
          </a>
        </div>
      </main>
    );
  }
}
