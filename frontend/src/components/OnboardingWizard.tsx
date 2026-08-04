import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { studioApi } from "../api/studio";
import { dialogBackdrop, dialogCard } from "../utils/motion";
import { ProjectDialogForm } from "./ProjectDialogForm";
import { ProviderDialogForm } from "./ProviderDialogForm";

const stepMotion = {
  initial: { opacity: 0, x: 20 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -20 },
  transition: { duration: 0.18 }
} as const;

export function OnboardingWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(1);
  const { data: providers = [] } = useQuery({
    queryKey: ["studio-providers"],
    queryFn: studioApi.providers
  });
  const effectiveStep = providers.length > 0 && step === 2 ? 3 : step;

  function handleFinish() {
    localStorage.setItem("onboarding-done", "true");
    onComplete();
  }

  return (
    <motion.div
      className="onboarding-overlay"
      {...dialogBackdrop}
      role="presentation"
    >
      <motion.section
        className="wizard-card"
        {...dialogCard}
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
      >
        <div className="wizard-progress" aria-label="首启配置进度">
          <div className={effectiveStep >= 1 ? "active" : ""}>1. 欢迎</div>
          <div className={effectiveStep >= 2 ? "active" : ""}>2. 配置模型</div>
          <div className={effectiveStep >= 3 ? "active" : ""}>3. 新建项目</div>
        </div>

        <AnimatePresence mode="wait">
          {effectiveStep === 1 ? (
            <motion.div key="welcome" {...stepMotion}>
              <h2 id="onboarding-title">欢迎使用 Novel Agent Studio</h2>
              <p>开始前需要完成两步配置：连接一个 AI 模型服务，并创建第一本小说。</p>
              <button className="primary-button" type="button" onClick={() => setStep(2)}>
                开始配置
              </button>
            </motion.div>
          ) : null}
          {effectiveStep === 2 ? (
            <motion.div key="provider" {...stepMotion}>
              <h2 id="onboarding-title">配置第一个模型服务</h2>
              <p>完整填写服务地址、模型与安全凭据，创建后即可进入项目设置。</p>
              <ProviderDialogForm onSuccess={() => setStep(3)} onCancel={null} />
            </motion.div>
          ) : null}
          {effectiveStep === 3 ? (
            <motion.div key="project" {...stepMotion}>
              <h2 id="onboarding-title">创建第一本小说</h2>
              <p>填写书名和题材创意；完成后将在项目首页看到新项目。</p>
              <ProjectDialogForm onSuccess={handleFinish} onCancel={null} />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </motion.section>
    </motion.div>
  );
}
