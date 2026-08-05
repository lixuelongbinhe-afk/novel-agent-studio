/**
 * 动画常量：Apple 风格的空间过渡，150-280ms，无 blur。
 * 修改此文件时必须同步更新 motion.test.ts 的断言。
 */

/** 路由切换：工作区内容轻微上浮，避免左右方向暗示错误层级。 */
export const routeTransition = {
  initial: { y: 4, opacity: 0 },
  animate: { y: 0, opacity: 1 },
  exit: { y: -2, opacity: 0 },
  transition: { duration: 0.16, ease: "easeOut" }
} as const;

/** 列表容器：错峰子项 */
export const staggerContainer = {
  animate: {
    transition: {
      staggerChildren: 0.035
    }
  }
} as const;

/** 列表项：淡入 + 轻微上移 */
export const fadeInUp = {
  initial: { opacity: 0, y: 5 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.16, ease: "easeOut" }
  }
} as const;

/** Dialog 背景 */
export const dialogBackdrop = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.15 }
} as const;

/** Dialog 卡片：从触发层上方轻微浮起。 */
export const dialogCard = {
  initial: { opacity: 0, scale: 0.985, y: 6 },
  animate: { opacity: 1, scale: 1, y: 0 },
  exit: { opacity: 0, scale: 0.985, y: 4 },
  transition: { duration: 0.18, ease: "easeOut" }
} as const;

/** 创作阶段切换：旧内容轻退，新内容从下方进入。 */
export const stageTransition = {
  initial: { opacity: 0, y: 10, scale: 0.995 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: -6, scale: 0.998 },
  transition: { duration: 0.24, ease: [0.22, 1, 0.36, 1] }
} as const;

/** 右侧审阅栏：保留布局宽度，同时沿空间层级滑入。 */
export const inspectorTransition = {
  open: { opacity: 1, x: 0, scale: 1, transition: { duration: 0.26, ease: [0.22, 1, 0.36, 1] } },
  closed: { opacity: 0, x: 28, scale: 0.985, transition: { duration: 0.22, ease: [0.22, 1, 0.36, 1] } }
} as const;

/** Agent 控制台：高度与内容一起展开。 */
export const consoleTransition = {
  initial: { height: 0, opacity: 0 },
  animate: { height: "auto", opacity: 1 },
  exit: { height: 0, opacity: 0 },
  transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] }
} as const;

/** 浮层提示：从顶部轻微落下。 */
export const toastTransition = {
  initial: { opacity: 0, y: -10, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: -6, scale: 0.985 },
  transition: { duration: 0.2, ease: [0.22, 1, 0.36, 1] }
} as const;

/** 最大错峰动画项数（长列表超过此数的项直接出现） */
export const MAX_STAGGER_ITEMS = 8;
