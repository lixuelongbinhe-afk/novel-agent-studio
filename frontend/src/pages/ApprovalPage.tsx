import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileClock,
  FileDiff,
  History,
  ListChecks,
  Merge,
  MessageSquareText,
  Pencil,
  RefreshCw,
  Save,
  ShieldCheck,
  XCircle
} from "lucide-react";
import {
  ApiError,
  api,
  type ApprovalRequest,
  type ApprovalStatus,
  type ChangeDecision,
  type ProposedChangeItem,
  type ProposedChangeSet,
  type WritebackAudit
} from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { ManuscriptEditor } from "../components/ManuscriptEditor";
import { useUiStore } from "../stores/ui";

type ApprovalTab = "requests" | "changes" | "audits";
type RequestFilter = "all" | "pending" | "history";

export function ApprovalPage() {
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const [tab, setTab] = useState<ApprovalTab>("requests");
  const [selectedApprovalId, setSelectedApprovalId] = useState<number | null>(null);
  const [selectedChangeSetId, setSelectedChangeSetId] = useState<number | null>(null);
  const [selectedAuditId, setSelectedAuditId] = useState<number | null>(null);
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const projectId = selectedProjectId ?? projectsQuery.data?.[0]?.id;
  const approvalsQuery = useQuery({
    queryKey: ["approval-requests", projectId],
    queryFn: () => api.listApprovalRequests(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 1_000
  });
  const changeSetsQuery = useQuery({
    queryKey: ["change-sets", projectId],
    queryFn: () => api.listChangeSets(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 1_500
  });
  const auditsQuery = useQuery({
    queryKey: ["writeback-audits", projectId],
    queryFn: () => api.listWritebackAudits(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: tab === "audits" ? 2_000 : false
  });
  const approvals = approvalsQuery.data ?? [];
  const changeSets = changeSetsQuery.data ?? [];
  const audits = auditsQuery.data ?? [];
  const pendingCount = approvals.filter((item) => item.status === "pending").length;
  const conflictCount = changeSets.filter((item) => item.status === "conflicted" || item.live_conflicts.length > 0).length;

  useEffect(() => {
    if (selectedApprovalId === null || !approvals.some((item) => item.id === selectedApprovalId)) {
      setSelectedApprovalId(approvals.find((item) => item.status === "pending")?.id ?? approvals[0]?.id ?? null);
    }
  }, [approvals, selectedApprovalId]);
  useEffect(() => {
    if (selectedChangeSetId === null || !changeSets.some((item) => item.id === selectedChangeSetId)) {
      setSelectedChangeSetId(changeSets.find((item) => item.status === "conflicted")?.id ?? changeSets[0]?.id ?? null);
    }
  }, [changeSets, selectedChangeSetId]);
  useEffect(() => {
    if (selectedAuditId === null || !audits.some((item) => item.id === selectedAuditId)) {
      setSelectedAuditId(audits[0]?.id ?? null);
    }
  }, [audits, selectedAuditId]);

  if (!projectId && !projectsQuery.isLoading) {
    return <section className="page-stack approval-page"><EmptyState icon={ShieldCheck} title="还没有项目" description="先创建小说项目，再从工作流发起人工审批。" /></section>;
  }

  return (
    <section className="page-stack approval-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Human control & writeback</span>
          <h1>审批与写回</h1>
          <p>所有正文和小说状态变更都在这里确认；未批准、过期或冲突的快照不会写入项目。</p>
        </div>
        <div className="approval-head-stats" aria-label="审批概览">
          <span><Clock3 size={15} /><strong>{pendingCount}</strong> 待处理</span>
          <span className={conflictCount ? "warn" : ""}><AlertTriangle size={15} /><strong>{conflictCount}</strong> 冲突</span>
          <span><History size={15} /><strong>{audits.length}</strong> 已写回</span>
        </div>
      </header>

      <nav className="segmented-tabs" aria-label="审批中心视图">
        <button className={tab === "requests" ? "active" : ""} type="button" onClick={() => setTab("requests")}><ShieldCheck size={16} />审批请求{pendingCount ? <b>{pendingCount}</b> : null}</button>
        <button className={tab === "changes" ? "active" : ""} type="button" onClick={() => setTab("changes")}><ListChecks size={16} />变更预览{conflictCount ? <b className="warn">{conflictCount}</b> : null}</button>
        <button className={tab === "audits" ? "active" : ""} type="button" onClick={() => setTab("audits")}><FileClock size={16} />写回审计</button>
      </nav>

      {approvalsQuery.error || changeSetsQuery.error || auditsQuery.error ? <ErrorNotice message={errorMessage(approvalsQuery.error ?? changeSetsQuery.error ?? auditsQuery.error, "审批数据读取失败")} /> : null}
      {tab === "requests" ? (
        <RequestsView
          approvals={approvals}
          loading={approvalsQuery.isLoading}
          selectedId={selectedApprovalId}
          onSelect={setSelectedApprovalId}
          onOpenChangeSet={(id) => { setSelectedChangeSetId(id); setTab("changes"); }}
        />
      ) : tab === "changes" ? (
        <ChangeSetsView
          changeSets={changeSets}
          loading={changeSetsQuery.isLoading}
          selectedId={selectedChangeSetId}
          onSelect={setSelectedChangeSetId}
        />
      ) : (
        <AuditsView audits={audits} loading={auditsQuery.isLoading} selectedId={selectedAuditId} onSelect={setSelectedAuditId} />
      )}
    </section>
  );
}

function RequestsView({ approvals, loading, selectedId, onSelect, onOpenChangeSet }: { approvals: ApprovalRequest[]; loading: boolean; selectedId: number | null; onSelect: (id: number) => void; onOpenChangeSet: (id: number) => void }) {
  const [filter, setFilter] = useState<RequestFilter>("all");
  const visible = approvals.filter((item) => filter === "all" || (filter === "pending" ? item.status === "pending" : item.status !== "pending"));
  const selected = approvals.find((item) => item.id === selectedId) ?? null;
  if (loading) return <div className="route-loading">正在读取审批队列...</div>;
  if (!approvals.length) return <EmptyState icon={ShieldCheck} title="暂无审批请求" description="工作流执行到 Human Approval 节点后，请求会自动出现在这里。" />;
  return (
    <div className="approval-workbench">
      <aside className="approval-queue">
        <header><div><strong>审批队列</strong><span>{visible.length} 条</span></div><div className="mini-segments">{(["all", "pending", "history"] as RequestFilter[]).map((value) => <button key={value} type="button" className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{({ all: "全部", pending: "待办", history: "历史" } as const)[value]}</button>)}</div></header>
        <div className="approval-queue-list">
          {visible.map((item) => <button type="button" className={item.id === selectedId ? "selected" : ""} key={item.id} onClick={() => onSelect(item.id)}><span className={`approval-type-icon type-${item.approval_type}`}>{item.approval_type === "prose" ? <FileDiff size={16} /> : item.approval_type === "change_set" ? <ListChecks size={16} /> : <ShieldCheck size={16} />}</span><span><strong>{item.title}</strong><small>运行 #{item.workflow_run_id} · 第 {item.round_number} 轮</small><time>{formatDate(item.created_at)}</time></span><ApprovalStatusBadge status={item.status} /><ChevronRight size={15} /></button>)}
        </div>
      </aside>
      {selected ? <ApprovalDetail approval={selected} onOpenChangeSet={onOpenChangeSet} /> : <div className="approval-detail-empty"><ShieldCheck size={26} /><span>选择一条审批查看快照</span></div>}
    </div>
  );
}

function ApprovalDetail({ approval, onOpenChangeSet }: { approval: ApprovalRequest; onOpenChangeSet: (id: number) => void }) {
  const value = approval.snapshot.value;
  const changeSetId = isRecord(value) && typeof value.change_set_id === "number" ? value.change_set_id : null;
  return (
    <section className="approval-detail">
      <header className="approval-detail-head"><div><span>{approvalTypeLabel(approval.approval_type)} · #{approval.id}</span><h2>{approval.title}</h2><p>{approval.instructions || "无附加说明"}</p></div><ApprovalStatusBadge status={approval.status} /></header>
      <div className="snapshot-meta"><span>快照 rev {approval.snapshot_revision}</span><span>审批轮次 {approval.round_number}/3</span><span title={approval.snapshot_hash}>hash {approval.snapshot_hash.slice(0, 12)}</span>{approval.expires_at ? <span>到期 {formatDate(approval.expires_at)}</span> : null}</div>
      <div className="approval-content-scroll">
        {approval.approval_type === "prose" && typeof value === "string" ? <ProseReview before={String(approval.snapshot.source.base_content ?? "")} proposed={value} /> : approval.approval_type === "change_set" && isRecord(value) ? <div className="change-approval-summary"><ListChecks size={24} /><h3>受控变更集 #{String(value.change_set_id ?? "-")}</h3><p>{Array.isArray(value.items) ? value.items.length : 0} 个变更项 · revision {String(value.change_set_revision ?? "-")}</p><code>{String(value.changes_hash ?? "")}</code>{changeSetId ? <button className="secondary-button" type="button" onClick={() => onOpenChangeSet(changeSetId)}><ListChecks size={15} />打开逐项预览</button> : null}</div> : <JsonPreview value={value} />}
        {approval.status !== "pending" ? <DecisionHistory approval={approval} /> : null}
      </div>
      <DecisionPanel approval={approval} />
    </section>
  );
}

function ProseReview({ before, proposed }: { before: string; proposed: string }) {
  const chunks = useMemo(() => diffText(visibleText(before), visibleText(proposed)), [before, proposed]);
  return <div className="prose-review"><div className="review-heading"><div><FileDiff size={17} /><span><strong>正文差异</strong><small>冻结的审批前版本与候选正文</small></span></div><span>{visibleText(proposed).length.toLocaleString()} 字符</span></div><div className="prose-diff" aria-label="正文差异">{chunks.map((chunk, index) => chunk.type === "equal" ? <span key={index}>{chunk.value}</span> : chunk.type === "remove" ? <del key={index}>{chunk.value}</del> : <ins key={index}>{chunk.value}</ins>)}</div><details><summary>候选正文全文</summary><pre>{visibleText(proposed)}</pre></details></div>;
}

function DecisionPanel({ approval }: { approval: ApprovalRequest }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [editedValue, setEditedValue] = useState(typeof approval.snapshot.value === "string" ? approval.snapshot.value : JSON.stringify(approval.snapshot.value, null, 2));
  const [localError, setLocalError] = useState("");
  useEffect(() => { setNote(""); setEditing(false); setEditedValue(typeof approval.snapshot.value === "string" ? approval.snapshot.value : JSON.stringify(approval.snapshot.value, null, 2)); setLocalError(""); }, [approval.id, approval.snapshot.value]);
  const mutation = useMutation({
    mutationFn: (action: "approve" | "request_changes" | "reject" | "edit") => {
      if (action === "request_changes" && !note.trim()) throw new Error("要求修改必须填写具体说明");
      let value: unknown = editedValue;
      if (action === "edit" && approval.approval_type !== "prose") {
        try { value = JSON.parse(editedValue) as unknown; } catch { throw new Error("编辑内容必须是有效 JSON"); }
      }
      return api.decideApprovalRequest(approval, { action, idempotency_key: idempotencyKey(approval.id, action), note, ...(action === "edit" ? { edited_value: value } : {}) });
    },
    onSuccess: async () => {
      setLocalError("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approval-requests", approval.project_id] }),
        queryClient.invalidateQueries({ queryKey: ["workflow-runs", approval.project_id] }),
        queryClient.invalidateQueries({ queryKey: ["change-sets", approval.project_id] })
      ]);
    },
    onError: (error) => setLocalError(errorMessage(error, "审批提交失败"))
  });
  if (approval.status !== "pending") return <footer className="decision-panel resolved"><CheckCircle2 size={17} /><span>该审批已结算，快照不可再次处理。</span></footer>;
  return <footer className="decision-panel">
    {localError ? <ErrorNotice message={localError} onDismiss={() => setLocalError("")} /> : null}
    {editing ? <div className="approval-edit-area">{approval.approval_type === "prose" ? <ManuscriptEditor value={editedValue} placeholder="编辑候选正文" onChange={setEditedValue} onSave={() => mutation.mutate("edit")} /> : <textarea aria-label="编辑审批值" rows={12} value={editedValue} onChange={(event) => setEditedValue(event.target.value)} />}</div> : null}
    <label><span>审批说明</span><textarea aria-label="审批说明" rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="退回或拒绝时填写原因" /></label>
    <div className="decision-actions">
      <button className="primary-button" type="button" disabled={mutation.isPending} onClick={() => mutation.mutate("approve")}><Check size={16} />批准</button>
      <button className="secondary-button" type="button" disabled={mutation.isPending} onClick={() => mutation.mutate("request_changes")}><MessageSquareText size={16} />要求修改</button>
      <button className={`secondary-button ${editing ? "active" : ""}`} type="button" disabled={approval.approval_type === "change_set" || mutation.isPending} onClick={() => setEditing((value) => !value)}><Pencil size={16} />编辑</button>
      {editing ? <button className="secondary-button" type="button" disabled={mutation.isPending} onClick={() => mutation.mutate("edit")}><Save size={16} />提交编辑</button> : null}
      <button className="danger-button" type="button" disabled={mutation.isPending} onClick={() => { if (window.confirm("拒绝后当前工作流将停止，确认继续？")) mutation.mutate("reject"); }}><XCircle size={16} />拒绝</button>
    </div>
  </footer>;
}

function DecisionHistory({ approval }: { approval: ApprovalRequest }) {
  return <div className="decision-history"><History size={17} /><div><strong>{approvalStatusLabel(approval.status)}</strong><p>{approval.decision_note || "未填写说明"}</p><small>{approval.resolved_at ? formatDate(approval.resolved_at) : ""}{approval.superseded_by_id ? ` · 已由 #${approval.superseded_by_id} 替代` : ""}</small></div></div>;
}

function ChangeSetsView({ changeSets, loading, selectedId, onSelect }: { changeSets: ProposedChangeSet[]; loading: boolean; selectedId: number | null; onSelect: (id: number) => void }) {
  const queryClient = useQueryClient();
  const selected = changeSets.find((item) => item.id === selectedId) ?? null;
  const [draftItems, setDraftItems] = useState<ProposedChangeItem[]>([]);
  const [localError, setLocalError] = useState("");
  useEffect(() => { setDraftItems(selected?.items ?? []); setLocalError(""); }, [selected?.changes_hash, selected?.id]);
  const save = useMutation({
    mutationFn: () => api.editChangeSet(selected!, draftItems),
    onSuccess: async () => { setLocalError(""); await Promise.all([queryClient.invalidateQueries({ queryKey: ["change-sets", selected?.project_id] }), queryClient.invalidateQueries({ queryKey: ["approval-requests", selected?.project_id] })]); },
    onError: (error) => setLocalError(errorMessage(error, "变更集保存失败"))
  });
  const resolve = useMutation({
    mutationFn: (action: "rebase_current" | "manual_merge" | "abandon" | "reextract") => api.resolveChangeSet(selected!, action, draftItems),
    onSuccess: async () => { setLocalError(""); await Promise.all([queryClient.invalidateQueries({ queryKey: ["change-sets", selected?.project_id] }), queryClient.invalidateQueries({ queryKey: ["approval-requests", selected?.project_id] })]); },
    onError: (error) => setLocalError(errorMessage(error, "冲突处理失败"))
  });
  if (loading) return <div className="route-loading">正在读取变更集...</div>;
  if (!changeSets.length) return <EmptyState icon={ListChecks} title="暂无变更预览" description="State Extraction 完成后，系统会在写入前生成受控 ChangeSet。" />;
  const dirty = selected ? JSON.stringify(draftItems) !== JSON.stringify(selected.items) : false;
  const readOnly = !selected || ["applied", "cancelled", "superseded"].includes(selected.status);
  return <div className="changes-workbench">
    <aside className="change-set-list">
      <header><strong>ChangeSets</strong><span>{changeSets.length}</span></header>
      <div className="change-set-list-scroll">{changeSets.map((item) => <button type="button" key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => onSelect(item.id)}><span><strong>变更集 #{item.id}</strong><small>运行 #{item.workflow_run_id} · {item.items.length} 项</small></span><ChangeSetStatusBadge item={item} /><ChevronRight size={15} /></button>)}</div>
    </aside>
    {selected ? <section className="change-set-detail">
      <header className="change-set-toolbar"><div><span>ChangeSet #{selected.id}</span><h2>{selected.items.length} 个受控变更</h2><code title={selected.changes_hash}>{selected.changes_hash.slice(0, 16)}</code></div><div>{dirty ? <span className="unsaved-mark">未保存</span> : null}<button className="primary-button" type="button" disabled={!dirty || readOnly || save.isPending} onClick={() => save.mutate()}><Save size={16} />保存逐项决定</button></div></header>
      {localError ? <ErrorNotice message={localError} onDismiss={() => setLocalError("")} /> : null}
      {selected.conflicts.length || selected.live_conflicts.length ? <div className="conflict-console"><header><AlertTriangle size={17} /><strong>需要处理的冲突</strong><span>{selected.conflicts.length + selected.live_conflicts.length}</span></header><ul>{[...selected.conflicts, ...selected.live_conflicts].map((message, index) => <li key={`${message}-${index}`}>{message}</li>)}</ul><div><button className="secondary-button compact" type="button" disabled={resolve.isPending} onClick={() => resolve.mutate("rebase_current")}><RefreshCw size={14} />按当前版本重基</button><button className="secondary-button compact" type="button" disabled={resolve.isPending} onClick={() => resolve.mutate("manual_merge")}><Merge size={14} />手工合并</button><button className="secondary-button compact" type="button" disabled={resolve.isPending} onClick={() => { if (window.confirm("放弃当前提取并重新运行提取节点？")) resolve.mutate("reextract"); }}><RefreshCw size={14} />重新提取</button><button className="danger-button compact" type="button" disabled={resolve.isPending} onClick={() => { if (window.confirm("放弃此变更集？")) resolve.mutate("abandon"); }}><Ban size={14} />放弃</button></div></div> : null}
      <div className="change-items">{draftItems.map((item) => <ChangeItemEditor key={item.id} item={item} readOnly={readOnly} onChange={(next) => setDraftItems((items) => items.map((value) => value.id === next.id ? next : value))} />)}</div>
    </section> : null}
  </div>;
}

function ChangeItemEditor({ item, readOnly, onChange }: { item: ProposedChangeItem; readOnly: boolean; onChange: (item: ProposedChangeItem) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(item.proposed, null, 2));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { setText(JSON.stringify(item.proposed, null, 2)); setInvalid(false); }, [item.id, item.proposed]);
  const isProse = item.kind === "chapter_content" && typeof item.before.content === "string" && typeof item.proposed.content === "string";
  function decision(value: ChangeDecision) { onChange({ ...item, decision: value }); }
  return <article className={`change-item decision-${item.decision}`}>
    <header><div><span className="change-kind">{changeKindLabel(item.kind)}</span><strong>{item.target_label}</strong><small>{item.operation} · {item.target_id ? `#${item.target_id}` : "新记录"} · 置信度 {(item.confidence * 100).toFixed(0)}%</small></div><div className="item-controls"><div className="mini-segments" aria-label={`${item.target_label} 决定`}>{(["accept", "reject", "later"] as ChangeDecision[]).map((value) => <button type="button" key={value} disabled={readOnly} className={item.decision === value ? "active" : ""} onClick={() => decision(value)}>{({ accept: "接受", reject: "拒绝", later: "稍后" } as const)[value]}</button>)}</div><button className="icon-button ghost" type="button" title="编辑变更值" disabled={readOnly} onClick={() => setEditing((value) => !value)}><Pencil size={15} /></button></div></header>
    {item.conflicts.length ? <div className="item-conflicts">{item.conflicts.map((message) => <span key={message}><AlertTriangle size={13} />{message}</span>)}</div> : null}
    {isProse ? <div className="compact-diff"><ProseReview before={String(item.before.content)} proposed={String(item.proposed.content)} /></div> : <div className="before-after"><div><span>Before</span><JsonPreview value={item.before} /></div><div><span>Proposed</span><JsonPreview value={item.proposed} /></div></div>}
    {editing ? <div className="item-json-editor"><textarea aria-label={`编辑 ${item.target_label}`} rows={12} className={invalid ? "field-error" : ""} value={text} onChange={(event) => { const next = event.target.value; setText(next); try { const parsed = JSON.parse(next) as unknown; if (!isRecord(parsed)) throw new Error(); setInvalid(false); onChange({ ...item, proposed: parsed }); } catch { setInvalid(true); } }} />{invalid ? <span>JSON 无效</span> : null}</div> : null}
    <footer><div><strong>证据</strong>{item.evidence.length ? item.evidence.map((value) => <span key={value}>{value}</span>) : <span>无证据片段</span>}</div><div><strong>解析</strong><code>{String(item.resolution.method ?? item.resolution.status ?? "direct")}</code></div></footer>
  </article>;
}

function AuditsView({ audits, loading, selectedId, onSelect }: { audits: WritebackAudit[]; loading: boolean; selectedId: number | null; onSelect: (id: number) => void }) {
  const selected = audits.find((item) => item.id === selectedId) ?? null;
  if (loading) return <div className="route-loading">正在读取写回审计...</div>;
  if (!audits.length) return <EmptyState icon={FileClock} title="暂无写回审计" description="只有成功提交的单事务写回会生成不可变审计记录。" />;
  return <div className="audit-workbench"><aside className="audit-list"><header><strong>写回记录</strong><span>{audits.length}</span></header><div className="audit-list-scroll">{audits.map((item) => <button type="button" key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => onSelect(item.id)}><FileClock size={17} /><span><strong>审计 #{item.id}</strong><small>ChangeSet #{item.change_set_id} · {item.entries.length} 项</small><time>{formatDate(item.created_at)}</time></span><ChevronRight size={15} /></button>)}</div></aside>{selected ? <section className="audit-detail"><header><div><span>Append-only audit</span><h2>写回审计 #{selected.id}</h2><p>运行 #{selected.workflow_run_id} · 审批 #{selected.approval_request_id} · ChangeSet #{selected.change_set_id}</p></div><CheckCircle2 size={22} /></header><div className="audit-hash"><span>ChangeSet hash</span><code>{selected.change_set_hash}</code></div><div className="audit-entries">{selected.entries.map((entry, index) => <article key={`${String(entry.item_id)}-${index}`}><header><span>{changeKindLabel(String(entry.kind))}</span><strong>{String(entry.item_id ?? `entry-${index + 1}`)}</strong><small>{String(entry.operation ?? "write")} → #{String(entry.target_id ?? "-")}</small></header><div className="before-after"><div><span>Before</span><JsonPreview value={entry.before} /></div><div><span>Applied</span><JsonPreview value={entry.applied} /></div></div></article>)}</div></section> : null}</div>;
}

function JsonPreview({ value }: { value: unknown }) { return <pre className="json-preview">{JSON.stringify(value, null, 2)}</pre>; }

function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) { return <span className={`approval-status status-${status}`}>{approvalStatusLabel(status)}</span>; }
function ChangeSetStatusBadge({ item }: { item: ProposedChangeSet }) { const conflicted = item.status === "conflicted" || item.live_conflicts.length > 0; return <span className={`approval-status status-${conflicted ? "conflicted" : item.status}`}>{conflicted ? "有冲突" : ({ pending: "待审批", approved: "已批准", applied: "已写回", conflicted: "有冲突", cancelled: "已放弃", superseded: "已替代" } as const)[item.status]}</span>; }

function approvalStatusLabel(status: ApprovalStatus): string { return ({ pending: "待审批", approved: "已批准", changes_requested: "要求修改", rejected: "已拒绝", expired: "已过期", cancelled: "已取消", superseded: "已替代" } as const)[status]; }
function approvalTypeLabel(type: ApprovalRequest["approval_type"]): string { return ({ prose: "正文审批", change_set: "元数据审批", generic: "通用审批" } as const)[type]; }
function changeKindLabel(kind: string): string { return ({ chapter_content: "章节正文", chapter_summary: "章节摘要", scene_synopsis: "场景摘要", scene_state: "场景状态", entity: "实体", entity_alias: "实体别名", entity_relation: "实体关系", entity_state_change: "实体状态", timeline_event: "时间线", foreshadow: "伏笔" } as Record<string, string>)[kind] ?? kind; }
function errorMessage(error: unknown, fallback: string): string { if (error instanceof ApiError) return error.message || fallback; return error instanceof Error ? error.message : fallback; }
function formatDate(value: string): string { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)); }
function isRecord(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function idempotencyKey(id: number, action: string): string { const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`; return `approval-${id}-${action}-${random}`; }
function visibleText(value: string): string { if (!/<[a-z][\s\S]*>/i.test(value)) return value; const documentValue = new DOMParser().parseFromString(value, "text/html"); return documentValue.body.textContent ?? ""; }

type DiffChunk = { type: "equal" | "add" | "remove"; value: string };
function diffText(before: string, after: string): DiffChunk[] {
  if (before === after) return [{ type: "equal", value: before }];
  const left = textSegments(before);
  const right = textSegments(after);
  if (left.length * right.length > 20_000) return [{ type: "remove", value: before }, { type: "add", value: after }];
  const matrix = Array.from({ length: left.length + 1 }, () => Array<number>(right.length + 1).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) for (let j = right.length - 1; j >= 0; j -= 1) matrix[i][j] = left[i] === right[j] ? matrix[i + 1][j + 1] + 1 : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
  const chunks: DiffChunk[] = [];
  let i = 0; let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) { chunks.push({ type: "equal", value: left[i] }); i += 1; j += 1; }
    else if (j < right.length && (i === left.length || matrix[i][j + 1] >= matrix[i + 1][j])) { chunks.push({ type: "add", value: right[j] }); j += 1; }
    else { chunks.push({ type: "remove", value: left[i] }); i += 1; }
  }
  return chunks;
}
function textSegments(value: string): string[] { return value.split(/([。！？；\n])/u).reduce<string[]>((items, part) => { if (!part) return items; if (/^[。！？；\n]$/u.test(part) && items.length) items[items.length - 1] += part; else items.push(part); return items; }, []); }
