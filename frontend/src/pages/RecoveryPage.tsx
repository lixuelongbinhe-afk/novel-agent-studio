import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, BookOpen, RotateCcw } from "lucide-react";
import { api, Project, ProjectTrash, TrashItem } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { useUiStore } from "../stores/ui";

const resourceMeta: Array<{
  key: keyof ProjectTrash;
  resource: string;
  label: string;
}> = [
  { key: "volumes", resource: "volume", label: "卷" },
  { key: "chapters", resource: "chapter", label: "章节" },
  { key: "scenes", resource: "scene", label: "场景" },
  { key: "entities", resource: "entity", label: "资料条目" },
  { key: "aliases", resource: "alias", label: "别名" },
  { key: "relations", resource: "relation", label: "人物关系" },
  { key: "state_changes", resource: "state-change", label: "状态变更" },
  { key: "timeline", resource: "timeline", label: "时间线事件" },
  { key: "foreshadows", resource: "foreshadow", label: "伏笔" },
  { key: "style_guides", resource: "style-guide", label: "风格规则" }
];

export function RecoveryPage() {
  const queryClient = useQueryClient();
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const setProject = useUiStore((state) => state.setProject);
  const { data: activeProjects = [] } = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const { data: deletedProjects = [], error: deletedError } = useQuery({
    queryKey: ["projects", "deleted"],
    queryFn: () => api.listProjects(true)
  });
  const projectId = selectedProjectId ?? activeProjects[0]?.id;
  const { data: trash, error: trashError } = useQuery({
    queryKey: ["trash", projectId],
    queryFn: () => api.projectTrash(projectId!),
    enabled: Boolean(projectId)
  });

  const restore = useMutation({
    mutationFn: ({ resource, record }: { resource: string; record: TrashItem | Project }) =>
      api.restoreRecord(resource, record),
    onSuccess: async (_, input) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({ queryKey: ["projects", "deleted"] });
      await queryClient.invalidateQueries({ queryKey: ["trash"] });
      if (input.resource === "project") setProject(input.record.id);
    }
  });

  const itemGroups = resourceMeta
    .map((meta) => ({ ...meta, items: trash?.[meta.key] ?? [] }))
    .filter((group) => group.items.length > 0);
  const nothingDeleted = deletedProjects.length === 0 && itemGroups.length === 0;

  return (
    <section className="page-stack recovery-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">数据恢复</span>
          <h1>回收站</h1>
          <p>恢复误删的项目、章节和资料记录。恢复操作保留原有编号与修订历史。</p>
        </div>
      </header>

      {deletedError || trashError ? <ErrorNotice message="无法读取回收站，请检查本地服务。" /> : null}

      {nothingDeleted ? (
        <EmptyState icon={ArchiveRestore} title="回收站是空的" description="被删除的项目和资料会安全保留在这里。" />
      ) : (
        <div className="recovery-groups">
          {deletedProjects.length > 0 ? (
            <section className="recovery-group">
              <header><BookOpen size={18} /><div><h2>已删除项目</h2><span>{deletedProjects.length} 项</span></div></header>
              <div className="recovery-list">
                {deletedProjects.map((project) => (
                  <RecoveryRow
                    key={project.id}
                    label={project.title}
                    type="小说项目"
                    deletedAt={project.deleted_at}
                    busy={restore.isPending}
                    onRestore={() => restore.mutate({ resource: "project", record: project })}
                  />
                ))}
              </div>
            </section>
          ) : null}

          {itemGroups.map((group) => (
            <section className="recovery-group" key={group.key}>
              <header><ArchiveRestore size={18} /><div><h2>{group.label}</h2><span>{group.items.length} 项</span></div></header>
              <div className="recovery-list">
                {group.items.map((item) => (
                  <RecoveryRow
                    key={item.id}
                    label={item.label}
                    type={group.label}
                    deletedAt={item.deleted_at}
                    busy={restore.isPending}
                    onRestore={() => restore.mutate({ resource: group.resource, record: item })}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function RecoveryRow({
  label,
  type,
  deletedAt,
  busy,
  onRestore
}: {
  label: string;
  type: string;
  deletedAt: string | null;
  busy: boolean;
  onRestore: () => void;
}) {
  return (
    <article>
      <div>
        <strong>{label}</strong>
        <span>{type}{deletedAt ? ` · 删除于 ${new Date(deletedAt).toLocaleString()}` : ""}</span>
      </div>
      <button className="secondary-button" type="button" onClick={onRestore} disabled={busy}>
        <RotateCcw size={16} />
        恢复
      </button>
    </article>
  );
}
