/**
 * 动画常量：克制的机械感，120-200ms，无 spring/blur。
 * 修改此文件时必须同步更新 motion.test.ts 的断言。
 */

/** 路由切换：垂直滑入 */
export const routeTransition = {
  initial: { x: 20, opacity: 0 },
  animate: { x: 0, opacity: 1 },
  exit: { x: -20, opacity: 0 },
  transition: { duration: 0.18, ease: "easeOut" }
} as const;

/** 列表容器：错峰子项 */
export const staggerContainer = {
  animate: {
    transition: {
      staggerChildren: 0.05
    }
  }
} as const;

/** 列表项：淡入 + 轻微上移 */
export const fadeInUp = {
  initial: { opacity: 0, y: 8 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.15, ease: "easeOut" }
  }
} as const;

/** Dialog 背景 */
export const dialogBackdrop = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.15 }
} as const;

/** Dialog 卡片：微小 scale */
export const dialogCard = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.96 },
  transition: { duration: 0.18, ease: "easeOut" }
} as const;

/** 最大错峰动画项数（长列表超过此数的项直接出现） */
export const MAX_STAGGER_ITEMS = 8;
