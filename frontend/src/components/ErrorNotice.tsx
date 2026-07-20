import { AlertCircle, X } from "lucide-react";

export function ErrorNotice({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div className="error-notice" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {onDismiss ? (
        <button className="icon-button ghost" type="button" onClick={onDismiss} title="关闭错误提示">
          <X size={16} />
        </button>
      ) : null}
    </div>
  );
}
