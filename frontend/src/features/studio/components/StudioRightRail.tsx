import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  CheckCircle2,
  CircleDollarSign,
  FileCheck2,
  LibraryBig,
  LoaderCircle,
  MessageSquareText,
  Send
} from "lucide-react";

import type { Artifact, StudioOverview } from "../../../api/studio";

export type StudioRightTab = "chat" | "reviews" | "progress" | "library" | "cost";

type Props = {
  overview: StudioOverview;
  activeTab: StudioRightTab;
  onTabChange: (tab: StudioRightTab) => void;
  chatValue: string;
  onChatChange: (value: string) => void;
  onChatSend: () => void;
  chatSending: boolean;
  onProposal: (messageId: number, action: "apply" | "reject") => void;
  pending: Artifact[];
  approving: boolean;
  onReviewOpen: (item: Artifact) => void;
  onReviewApprove: (item: Artifact) => void;
  onBudgetUpdate: (value: Record<string, unknown>) => void;
};

export function StudioRightRail({
  overview,
  activeTab,
  onTabChange,
  chatValue,
  onChatChange,
  onChatSend,
  chatSending,
  onProposal,
  pending,
  approving,
  onReviewOpen,
  onReviewApprove,
  onBudgetUpdate
}: Props) {
  return (
    <>
      <div className="rail-tabs">
        <RailTab icon={MessageSquareText} label="对话" active={activeTab === "chat"} onClick={() => onTabChange("chat")} />
        <RailTab icon={FileCheck2} label="审核" count={pending.length} active={activeTab === "reviews"} onClick={() => onTabChange("reviews")} />
        <RailTab icon={Activity} label="进度" active={activeTab === "progress"} onClick={() => onTabChange("progress")} />
        <RailTab icon={LibraryBig} label="资料" active={activeTab === "library"} onClick={() => onTabChange("library")} />
        <RailTab icon={CircleDollarSign} label="费用" active={activeTab === "cost"} onClick={() => onTabChange("cost")} />
      </div>
      {activeTab === "chat" ? (
        <ChatPanel
          overview={overview}
          value={chatValue}
          onChange={onChatChange}
          onSend={onChatSend}
          sending={chatSending}
          onProposal={onProposal}
        />
      ) : null}
      {activeTab === "reviews" ? (
        <ReviewPanel
          items={pending}
          approving={approving}
          onOpen={onReviewOpen}
          onApprove={onReviewApprove}
        />
      ) : null}
      {activeTab === "progress" ? <ProgressPanel overview={overview} /> : null}
      {activeTab === "library" ? <LibraryPanel overview={overview} /> : null}
      {activeTab === "cost" ? <CostPanel overview={overview} onUpdate={onBudgetUpdate} /> : null}
    </>
  );
}

function RailTab({ icon: Icon, label, count, active, onClick }: { icon: typeof Bot; label: string; count?: number; active: boolean; onClick: () => void }) {
  return <button type="button" className={active ? "active" : ""} onClick={onClick} title={label}><Icon size={15} /><span>{label}</span>{count ? <b>{count}</b> : null}</button>;
}

function ChatPanel({ overview, value, onChange, onSend, sending, onProposal }: { overview: StudioOverview; value: string; onChange: (value: string) => void; onSend: () => void; sending: boolean; onProposal: (messageId: number, action: "apply" | "reject") => void }) {
  const streamRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const stream = streamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [overview.messages.length]);
  return <div className="rail-panel chat-panel">
    <header><div><Bot size={16} /><strong>总编对话</strong></div><span>自动上下文</span></header>
    <div ref={streamRef} className="chat-stream" role="log" aria-label="对话消息" aria-live="polite">
      {overview.messages.length === 0 ? <div className="chat-empty"><MessageSquareText size={22} /><span>开始对话</span></div> : null}
      {overview.messages.map((message) => <div key={message.id} className={`chat-message ${message.role}`}>
        <div>{message.content}</div>
        {message.role === "assistant" ? <small>{message.model_name} · {message.context_scope}</small> : null}
        {message.proposal_status === "pending" ? <div className="proposal-actions"><span>{message.proposal?.target_type === "workflow" ? `工作流操作待确认：${message.proposal.label ?? "推进下一步"}` : "修改提案待确认"}</span><button onClick={() => onProposal(message.id, "reject")}>拒绝</button><button className="approve" onClick={() => onProposal(message.id, "apply")}>{message.proposal?.target_type === "workflow" ? "执行" : "应用"}</button></div> : null}
      </div>)}
    </div>
    <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); if (value.trim()) onSend(); }}>
      <textarea rows={3} value={value} onChange={(event) => onChange(event.target.value)} placeholder="询问、分析或提出修改要求" />
      <div><span>项目 · 阶段 · 章节 · 选区</span><button type="submit" className="send-button" disabled={sending || !value.trim()} title="发送">{sending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button></div>
    </form>
  </div>;
}

function ReviewPanel({ items, approving, onOpen, onApprove }: { items: Artifact[]; approving: boolean; onOpen: (item: Artifact) => void; onApprove: (item: Artifact) => void }) {
  return <div className="rail-panel"><header><div><FileCheck2 size={16} /><strong>待审核</strong></div><span>{items.length} 项</span></header><div className="rail-list">
    {items.length === 0 ? <div className="rail-empty"><CheckCircle2 size={22} /><span>没有待审核内容</span></div> : null}
    {items.map((item) => { const writesManuscript = ["drafting", "revision_proposal", "scene_draft"].includes(item.kind); const approveLabel = writesManuscript ? "通过并写入正文" : "通过"; return <article key={item.id} className="review-item"><button type="button" onClick={() => onOpen(item)}><span>{artifactKindLabel(item.kind)}</span><strong>{item.title}</strong><small>版本 {item.version_number} · 点击查看和编辑</small></button><button className="approve-button review-approve" title={approveLabel} aria-label={approveLabel} disabled={approving} onClick={() => onApprove(item)}><Check size={14} /><span>{approveLabel}</span></button></article>; })}
  </div></div>;
}

function ProgressPanel({ overview }: { overview: StudioOverview }) {
  return <div className="rail-panel"><header><div><Activity size={16} /><strong>执行进度</strong></div><span>{overview.jobs.length} 条</span></header><div className="rail-list">
    {overview.jobs.length === 0 ? <div className="rail-empty"><Activity size={22} /><span>暂无任务</span></div> : null}
    {overview.jobs.map((job) => <article key={job.id} className="job-item"><div><span className={`job-dot ${job.status}`} /><strong>{job.label}</strong><small>{job.model_name}</small></div><div className="progress-track"><i style={{ width: `${job.progress}%` }} /></div><footer><span>{job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : `${job.progress}%`}</span><small title={job.model_reason}>{job.model_reason}</small></footer></article>)}
  </div></div>;
}

function LibraryPanel({ overview }: { overview: StudioOverview }) {
  const rows = [["人物与实体", overview.library_counts.entities], ["时间线事件", overview.library_counts.timeline], ["伏笔", overview.library_counts.foreshadows], ["文风规则", overview.library_counts.style_guides]];
  return <div className="rail-panel"><header><div><LibraryBig size={16} /><strong>资料库</strong></div><span>自动更新</span></header><div className="library-metrics">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><div className="memory-state"><CheckCircle2 size={15} /><span>记忆模式：{overview.state.memory_mode === "automatic" ? "自动更新" : "确认后更新"}</span></div></div>;
}

function CostPanel({ overview, onUpdate }: { overview: StudioOverview; onUpdate: (value: Record<string, unknown>) => void }) {
  const [budget, setBudget] = useState(overview.state.budget_limit?.toString() ?? "");
  return <div className="rail-panel"><header><div><CircleDollarSign size={16} /><strong>费用</strong></div><span>{overview.usage.currency}</span></header><div className="cost-summary"><strong>{overview.usage.spent.toFixed(4)}</strong><span>/ {overview.usage.limit?.toFixed(2) ?? "未设置"}</span><div className={overview.usage.warning ? "budget-bar warning" : "budget-bar"}><i style={{ width: `${Math.min(100, overview.usage.percent)}%` }} /></div><small>{overview.usage.tokens.toLocaleString()} tokens · {overview.usage.invocations} 次调用</small></div><label className="budget-input"><span>项目预算</span><div><input type="number" min="0.01" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /><button onClick={() => onUpdate({ budget_limit: budget ? Number(budget) : null, budget_paused: false })}>保存</button></div></label><div className="budget-rules"><span>70% 提醒</span><span>110% 任务结束后暂停</span></div>{overview.usage.paused ? <button className="primary-button full" onClick={() => onUpdate({ budget_paused: false })}>确认继续生成</button> : null}</div>;
}

function artifactKindLabel(kind: string) {
  return ({ drafting: "章节正文", revision_proposal: "正文修改方案", scene_draft: "场景正文", world: "世界观", characters: "人物关系", plot: "剧情伏笔", volumes: "分卷大纲", chapters: "章节大纲", continuation_original: "原始只读副本", continuation_analysis: "原文资料分析", continuation_outline: "反向补建大纲", continuation_plan: "续写规划", continuation_direction: "作者续写方向", review: "全文审阅" } as Record<string, string>)[kind] ?? kind;
}
