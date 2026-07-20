import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  FilePlus2,
  History,
  Lightbulb,
  MapPin,
  Package,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Save,
  Sparkles,
  Trash2,
  UserRound,
  UsersRound
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, Chapter, ProjectTree, RecordBase, Scene, Volume } from "../api/client";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";
import { ManuscriptEditor } from "../components/ManuscriptEditor";
import { useUiStore } from "../stores/ui";

type CreateTarget = { kind: "volume" | "chapter" | "scene"; parentId: number };
type DeleteTarget = { resource: string; record: RecordBase; label: string };
type ReferenceTab = "character" | "location" | "item" | "foreshadow" | "style";

export function WorkspacePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const selectedChapterId = useUiStore((state) => state.selectedChapterId);
  const selectedSceneId = useUiStore((state) => state.selectedSceneId);
  const setChapter = useUiStore((state) => state.setChapter);
  const setScene = useUiStore((state) => state.setScene);
  const rightPanelOpen = useUiStore((state) => state.rightPanelOpen);
  const toggleRightPanel = useUiStore((state) => state.toggleRightPanel);

  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const projectId = selectedProjectId ?? projects[0]?.id;
  const {
    data: tree,
    isLoading,
    error: treeError
  } = useQuery({
    queryKey: ["tree", projectId],
    queryFn: () => api.tree(projectId!),
    enabled: Boolean(projectId)
  });
  const chapters = tree?.chapters ?? [];
  const scenes = tree?.scenes ?? [];
  const currentChapter = useMemo(
    () => chapters.find((chapter) => chapter.id === selectedChapterId) ?? chapters[0],
    [chapters, selectedChapterId]
  );
  const currentScene = useMemo(
    () => scenes.find((scene) => scene.id === selectedSceneId && scene.chapter_id === currentChapter?.id),
    [scenes, selectedSceneId, currentChapter?.id]
  );

  useEffect(() => {
    if (currentChapter && currentChapter.id !== selectedChapterId) setChapter(currentChapter.id);
  }, [currentChapter?.id, selectedChapterId, setChapter]);

  const [title, setTitle] = useState("");
  const [synopsis, setSynopsis] = useState("");
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [createTarget, setCreateTarget] = useState<CreateTarget | null>(null);
  const [createTitle, setCreateTitle] = useState("");
  const [renameVolume, setRenameVolume] = useState<Volume | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [referenceTab, setReferenceTab] = useState<ReferenceTab>("character");
  const titleRef = useRef(title);
  const synopsisRef = useRef(synopsis);
  const draftRef = useRef(draft);
  titleRef.current = title;
  synopsisRef.current = synopsis;
  draftRef.current = draft;

  useEffect(() => {
    const record = currentScene ?? currentChapter;
    if (!record) return;
    setTitle(record.title);
    setSynopsis("synopsis" in record ? record.synopsis : "");
    setDraft(record.content);
    setDirty(false);
  }, [currentChapter?.id, currentScene?.id]);

  const updateTreeRecord = (record: Chapter | Scene) => {
    queryClient.setQueryData<ProjectTree>(["tree", projectId], (current) => {
      if (!current) return current;
      if ("volume_id" in record) {
        return { ...current, chapters: current.chapters.map((item) => (item.id === record.id ? record : item)) };
      }
      return { ...current, scenes: current.scenes.map((item) => (item.id === record.id ? record : item)) };
    });
  };

  const saveChapter = useMutation({
    mutationFn: (input: { chapter: Chapter; title: string; content: string }) =>
      api.autosaveChapter(input.chapter, input.title, input.content),
    onSuccess: (saved, input) => {
      updateTreeRecord(saved);
      setLastSavedAt(new Date());
      if (titleRef.current === input.title && draftRef.current === input.content) setDirty(false);
    }
  });
  const saveScene = useMutation({
    mutationFn: (input: { scene: Scene; title: string; synopsis: string; content: string }) =>
      api.updateScene(input.scene, { title: input.title, synopsis: input.synopsis, content: input.content }),
    onSuccess: (saved, input) => {
      updateTreeRecord(saved);
      setLastSavedAt(new Date());
      if (
        titleRef.current === input.title &&
        synopsisRef.current === input.synopsis &&
        draftRef.current === input.content
      ) setDirty(false);
    }
  });

  const saveNow = () => {
    if (!dirty || !title.trim()) return;
    if (currentScene) {
      saveScene.mutate({ scene: currentScene, title: title.trim(), synopsis: synopsis.trim(), content: draft });
    }
    else if (currentChapter) saveChapter.mutate({ chapter: currentChapter, title: title.trim(), content: draft });
  };

  useEffect(() => {
    if (!dirty || saveChapter.isPending || saveScene.isPending) return;
    const timer = window.setTimeout(saveNow, 1800);
    return () => window.clearTimeout(timer);
  }, [dirty, title, synopsis, draft, currentChapter?.id, currentScene?.id, saveChapter.isPending, saveScene.isPending]);

  const refreshTree = () => queryClient.invalidateQueries({ queryKey: ["tree", projectId] });
  const createRecord = useMutation({
    mutationFn: async () => {
      if (!createTarget) throw new Error("Missing create target");
      if (createTarget.kind === "volume") {
        return api.createVolume(projectId!, createTitle.trim(), (tree?.volumes.length ?? 0) + 1);
      }
      if (createTarget.kind === "chapter") {
        const count = chapters.filter((item) => item.volume_id === createTarget.parentId).length;
        return api.createChapter(createTarget.parentId, createTitle.trim(), count + 1);
      }
      const count = scenes.filter((item) => item.chapter_id === createTarget.parentId).length;
      return api.createScene(createTarget.parentId, createTitle.trim(), count + 1);
    },
    onSuccess: async (record) => {
      await refreshTree();
      if (createTarget?.kind === "chapter") setChapter(record.id);
      if (createTarget?.kind === "scene") setScene(record.id);
      setCreateTarget(null);
      setCreateTitle("");
    }
  });
  const renameVolumeMutation = useMutation({
    mutationFn: () => api.updateVolume(renameVolume!, { title: renameValue.trim() }),
    onSuccess: async () => {
      await refreshTree();
      setRenameVolume(null);
    }
  });
  const deleteRecord = useMutation({
    mutationFn: (target: DeleteTarget) => api.deleteRecord(target.resource, target.record),
    onSuccess: async () => {
      setDeleteTarget(null);
      setScene(null);
      await refreshTree();
    }
  });
  const reorderRecords = useMutation({
    mutationFn: ({ resource, items }: { resource: string; items: Array<Volume | Chapter | Scene> }) =>
      api.reorder(resource, items),
    onSuccess: refreshTree
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["chapter-versions", currentChapter?.id],
    queryFn: () => api.listChapterVersions(currentChapter!.id),
    enabled: Boolean(currentChapter && versionsOpen)
  });
  const restoreVersion = useMutation({
    mutationFn: (versionId: number) => api.restoreChapterVersion(currentChapter!, versionId),
    onSuccess: async (saved) => {
      updateTreeRecord(saved);
      setTitle(saved.title);
      setDraft(saved.content);
      setDirty(false);
      setVersionsOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["chapter-versions", currentChapter?.id] });
    }
  });

  const editorError = saveChapter.error ?? saveScene.error;
  const saving = saveChapter.isPending || saveScene.isPending;

  if (!projectId) {
    return (
      <EmptyState
        icon={BookOpen}
        title="先创建一个小说项目"
        description="项目创建后，卷、章和场景树会显示在这里。"
        action={<button className="primary-button" onClick={() => navigate("/")}>返回项目首页</button>}
      />
    );
  }

  return (
    <section className={`workspace-grid ${rightPanelOpen ? "" : "right-closed"}`}>
      <aside className="tree-panel" aria-label="卷章场景树">
        <div className="panel-heading compact">
          <div>
            <span className="eyebrow">作品结构</span>
            <h2>卷 / 章 / 场景</h2>
          </div>
          <button className="icon-button ghost" type="button" onClick={() => setCreateTarget({ kind: "volume", parentId: projectId })} title="新建卷">
            <Plus size={17} />
          </button>
        </div>
        {treeError ? <ErrorNotice message="无法读取作品结构。" /> : null}
        <div className="tree-scroll">
          {isLoading ? <div className="tree-loading">正在读取章节...</div> : null}
          {tree?.volumes.map((volume, volumeIndex) => {
            const volumeChapters = chapters.filter((chapter) => chapter.volume_id === volume.id);
            return (
              <div className="tree-volume" key={volume.id}>
                <div className="tree-volume-row">
                  <ChevronDown size={15} />
                  <button
                    className="tree-label"
                    type="button"
                    onDoubleClick={() => {
                      setRenameVolume(volume);
                      setRenameValue(volume.title);
                    }}
                  >
                    {volume.title}
                  </button>
                  <button className="tree-action" type="button" onClick={() => setCreateTarget({ kind: "chapter", parentId: volume.id })} title="新建章节">
                    <FilePlus2 size={15} />
                  </button>
                  {tree.volumes.length > 1 ? (
                    <>
                      <button
                        className="tree-action"
                        type="button"
                        disabled={volumeIndex === 0}
                        onClick={() => reorderRecords.mutate({ resource: "volume", items: moveItem(tree.volumes, volumeIndex, volumeIndex - 1) })}
                        title="上移卷"
                      >
                        <ChevronUp size={14} />
                      </button>
                      <button
                        className="tree-action"
                        type="button"
                        disabled={volumeIndex === tree.volumes.length - 1}
                        onClick={() => reorderRecords.mutate({ resource: "volume", items: moveItem(tree.volumes, volumeIndex, volumeIndex + 1) })}
                        title="下移卷"
                      >
                        <ChevronDown size={14} />
                      </button>
                    </>
                  ) : null}
                  <button
                    className="tree-action danger"
                    type="button"
                    onClick={() => setDeleteTarget({ resource: "volume", record: volume, label: volume.title })}
                    title="删除卷"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="tree-children">
                  {volumeChapters.map((chapter, chapterIndex) => {
                    const chapterScenes = scenes.filter((scene) => scene.chapter_id === chapter.id);
                    const active = chapter.id === currentChapter?.id && !currentScene;
                    return (
                      <div className="tree-chapter" key={chapter.id}>
                        <div className={`tree-chapter-row ${active ? "selected" : ""}`}>
                          <button
                            className="tree-label"
                            type="button"
                            onClick={() => {
                              setChapter(chapter.id);
                              setScene(null);
                            }}
                          >
                            <span>{chapter.title}</span>
                            <small>{chapter.word_count.toLocaleString()} 字</small>
                          </button>
                          <button className="tree-action" type="button" onClick={() => setCreateTarget({ kind: "scene", parentId: chapter.id })} title="新建场景">
                            <Plus size={14} />
                          </button>
                          <button
                            className="tree-action danger"
                            type="button"
                            onClick={() => setDeleteTarget({ resource: "chapter", record: chapter, label: chapter.title })}
                            title="删除章节"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                        <div className="tree-scenes">
                          {chapterScenes.map((scene, sceneIndex) => (
                            <div key={scene.id} className={`tree-scene-row ${scene.id === currentScene?.id ? "selected" : ""}`}>
                              <button
                                className="tree-scene-select"
                                type="button"
                                onClick={() => {
                                  setChapter(chapter.id);
                                  setScene(scene.id);
                                }}
                              >
                                <ChevronRight size={13} />
                                <span>{scene.title}</span>
                              </button>
                              {chapterScenes.length > 1 ? (
                                <>
                                  <button
                                    className="tree-action"
                                    type="button"
                                    disabled={sceneIndex === 0}
                                    onClick={() => reorderRecords.mutate({ resource: "scene", items: moveItem(chapterScenes, sceneIndex, sceneIndex - 1) })}
                                    title="上移场景"
                                  >
                                    <ChevronUp size={13} />
                                  </button>
                                  <button
                                    className="tree-action"
                                    type="button"
                                    disabled={sceneIndex === chapterScenes.length - 1}
                                    onClick={() => reorderRecords.mutate({ resource: "scene", items: moveItem(chapterScenes, sceneIndex, sceneIndex + 1) })}
                                    title="下移场景"
                                  >
                                    <ChevronDown size={13} />
                                  </button>
                                </>
                              ) : null}
                              <button
                                className="tree-action danger"
                                type="button"
                                onClick={() => setDeleteTarget({ resource: "scene", record: scene, label: scene.title })}
                                title="删除场景"
                              >
                                <Trash2 size={13} />
                              </button>
                            </div>
                          ))}
                        </div>
                        {volumeChapters.length > 1 ? (
                          <div className="tree-order-controls" aria-label={`${chapter.title} 排序`}>
                            <button
                              type="button"
                              disabled={chapterIndex === 0}
                              onClick={() => reorderRecords.mutate({ resource: "chapter", items: moveItem(volumeChapters, chapterIndex, chapterIndex - 1) })}
                            >上移</button>
                            <button
                              type="button"
                              disabled={chapterIndex === volumeChapters.length - 1}
                              onClick={() => reorderRecords.mutate({ resource: "chapter", items: moveItem(volumeChapters, chapterIndex, chapterIndex + 1) })}
                            >下移</button>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </aside>

      <section className="editor-panel">
        {currentChapter ? (
          <>
            <header className="editor-header">
              <div className="editor-title-wrap">
                <span className="editor-context">{currentScene ? `场景 · ${currentChapter.title}` : "章节正文"}</span>
                <input
                  className="editor-title-input"
                  value={title}
                  onChange={(event) => {
                    setTitle(event.target.value);
                    setDirty(true);
                  }}
                  aria-label="标题"
                />
                {currentScene ? (
                  <input
                    className="scene-synopsis-input"
                    value={synopsis}
                    onChange={(event) => {
                      setSynopsis(event.target.value);
                      setDirty(true);
                    }}
                    aria-label="场景梗概"
                    placeholder="场景梗概（可选）"
                    maxLength={20000}
                  />
                ) : null}
              </div>
              <div className="editor-actions">
                {!currentScene ? (
                  <button className="icon-button ghost" type="button" onClick={() => setVersionsOpen(true)} title="版本历史">
                    <History size={18} />
                  </button>
                ) : null}
                <button className="icon-button" type="button" onClick={saveNow} disabled={!dirty || saving || !title.trim()} title="保存 Ctrl+S">
                  <Save size={18} />
                </button>
                <button className="icon-button ghost" type="button" onClick={toggleRightPanel} title={rightPanelOpen ? "收起资料侧栏" : "展开资料侧栏"}>
                  {rightPanelOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
                </button>
              </div>
            </header>
            {editorError ? <ErrorNotice message="保存失败：内容已在别处更新，或本地服务暂时不可用。请先复制当前内容，再刷新合并。" /> : null}
            <ManuscriptEditor
              value={draft}
              onChange={(value) => {
                setDraft(value);
                setDirty(true);
              }}
              placeholder={currentScene ? "写下这个场景的正文..." : "从这一章开始写..."}
              onSave={saveNow}
            />
            <footer className="editor-footer">
              <span className={`save-state ${editorError ? "error" : dirty ? "dirty" : "saved"}`}>
                {saving ? "保存中..." : editorError ? "保存失败" : dirty ? "等待自动保存" : "已保存"}
              </span>
              <span>{countWords(stripHtml(draft)).toLocaleString()} 字</span>
              <span>修订 {currentScene?.revision ?? currentChapter.revision}</span>
              {lastSavedAt ? <span>最近保存 {lastSavedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span> : null}
            </footer>
          </>
        ) : (
          <EmptyState icon={FilePlus2} title="还没有章节" description="从左侧先创建一个卷和章节。" />
        )}
      </section>

      {rightPanelOpen ? (
        <ReferencePanel
          projectId={projectId}
          activeTab={referenceTab}
          onTabChange={setReferenceTab}
          onOpenLibrary={() => navigate("/library")}
        />
      ) : null}

      <Dialog
        open={Boolean(createTarget)}
        title={`新建${createTarget?.kind === "volume" ? "卷" : createTarget?.kind === "chapter" ? "章节" : "场景"}`}
        width="small"
        onClose={() => setCreateTarget(null)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setCreateTarget(null)}>取消</button>
            <button className="primary-button" type="submit" form="create-tree-record" disabled={!createTitle.trim() || createRecord.isPending}>
              <Plus size={17} />
              创建
            </button>
          </>
        }
      >
        <form
          id="create-tree-record"
          onSubmit={(event) => {
            event.preventDefault();
            createRecord.mutate();
          }}
        >
          <FormField label="名称">
            <input value={createTitle} onChange={(event) => setCreateTitle(event.target.value)} autoFocus maxLength={200} />
          </FormField>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(renameVolume)}
        title="重命名卷"
        width="small"
        onClose={() => setRenameVolume(null)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setRenameVolume(null)}>取消</button>
            <button className="primary-button" type="button" onClick={() => renameVolumeMutation.mutate()} disabled={!renameValue.trim()}>保存</button>
          </>
        }
      >
        <FormField label="卷名">
          <input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} autoFocus />
        </FormField>
      </Dialog>

      <Dialog
        open={Boolean(deleteTarget)}
        title="移到回收站"
        width="small"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <button className="secondary-button" type="button" onClick={() => setDeleteTarget(null)}>取消</button>
            <button className="danger-button" type="button" onClick={() => deleteTarget && deleteRecord.mutate(deleteTarget)}>
              <Trash2 size={17} />
              删除
            </button>
          </>
        }
      >
        <p>“{deleteTarget?.label}”会进入回收站，不会永久丢失。</p>
      </Dialog>

      <Dialog
        open={versionsOpen}
        title="章节版本历史"
        description="恢复前会自动保存当前版本。"
        width="large"
        onClose={() => setVersionsOpen(false)}
      >
        {versions.length === 0 ? (
          <EmptyState icon={History} title="还没有历史版本" description="正文首次保存后会生成版本。" />
        ) : (
          <div className="version-list">
            {versions.map((version) => (
              <article key={version.id}>
                <div>
                  <strong>{version.title}</strong>
                  <span>{new Date(version.created_at).toLocaleString()} · {version.word_count} 字 · {version.source}</span>
                </div>
                <p>{version.content.slice(0, 160) || "（空白版本）"}</p>
                <button className="secondary-button" type="button" onClick={() => restoreVersion.mutate(version.id)}>恢复此版本</button>
              </article>
            ))}
          </div>
        )}
      </Dialog>
    </section>
  );
}

function ReferencePanel({
  projectId,
  activeTab,
  onTabChange,
  onOpenLibrary
}: {
  projectId: number;
  activeTab: ReferenceTab;
  onTabChange: (tab: ReferenceTab) => void;
  onOpenLibrary: () => void;
}) {
  const { data: entities = [] } = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId) });
  const { data: foreshadows = [] } = useQuery({ queryKey: ["foreshadows", projectId], queryFn: () => api.listForeshadows(projectId) });
  const { data: styles = [] } = useQuery({ queryKey: ["style-guides", projectId], queryFn: () => api.listStyleGuides(projectId) });
  const tabs: Array<{ key: ReferenceTab; label: string; icon: typeof UserRound }> = [
    { key: "character", label: "人物", icon: UserRound },
    { key: "location", label: "地点", icon: MapPin },
    { key: "item", label: "物品", icon: Package },
    { key: "foreshadow", label: "伏笔", icon: Lightbulb },
    { key: "style", label: "风格", icon: Sparkles }
  ];
  const filtered = entities.filter((entity) => entity.kind === activeTab);

  return (
    <aside className="bible-panel" aria-label="资料侧栏">
      <div className="panel-heading compact">
        <div>
          <span className="eyebrow">随写随查</span>
          <h2>资料侧栏</h2>
        </div>
        <button className="text-button" type="button" onClick={onOpenLibrary}>完整资料库</button>
      </div>
      <div className="reference-tabs" role="tablist">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button key={tab.key} className={activeTab === tab.key ? "active" : ""} type="button" onClick={() => onTabChange(tab.key)} title={tab.label}>
              <Icon size={16} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>
      <div className="reference-list">
        {activeTab === "foreshadow"
          ? foreshadows.map((item) => (
              <article key={item.id}>
                <span className={`dot ${item.status}`} />
                <div><strong>{item.setup_text}</strong><small>{item.status}</small></div>
              </article>
            ))
          : activeTab === "style"
            ? styles.map((item) => (
                <article key={item.id}>
                  <Sparkles size={15} />
                  <div><strong>{item.name}</strong><small>{item.rule_text}</small></div>
                </article>
              ))
            : filtered.map((entity) => (
                <article key={entity.id}>
                  {activeTab === "character" ? <UsersRound size={15} /> : activeTab === "location" ? <MapPin size={15} /> : <Package size={15} />}
                  <div><strong>{entity.name}</strong><small>{entity.description || "暂无描述"}</small></div>
                </article>
              ))}
        {((activeTab === "foreshadow" && foreshadows.length === 0) ||
          (activeTab === "style" && styles.length === 0) ||
          (!["foreshadow", "style"].includes(activeTab) && filtered.length === 0)) ? (
          <p className="muted">该分类暂无资料。</p>
        ) : null}
      </div>
    </aside>
  );
}

function moveItem<T>(items: T[], from: number, to: number): T[] {
  const copy = [...items];
  const [item] = copy.splice(from, 1);
  copy.splice(to, 0, item);
  return copy;
}

function countWords(text: string): number {
  const chinese = text.match(/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g)?.length ?? 0;
  const words = text.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g)?.length ?? 0;
  return chinese + words;
}

function stripHtml(value: string): string {
  if (!value.includes("<")) return value;
  const document = new DOMParser().parseFromString(value, "text/html");
  return document.body.textContent ?? "";
}
