/**
 * Apple-style animation constants
 * Mixed durations: micro-interactions 150ms, page transitions 300ms, modals 350ms
 */

/** Route transition: subtle fade */
export const routeTransition = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] }
} as const;

/** List container: stagger children */
export const staggerContainer = {
  animate: {
    transition: {
      staggerChildren: 0.04
    }
  }
} as const;

/** List item: fade in */
export const fadeInUp = {
  initial: { opacity: 0 },
  animate: {
    opacity: 1,
    transition: { duration: 0.15, ease: [0.22, 1, 0.36, 1] }
  }
} as const;

/** Dialog backdrop */
export const dialogBackdrop = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] }
} as const;

/** Dialog card: gentle scale */
export const dialogCard = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.96 },
  transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] }
} as const;

/** Stage transition: fade */
export const stageTransition = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] }
} as const;

/** Inspector transition: slide */
export const inspectorTransition = {
  open: { opacity: 1, x: 0, transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] } },
  closed: { opacity: 0, x: 24, transition: { duration: 0.25, ease: [0.22, 1, 0.36, 1] } }
} as const;

/** Console transition: expand */
export const consoleTransition = {
  initial: { height: 0, opacity: 0 },
  animate: { height: "auto", opacity: 1 },
  exit: { height: 0, opacity: 0 },
  transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] }
} as const;

/** Toast transition: gentle drop */
export const toastTransition = {
  initial: { opacity: 0, y: -8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
  transition: { duration: 0.25, ease: [0.22, 1, 0.36, 1] }
} as const;

/** Max stagger items for long lists */
export const MAX_STAGGER_ITEMS = 8;
