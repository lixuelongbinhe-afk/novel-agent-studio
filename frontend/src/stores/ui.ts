import { create } from "zustand";
import { persist } from "zustand/middleware";

type UiState = {
  selectedProjectId: number | null;
  selectedChapterId: number | null;
  selectedSceneId: number | null;
  sidebarCollapsed: boolean;
  rightPanelOpen: boolean;
  theme: "light" | "dark";
  setProject: (id: number | null) => void;
  setChapter: (id: number) => void;
  setScene: (id: number | null) => void;
  toggleSidebar: () => void;
  toggleRightPanel: () => void;
  toggleTheme: () => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      selectedProjectId: null,
      selectedChapterId: null,
      selectedSceneId: null,
      sidebarCollapsed: false,
      rightPanelOpen: true,
      theme: "dark",
      setProject: (id) => set({ selectedProjectId: id, selectedChapterId: null, selectedSceneId: null }),
      setChapter: (id) => set({ selectedChapterId: id, selectedSceneId: null }),
      setScene: (id) => set({ selectedSceneId: id }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      toggleRightPanel: () => set((state) => ({ rightPanelOpen: !state.rightPanelOpen })),
      toggleTheme: () => set({ theme: "dark" })
    }),
    {
      name: "novel-agent-studio-ui",
      partialize: (state) => ({
        selectedProjectId: state.selectedProjectId,
        sidebarCollapsed: state.sidebarCollapsed,
        rightPanelOpen: state.rightPanelOpen,
        theme: state.theme
      })
    }
  )
);
