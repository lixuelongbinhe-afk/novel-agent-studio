import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Bot,
  Braces,
  CircleDot,
  ClipboardCopy,
  Database,
  DatabaseZap,
  GitBranch,
  ListChecks,
  Merge,
  Play,
  Redo2,
  ScanSearch,
  ShieldCheck,
  Square,
  Trash2,
  Type,
  Undo2
} from "lucide-react";
import type {
  AgentDefinition,
  NodeRunStatus,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeType
} from "../api/client";
import { FormField } from "../components/FormField";

type CanvasData = {
  label: string;
  nodeType: WorkflowNodeType;
  config: Record<string, unknown>;
  status?: NodeRunStatus;
};

type CanvasNode = Node<CanvasData, "studio">;
type GraphSnapshot = { nodes: CanvasNode[]; edges: Edge[] };

const NODE_OPTIONS: Array<{
  type: WorkflowNodeType;
  label: string;
  icon: typeof Play;
}> = [
  { type: "start", label: "Start", icon: Play },
  { type: "input_mapping", label: "Input Mapping", icon: Braces },
  { type: "context_retrieval", label: "Context Retrieval", icon: Database },
  { type: "agent", label: "Agent", icon: Bot },
  { type: "human_approval", label: "Human Approval", icon: ShieldCheck },
  { type: "state_extraction", label: "State Extraction", icon: ScanSearch },
  { type: "proposed_changes", label: "Proposed Changes", icon: ListChecks },
  { type: "database_writeback", label: "Database Writeback", icon: DatabaseZap },
  { type: "merge", label: "Merge", icon: Merge },
  { type: "condition", label: "Condition", icon: GitBranch },
  { type: "text_template", label: "Text Template", icon: Type },
  { type: "data_transform", label: "Data Transform", icon: CircleDot },
  { type: "output", label: "Output", icon: Square }
];

export function WorkflowCanvas({
  workflowKey,
  value,
  agents,
  statuses,
  readOnly = false,
  onChange
}: {
  workflowKey: string;
  value: { nodes: WorkflowNode[]; edges: WorkflowEdge[] };
  agents: AgentDefinition[];
  statuses?: Record<string, NodeRunStatus>;
  readOnly?: boolean;
  onChange?: (value: { nodes: WorkflowNode[]; edges: WorkflowEdge[] }) => void;
}) {
  const [nodes, setNodes] = useState<CanvasNode[]>(() => toCanvasNodes(value.nodes));
  const [edges, setEdges] = useState<Edge[]>(() => toCanvasEdges(value.edges));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [instance, setInstance] = useState<ReactFlowInstance<CanvasNode, Edge> | null>(null);
  const [stageWidth, setStageWidth] = useState(0);
  const stageRef = useRef<HTMLDivElement>(null);
  const [historyIndex, setHistoryIndex] = useState(0);
  const history = useRef<GraphSnapshot[]>([{ nodes: toCanvasNodes(value.nodes), edges: toCanvasEdges(value.edges) }]);
  const lastKey = useRef(workflowKey);

  useEffect(() => {
    if (lastKey.current === workflowKey) return;
    lastKey.current = workflowKey;
    const next = { nodes: toCanvasNodes(value.nodes), edges: toCanvasEdges(value.edges) };
    setNodes(next.nodes);
    setEdges(next.edges);
    setSelectedId(null);
    history.current = [next];
    setHistoryIndex(0);
  }, [value.edges, value.nodes, workflowKey]);

  useEffect(() => {
    if (!instance) return;
    let firstFrame = 0;
    let secondFrame = 0;
    let measuredWidth = stageRef.current?.clientWidth ?? 0;
    const fit = (width = measuredWidth) => {
      measuredWidth = width;
      window.cancelAnimationFrame(firstFrame);
      window.cancelAnimationFrame(secondFrame);
      firstFrame = window.requestAnimationFrame(() => {
        secondFrame = window.requestAnimationFrame(() => {
          if (readOnly && measuredWidth > 0 && measuredWidth < 500) {
            void instance.setViewport({ x: 0, y: 0, zoom: 1 }, { duration: 0 });
          } else {
            void instance.fitView({ padding: 0.2, duration: 180 });
          }
        });
      });
    };
    fit();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setStageWidth((current) => Math.abs(current - width) < 1 ? current : width);
      fit(width);
    });
    setStageWidth(stageRef.current?.clientWidth ?? 0);
    if (stageRef.current) observer?.observe(stageRef.current);
    const handleWindowResize = () => fit(stageRef.current?.clientWidth ?? 0);
    window.addEventListener("resize", handleWindowResize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", handleWindowResize);
      window.cancelAnimationFrame(firstFrame);
      window.cancelAnimationFrame(secondFrame);
    };
  }, [instance, readOnly, workflowKey]);

  const renderedNodes = useMemo(
    () => nodes.map((node, index) => ({
      ...node,
      position: readOnly && stageWidth > 0 && stageWidth < 500
        ? { x: Math.max(24, (stageWidth - 172) / 2), y: 28 + index * 90 }
        : node.position,
      data: { ...node.data, status: statuses?.[node.id] }
    })),
    [nodes, readOnly, stageWidth, statuses]
  );
  const selected = nodes.find((node) => node.id === selectedId) ?? null;

  function publish(nextNodes: CanvasNode[], nextEdges: Edge[]) {
    setNodes(nextNodes);
    setEdges(nextEdges);
    onChange?.({ nodes: fromCanvasNodes(nextNodes), edges: fromCanvasEdges(nextEdges) });
  }

  function commit(nextNodes: CanvasNode[], nextEdges: Edge[]) {
    publish(nextNodes, nextEdges);
    const nextHistory = history.current.slice(0, historyIndex + 1);
    nextHistory.push({ nodes: cloneNodes(nextNodes), edges: cloneEdges(nextEdges) });
    history.current = nextHistory.slice(-80);
    setHistoryIndex(history.current.length - 1);
  }

  function handleNodeChanges(changes: NodeChange<CanvasNode>[]) {
    const next = applyNodeChanges(changes, nodes);
    const structural = changes.some((change) => change.type === "remove");
    if (structural) commit(next, edges);
    else {
      setNodes(next);
      if (changes.some((change) => change.type === "position")) {
        onChange?.({ nodes: fromCanvasNodes(next), edges: fromCanvasEdges(edges) });
      }
    }
    const selectedChange = changes.find((change) => change.type === "select" && change.selected);
    if (selectedChange && "id" in selectedChange) setSelectedId(selectedChange.id);
  }

  function handleEdgeChanges(changes: EdgeChange<Edge>[]) {
    const next = applyEdgeChanges(changes, edges);
    if (changes.some((change) => change.type === "remove")) commit(nodes, next);
    else setEdges(next);
  }

  function connect(connection: Connection) {
    const id = edgeId(connection.source, connection.target, edges.length + 1);
    commit(
      nodes,
      addEdge({ ...connection, id, type: "smoothstep", animated: false }, edges)
    );
  }

  function addNode(type: WorkflowNodeType, position?: { x: number; y: number }) {
    const count = nodes.filter((node) => node.data.nodeType === type).length + 1;
    const id = uniqueNodeId(type, nodes);
    const option = NODE_OPTIONS.find((item) => item.type === type)!;
    const node: CanvasNode = {
      id,
      type: "studio",
      position: position ?? { x: 120 + count * 28, y: 100 + count * 28 },
      data: {
        label: type === "start" || type === "output" ? option.label : `${option.label} ${count}`,
        nodeType: type,
        config: defaultConfig(type, agents)
      }
    };
    commit([...nodes, node], edges);
    setSelectedId(id);
  }

  function updateSelected(patch: Partial<CanvasData>) {
    if (!selected) return;
    commit(
      nodes.map((node) => node.id === selected.id ? { ...node, data: { ...node.data, ...patch } } : node),
      edges
    );
  }

  function copySelection() {
    const chosen = nodes.filter((node) => node.selected || node.id === selectedId);
    if (!chosen.length) return;
    const replacements = new Map<string, string>();
    const copies = chosen.map((node) => {
      const id = uniqueNodeId(node.data.nodeType, [...nodes, ...chosen], replacements.size + 1);
      replacements.set(node.id, id);
      return {
        ...node,
        id,
        selected: false,
        position: { x: node.position.x + 36, y: node.position.y + 36 },
        data: { ...node.data, config: { ...node.data.config }, label: `${node.data.label} 副本` }
      };
    });
    const copiedEdges = edges
      .filter((edge) => replacements.has(edge.source) && replacements.has(edge.target))
      .map((edge, index) => ({
        ...edge,
        id: edgeId(replacements.get(edge.source)!, replacements.get(edge.target)!, index + edges.length + 1),
        source: replacements.get(edge.source)!,
        target: replacements.get(edge.target)!
      }));
    commit([...nodes, ...copies], [...edges, ...copiedEdges]);
  }

  function deleteSelection() {
    const ids = new Set(nodes.filter((node) => node.selected || node.id === selectedId).map((node) => node.id));
    if (!ids.size) return;
    commit(
      nodes.filter((node) => !ids.has(node.id)),
      edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target))
    );
    setSelectedId(null);
  }

  function travel(index: number) {
    const snapshot = history.current[index];
    if (!snapshot) return;
    setHistoryIndex(index);
    publish(cloneNodes(snapshot.nodes), cloneEdges(snapshot.edges));
  }

  return (
    <div className={`workflow-builder ${readOnly ? "read-only" : ""}`}>
      {!readOnly ? <aside className="node-palette" aria-label="节点工具箱">
        <header><span>节点</span></header>
        {NODE_OPTIONS.map(({ type, label, icon: Icon }) => (
          <button
            key={type}
            type="button"
            draggable
            onDragStart={(event) => event.dataTransfer.setData("application/nas-node", type)}
            onClick={() => addNode(type)}
          >
            <Icon size={15} /><span>{label}</span>
          </button>
        ))}
      </aside> : null}
      <section className="workflow-canvas-shell">
        {!readOnly ? <div className="canvas-commandbar">
          <button className="icon-button ghost" type="button" title="撤销" disabled={historyIndex <= 0} onClick={() => travel(historyIndex - 1)}><Undo2 size={17} /></button>
          <button className="icon-button ghost" type="button" title="重做" disabled={historyIndex >= history.current.length - 1} onClick={() => travel(historyIndex + 1)}><Redo2 size={17} /></button>
          <span />
          <button className="icon-button ghost" type="button" title="复制选中节点" onClick={copySelection}><ClipboardCopy size={17} /></button>
          <button className="icon-button ghost danger-ink" type="button" title="删除选中节点" onClick={deleteSelection}><Trash2 size={17} /></button>
        </div> : null}
        <div
          ref={stageRef}
          className="workflow-canvas-stage"
          onDragOver={(event) => {
            if (readOnly) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDrop={(event) => {
            if (readOnly) return;
            event.preventDefault();
            const type = event.dataTransfer.getData("application/nas-node") as WorkflowNodeType;
            if (!NODE_OPTIONS.some((item) => item.type === type) || !instance) return;
            addNode(type, instance.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
          }}
        >
          <ReactFlow<CanvasNode, Edge>
            nodes={renderedNodes}
            edges={edges}
            nodeTypes={STUDIO_NODE_TYPES}
            onInit={setInstance}
            onNodesChange={handleNodeChanges}
            onEdgesChange={handleEdgeChanges}
            onConnect={connect}
            onNodeClick={(_event, node) => setSelectedId(node.id)}
            onNodeDragStop={(_event, dragged) => {
              const next = nodes.map((node) => node.id === dragged.id ? { ...node, position: dragged.position } : node);
              commit(next, edges);
            }}
            nodesDraggable={!readOnly}
            nodesConnectable={!readOnly}
            edgesReconnectable={!readOnly}
            deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            onlyRenderVisibleElements
            minZoom={0.2}
            maxZoom={2}
            multiSelectionKeyCode="Shift"
            selectionOnDrag={!readOnly}
          >
            <Background gap={18} size={1} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(node) => nodeColor((node.data as CanvasData).nodeType)} />
          </ReactFlow>
        </div>
      </section>
      {!readOnly ? <aside className="node-inspector" aria-label="节点检查器">
        {selected ? (
          <>
            <header><span>节点配置</span><strong>{nodeTypeLabel(selected.data.nodeType)}</strong></header>
            <FormField label="显示名称"><input value={selected.data.label} onChange={(event) => updateSelected({ label: event.target.value })} /></FormField>
            <FormField label="Node Key"><input value={selected.id} disabled /></FormField>
            {["agent", "context_retrieval", "state_extraction"].includes(selected.data.nodeType) ? (
              <FormField label={selected.data.nodeType === "agent" ? "Agent" : "目标 Agent"}>
                <select value={String(selected.data.config.agent_id ?? "")} onChange={(event) => updateSelected({ config: { ...selected.data.config, agent_id: Number(event.target.value) } })}>
                  <option value="" disabled>请选择 Agent</option>
                  {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · v{agent.version}</option>)}
                </select>
              </FormField>
            ) : null}
            {selected.data.nodeType === "human_approval" ? <ApprovalFields value={selected.data.config} agents={agents} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "state_extraction" ? <StateExtractionFields value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "proposed_changes" ? <ProposedChangesFields value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "database_writeback" ? <WritebackFields value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "condition" ? <ConditionFields value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "merge" ? <MergeFields value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
            {selected.data.nodeType === "text_template" ? <FormField label="模板"><textarea rows={10} value={String(selected.data.config.template ?? "")} onChange={(event) => updateSelected({ config: { ...selected.data.config, template: event.target.value } })} /></FormField> : null}
            {!["start", "condition", "merge", "text_template", "human_approval", "state_extraction", "proposed_changes", "database_writeback"].includes(selected.data.nodeType) ? <JsonConfig value={selected.data.config} onChange={(config) => updateSelected({ config })} /> : null}
          </>
        ) : <div className="inspector-empty"><CircleDot size={24} /><span>选择节点后编辑配置</span></div>}
      </aside> : null}
    </div>
  );
}

const STUDIO_NODE_TYPES = { studio: StudioNode };

function StudioNode({ data, selected }: NodeProps<CanvasNode>) {
  const Icon = NODE_OPTIONS.find((item) => item.type === data.nodeType)?.icon ?? CircleDot;
  return (
    <div className={`studio-flow-node node-${data.nodeType} ${selected ? "selected" : ""} ${data.status ? `run-${data.status}` : ""}`}>
      {data.nodeType !== "start" ? <Handle type="target" position={Position.Left} /> : null}
      <div><Icon size={16} /><span><strong>{data.label}</strong><small>{nodeTypeLabel(data.nodeType)}</small></span></div>
      {data.status ? <span className={`node-run-dot status-${data.status}`} title={runStatusLabel(data.status)} /> : null}
      {data.nodeType === "condition" ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "34%" }} />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "70%" }} />
          <span className="condition-handle-label true">T</span><span className="condition-handle-label false">F</span>
        </>
      ) : data.nodeType !== "output" ? <Handle type="source" position={Position.Right} /> : null}
    </div>
  );
}

function ConditionFields({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  return <><FormField label="变量路径"><input value={String(value.path ?? "input.flag")} onChange={(event) => onChange({ ...value, path: event.target.value })} /></FormField><FormField label="比较"><select value={String(value.operator ?? "equals")} onChange={(event) => onChange({ ...value, operator: event.target.value })}>{["equals", "not_equals", "contains", "exists", "gt", "gte", "lt", "lte"].map((item) => <option key={item} value={item}>{item}</option>)}</select></FormField><FormField label="目标值"><input value={jsonScalar(value.value)} onChange={(event) => onChange({ ...value, value: parseScalar(event.target.value) })} /></FormField></>;
}

function MergeFields({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  return <><FormField label="合并模式"><select value={String(value.mode ?? "object")} onChange={(event) => onChange({ ...value, mode: event.target.value })}><option value="object">对象</option><option value="array">数组</option><option value="concat">文本拼接</option></select></FormField>{value.mode === "concat" ? <FormField label="分隔符"><input value={String(value.separator ?? "\n\n")} onChange={(event) => onChange({ ...value, separator: event.target.value })} /></FormField> : null}</>;
}

function ApprovalFields({ value, agents, onChange }: { value: Record<string, unknown>; agents: AgentDefinition[]; onChange: (value: Record<string, unknown>) => void }) {
  const approvalType = String(value.approval_type ?? "prose");
  return <>
    <FormField label="审批类型"><select value={approvalType} onChange={(event) => { const next: Record<string, unknown> = { ...value, approval_type: event.target.value }; if (event.target.value !== "prose") delete next.revision_agent_id; onChange(next); }}><option value="prose">正文</option><option value="change_set">元数据变更</option><option value="generic">通用</option></select></FormField>
    <FormField label="标题"><input value={String(value.title ?? "人工审批")} onChange={(event) => onChange({ ...value, title: event.target.value })} /></FormField>
    <FormField label="审批说明"><textarea rows={5} value={String(value.instructions ?? "")} onChange={(event) => onChange({ ...value, instructions: event.target.value })} /></FormField>
    {approvalType === "prose" ? <FormField label="修订 Agent"><select value={String(value.revision_agent_id ?? "")} onChange={(event) => onChange({ ...value, revision_agent_id: Number(event.target.value) })}><option value="" disabled>请选择 Agent</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · v{agent.version}</option>)}</select></FormField> : null}
    <FormField label="过期秒数"><input type="number" min={30} max={604800} value={value.expires_in_seconds == null ? "" : String(value.expires_in_seconds)} onChange={(event) => onChange(optionalConfig(value, "expires_in_seconds", event.target.value ? Number(event.target.value) : undefined))} /></FormField>
  </>;
}

function StateExtractionFields({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  return <>
    <FormField label="章节 ID 路径"><input value={String(value.chapter_id_path ?? "input.chapter_id")} onChange={(event) => onChange({ ...value, chapter_id_path: event.target.value })} /></FormField>
    <FormField label="场景 ID 路径"><input value={String(value.scene_id_path ?? "input.scene_id")} onChange={(event) => onChange({ ...value, scene_id_path: event.target.value })} /></FormField>
    <label className="checkbox-row"><input type="checkbox" checked={Boolean(value.automatic_context)} onChange={(event) => onChange({ ...value, automatic_context: event.target.checked })} /><span>自动构建上下文</span></label>
  </>;
}

function ProposedChangesFields({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  return <>
    <FormField label="章节 ID 路径"><input value={String(value.chapter_id_path ?? "")} placeholder="沿用提取结果" onChange={(event) => onChange(optionalConfig(value, "chapter_id_path", event.target.value || undefined))} /></FormField>
    <FormField label="场景 ID 路径"><input value={String(value.scene_id_path ?? "")} placeholder="沿用提取结果" onChange={(event) => onChange(optionalConfig(value, "scene_id_path", event.target.value || undefined))} /></FormField>
  </>;
}

function WritebackFields({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  return <FormField label="冲突轮询（秒）"><input type="number" min={0.1} max={5} step={0.1} value={String(value.poll_seconds ?? 0.5)} onChange={(event) => onChange({ ...value, poll_seconds: Number(event.target.value) })} /></FormField>;
}

function JsonConfig({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  return <FormField label="配置 JSON" hint={invalid ? "JSON 无效" : undefined}><textarea className={invalid ? "field-error" : ""} rows={14} value={text} onChange={(event) => { const next = event.target.value; setText(next); try { const parsed = JSON.parse(next) as unknown; if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(); setInvalid(false); onChange(parsed as Record<string, unknown>); } catch { setInvalid(true); } }} /></FormField>;
}

function optionalConfig(value: Record<string, unknown>, key: string, next: unknown): Record<string, unknown> {
  const result = { ...value };
  if (next === undefined) delete result[key];
  else result[key] = next;
  return result;
}

function toCanvasNodes(values: WorkflowNode[]): CanvasNode[] {
  return values.map((item) => ({ id: item.key, type: "studio", position: { x: item.position_x, y: item.position_y }, data: { label: item.label, nodeType: item.type, config: { ...item.config } } }));
}

function toCanvasEdges(values: WorkflowEdge[]): Edge[] {
  return values.map((item) => ({ id: item.key, source: item.source, target: item.target, sourceHandle: item.source_handle, targetHandle: item.target_handle, type: "smoothstep" }));
}

function fromCanvasNodes(values: CanvasNode[]): WorkflowNode[] {
  return values.map((item) => ({ key: item.id, type: item.data.nodeType, label: item.data.label, position_x: item.position.x, position_y: item.position.y, config: item.data.config }));
}

function fromCanvasEdges(values: Edge[]): WorkflowEdge[] {
  return values.map((item) => ({ key: item.id, source: item.source, target: item.target, source_handle: item.sourceHandle ?? null, target_handle: item.targetHandle ?? null }));
}

function defaultConfig(type: WorkflowNodeType, agents: AgentDefinition[]): Record<string, unknown> {
  if (type === "agent") return { agent_id: agents[0]?.id ?? null };
  if (type === "human_approval") return {
    approval_type: "prose",
    title: "正文审批",
    instructions: "确认正文或填写修改要求。",
    revision_agent_id: agents[0]?.id ?? null
  };
  if (type === "state_extraction") return {
    agent_id: agents[0]?.id ?? null,
    chapter_id_path: "input.chapter_id",
    scene_id_path: "input.scene_id",
    automatic_context: false
  };
  if (type === "proposed_changes") return {};
  if (type === "database_writeback") return { poll_seconds: 0.5 };
  if (type === "context_retrieval") return {
    agent_id: agents[0]?.id ?? null,
    chapter_id_path: "input.chapter_id",
    scene_id_path: "input.scene_id",
    query_template: "{input.task}",
    token_budget: 6000,
    reserved_output_tokens: 1024
  };
  if (type === "input_mapping") return { mapping: { task: "input.task" } };
  if (type === "merge") return { mode: "object" };
  if (type === "condition") return { path: "input.flag", operator: "equals", value: true };
  if (type === "text_template") return { template: "{value}" };
  if (type === "data_transform") return { operation: "passthrough", path: "value" };
  return {};
}

function uniqueNodeId(type: WorkflowNodeType, nodes: CanvasNode[], offset = 0): string {
  let index = nodes.filter((node) => node.data.nodeType === type).length + 1 + offset;
  let value = `${type}_${index}`;
  while (nodes.some((node) => node.id === value)) value = `${type}_${++index}`;
  return value;
}

function edgeId(source: string, target: string, index: number): string { return `e_${source}_${target}_${index}`.replace(/[^A-Za-z0-9_.:-]/g, "_"); }
function cloneNodes(nodes: CanvasNode[]): CanvasNode[] { return nodes.map((node) => ({ ...node, position: { ...node.position }, data: { ...node.data, config: { ...node.data.config } } })); }
function cloneEdges(edges: Edge[]): Edge[] { return edges.map((edge) => ({ ...edge })); }
function nodeTypeLabel(type: WorkflowNodeType): string { return NODE_OPTIONS.find((item) => item.type === type)?.label ?? type; }
function nodeColor(type: WorkflowNodeType): string { return ({ start: "#397a5b", output: "#9b7045", agent: "#0b7b83", context_retrieval: "#2d718f", human_approval: "#a0782b", state_extraction: "#6f5f99", proposed_changes: "#ad633f", database_writeback: "#39705f", condition: "#a0782b", merge: "#626d76", input_mapping: "#5f6f9b", text_template: "#8a5a77", data_transform: "#67745c" } as Record<WorkflowNodeType, string>)[type]; }
function runStatusLabel(status: NodeRunStatus): string { return ({ pending: "等待", ready: "就绪", running: "运行中", waiting_approval: "等待审批", completed: "完成", failed: "失败", skipped: "跳过", cancelled: "取消" } as Record<NodeRunStatus, string>)[status]; }
function jsonScalar(value: unknown): string { return typeof value === "string" ? value : JSON.stringify(value ?? null); }
function parseScalar(value: string): unknown { try { return JSON.parse(value) as unknown; } catch { return value; } }
