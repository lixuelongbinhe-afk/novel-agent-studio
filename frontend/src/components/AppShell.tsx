import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArchiveRestore,
  BookOpenText,
  Boxes,
  Braces,
  BrainCircuit,
  ChevronLeft,
  CircleDot,
  FolderKanban,
  Library,
  PackageCheck,
  PanelLeftClose,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow
} from "lucide-react";
import { NavLink, useLocation, useOutlet } from "react-router-dom";
import { studioApi } from "../api/studio";
import { useUiStore } from "../stores/ui";
import { routeTransition } from "../utils/motion";
import { OnboardingWizard } from "./OnboardingWizard";

export function AppShell() {
  const location = useLocation();
  const outlet = useOutlet();
  const { data: projects = [] } = useQuery({ queryKey: ["studio-projects"], queryFn: studioApi.dashboard });
  const { data: providers = [], isSuccess: providersLoaded } = useQuery({
    queryKey: ["studio-providers"],
    queryFn: studioApi.providers
  });
  const [showWizard, setShowWizard] = useState(false);
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const setProject = useUiStore((state) => state.setProject);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const current = projects.find((project) => project.id === selectedProjectId) ?? projects[0];

  useEffect(() => {
    if (!selectedProjectId && projects[0]) setProject(projects[0].id);
  }, [projects, selectedProjectId, setProject]);

  useEffect(() => {
    if (
      providersLoaded &&
      !localStorage.getItem("onboarding-done") &&
      providers.length === 0
    ) {
      setShowWizard(true);
    }
  }, [providers.length, providersLoaded]);

  const studioPath = current ? `/studio/${current.id}` : "/";
  const studioActive = location.pathname.startsWith("/studio/");

  return (
    <div className={`nas-shell ${sidebarCollapsed ? "is-collapsed" : ""}`}>
      <AnimatePresence>
        {showWizard ? <OnboardingWizard onComplete={() => setShowWizard(false)} /> : null}
      </AnimatePresence>
      <aside className="nas-sidebar">
        <div className="nas-brand">
          {sidebarCollapsed ? (
            <button type="button" className="nas-brand-mark" onClick={toggleSidebar} title="展开侧栏">
              <Sparkles size={16} />
            </button>
          ) : (
            <>
              <span className="nas-brand-mark"><Sparkles size={16} /></span>
              <strong>Novel Agent Studio</strong>
              <button type="button" className="icon-button subtle" onClick={toggleSidebar} title="收起侧栏">
                <PanelLeftClose size={16} />
              </button>
            </>
          )}
        </div>

        <nav className="primary-nav" aria-label="主导航">
          <NavLink to="/" end title="项目">
            <FolderKanban size={17} />
            {!sidebarCollapsed ? <span>项目</span> : null}
          </NavLink>
          <NavLink to={studioPath} className={studioActive ? "active" : ""} title="创作流程">
            <BookOpenText size={17} />
            {!sidebarCollapsed ? <span>创作流程</span> : null}
          </NavLink>
          <NavLink to="/workspace" title="写作台">
            <Boxes size={17} />
            {!sidebarCollapsed ? <span>写作台</span> : null}
          </NavLink>
          <NavLink to="/approvals" title="待审核">
            <ShieldCheck size={17} />
            {!sidebarCollapsed ? <span>待审核</span> : null}
          </NavLink>
          <NavLink to="/workflows" title="Agent 工作流">
            <Workflow size={17} />
            {!sidebarCollapsed ? <span>Agent 工作流</span> : null}
          </NavLink>
          {!sidebarCollapsed ? <span className="nav-section-label">配置与数据</span> : null}
          <NavLink to="/models" title="模型与 API">
            <Settings2 size={17} />
            {!sidebarCollapsed ? <span>模型与 API</span> : null}
          </NavLink>
          <NavLink to="/advanced-api" title="自定义 HTTP">
            <Braces size={17} />
            {!sidebarCollapsed ? <span>自定义 HTTP</span> : null}
          </NavLink>
          <NavLink to="/model-center" title="高级模型中心">
            <Settings2 size={17} />
            {!sidebarCollapsed ? <span>高级模型中心</span> : null}
          </NavLink>
          <NavLink to="/context" title="上下文">
            <BrainCircuit size={17} />
            {!sidebarCollapsed ? <span>上下文</span> : null}
          </NavLink>
          <NavLink to="/library" title="资料库">
            <Library size={17} />
            {!sidebarCollapsed ? <span>资料库</span> : null}
          </NavLink>
          <NavLink to="/recovery" title="恢复">
            <ArchiveRestore size={17} />
            {!sidebarCollapsed ? <span>恢复</span> : null}
          </NavLink>
          <NavLink to="/release" title="发布与备份">
            <PackageCheck size={17} />
            {!sidebarCollapsed ? <span>发布与备份</span> : null}
          </NavLink>
        </nav>

        {!sidebarCollapsed ? (
          <div className="sidebar-projects">
            <span className="nav-caption">最近项目</span>
            {projects.slice(0, 6).map((project) => (
              <NavLink
                key={project.id}
                to={`/studio/${project.id}`}
                onClick={() => setProject(project.id)}
                className="project-nav-item"
              >
                <CircleDot size={11} />
                <span>{project.title}</span>
                {project.pending_reviews ? <b>{project.pending_reviews}</b> : null}
              </NavLink>
            ))}
          </div>
        ) : null}

        <div className="sidebar-status">
          {sidebarCollapsed ? (
            <button type="button" className="sidebar-status-expand" onClick={toggleSidebar} title="展开侧栏">
              <span className="status-light" />
            </button>
          ) : (
            <>
              <span className="status-light" />
              <span>本地数据已连接</span>
              <button type="button" className="icon-button subtle" onClick={toggleSidebar} title="收起侧栏">
                <ChevronLeft size={15} />
              </button>
            </>
          )}
        </div>
      </aside>

      <div className="nas-content">
        <header className="nas-topbar">
          <div className="topbar-title">
            <span>{current?.title ?? "小说智能体工作室"}</span>
            {current ? <small>{current.stage_label}</small> : null}
          </div>
          <div className="topbar-meta">
            {current ? <span>{current.completed_words.toLocaleString()} 字</span> : null}
            {current?.pending_reviews ? <span className="attention">{current.pending_reviews} 项待审核</span> : null}
          </div>
        </header>
        <main className="nas-main">
          <AnimatePresence mode="wait">
            <motion.div className="route-motion" key={location.pathname} {...routeTransition}>
              {outlet}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
