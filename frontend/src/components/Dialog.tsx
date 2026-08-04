import { ReactNode, useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { dialogBackdrop, dialogCard } from "../utils/motion";

type DialogProps = {
  open: boolean;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: "small" | "medium" | "large";
  onClose: () => void;
};

export function Dialog({
  open,
  title,
  description,
  children,
  footer,
  width = "medium",
  onClose
}: DialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div className="dialog-backdrop" role="presentation" onMouseDown={onClose} {...dialogBackdrop}>
          <motion.section
            className={`dialog dialog-${width}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
            {...dialogCard}
          >
            <header className="dialog-header">
              <div>
                <h2 id="dialog-title">{title}</h2>
                {description ? <p>{description}</p> : null}
              </div>
              <button className="icon-button ghost" type="button" onClick={onClose} title="关闭">
                <X size={18} />
              </button>
            </header>
            <div className="dialog-body">{children}</div>
            {footer ? <footer className="dialog-footer">{footer}</footer> : null}
          </motion.section>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

