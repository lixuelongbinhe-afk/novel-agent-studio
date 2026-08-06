import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArchiveRestore,
  BadgeCheck,
  BookOpenText,
  Boxes,
  Braces,
  BrainCircuit,
  ChevronLeft,
  CircleDot,
  Command,
  FolderKanban,
  Library,
  Maximize2,
  Moon,
  PackageCheck,
  PanelLeftClose,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sun,
  Workflow
} from "lucide-react";
import { NavLink, useLocation, useOutlet } from "react-router-dom";
import { studioApi } from "../api/studio";
import { useUiStore } from "../stores/ui";
import { dialogBackdrop, dialogCard, routeTransition } from "../utils/motion";
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
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const setProject = useUiStore((state) => state.setProject);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const theme = useUiStore((state) => state.theme);
  const toggleTheme = useUiStore((state) => state.toggleTheme);
  const focusMode = useUiStore((state) => state.focusMode);
  const toggleFocusMode = useUiStore((state) => state.toggleFocusMode);
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

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((value) => !value);
      }
      if (event.key === "Escape") setCommandOpen(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const studioPath = current ? `/studio/${current.id}` : "/";
  const studioActive = location.pathname.startsWith("/studio/");
  const commandEntries = [
    { to: "/", label: "项目", detail: "查看与继续最近小说" },
    { to: studioPath, label: "创作流程", detail: "打开当前小说工作台" },
    { to: "/workspace", label: "写作台", detail: "卷章与场景编辑" },
    { to: "/approvals", label: "待审核", detail: "Diff、批注与写回" },
    { to: "/workflows", label: "Agent 工作流", detail: "运行与事件记录" },
    { to: "/models", label: "模型与 API", detail: "服务、凭据与连接测试" },
    { to: "/library", label: "资料库", detail: "人物、地点、时间线与伏笔" }
  ].filter((entry) => `${entry.label}${entry.detail}`.toLowerCase().includes(commandQuery.trim().toLowerCase()));

  return (
    <div className={`nas-shell ${sidebarCollapsed ? "is-collapsed" : ""} ${focusMode ? "is-focus-mode" : ""}`}>
      <AnimatePresence>
        {showWizard ? <OnboardingWizard onComplete={() => setShowWizard(false)} /> : null}
        {commandOpen ? (
          <motion.div className="command-backdrop" role="presentation" onMouseDown={() => setCommandOpen(false)} {...dialogBackdrop}>
            <motion.section className="command-palette" role="dialog" aria-modal="true" aria-label="命令面板" onMouseDown={(event) => event.stopPropagation()} {...dialogCard}>
              <header>
                <Search size={17} />
                <input autoFocus value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} placeholder="搜索页面或命令" />
                <kbd>Esc</kbd>
              </header>
              <div className="command-results">
                {commandEntries.map((entry) => (
                  <NavLink key={entry.label} to={entry.to} onClick={() => { setCommandOpen(false); setCommandQuery(""); }}>
                    <Command size={15} /><span><strong>{entry.label}</strong><small>{entry.detail}</small></span>
                  </NavLink>
                ))}
                {commandEntries.length === 0 ? <div className="command-empty">没有匹配命令</div> : null}
              </div>
            </motion.section>
          </motion.div>
        ) : null}
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
          <button type="button" className="command-trigger" onClick={() => setCommandOpen(true)}>
            <Search size={14} /><span>搜索命令或快速打开...</span><kbd>Ctrl K</kbd>
          </button>
          <div className="topbar-meta">
            <span className="autosave-state"><BadgeCheck size={14} />已自动保存</span>
            {current ? <span>{current.completed_words.toLocaleString()} 字</span> : null}
            {current?.pending_reviews ? <span className="attention">{current.pending_reviews} 项待审核</span> : null}
            {studioActive ? <button type="button" className={focusMode ? "topbar-icon active" : "topbar-icon"} onClick={toggleFocusMode} title="专注模式"><Maximize2 size={15} /></button> : null}
            <button type="button" className="topbar-icon" onClick={toggleTheme} title={theme === "dark" ? "切换浅色主题" : "切换深色主题"}>
              {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            </button>
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
