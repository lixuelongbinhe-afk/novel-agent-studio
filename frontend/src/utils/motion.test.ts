import { describe, expect, it } from "vitest";
import * as motion from "./motion";

describe("motion constants", () => {
  it("routeTransition 必须是 300ms 的淡入淡出", () => {
    expect(motion.routeTransition.initial).toEqual({ opacity: 0 });
    expect(motion.routeTransition.animate).toEqual({ opacity: 1 });
    expect(motion.routeTransition.exit).toEqual({ opacity: 0 });
    expect(motion.routeTransition.transition.duration).toBe(0.3);
  });

  it("staggerContainer 必须是 40ms 间隔", () => {
    expect(motion.staggerContainer.animate.transition.staggerChildren).toBe(0.04);
  });

  it("fadeInUp 必须是 150ms 淡入", () => {
    expect(motion.fadeInUp.initial).toEqual({ opacity: 0 });
    expect(motion.fadeInUp.animate.opacity).toBe(1);
    expect(motion.fadeInUp.animate.transition.duration).toBe(0.15);
  });

  it("dialogCard 必须是 scale 0.96→1", () => {
    expect(motion.dialogCard.initial.scale).toBe(0.96);
    expect(motion.dialogCard.animate.scale).toBe(1);
    expect(motion.dialogCard.transition.duration).toBe(0.35);
  });

  it("stageTransition 必须提供阶段淡入淡出", () => {
    expect(motion.stageTransition.initial).toEqual({ opacity: 0 });
    expect(motion.stageTransition.exit.opacity).toBe(0);
    expect(motion.stageTransition.transition.duration).toBe(0.3);
  });

  it("inspectorTransition 必须从右侧滑入", () => {
    expect(motion.inspectorTransition.closed.x).toBe(24);
    expect(motion.inspectorTransition.open.x).toBe(0);
    expect(motion.inspectorTransition.open.transition.duration).toBe(0.3);
  });

  it("consoleTransition 必须支持高度展开", () => {
    expect(motion.consoleTransition.initial.height).toBe(0);
    expect(motion.consoleTransition.animate.height).toBe("auto");
    expect(motion.consoleTransition.transition.duration).toBe(0.3);
  });

  it("toastTransition 必须从顶部轻落", () => {
    expect(motion.toastTransition.initial.y).toBe(-8);
    expect(motion.toastTransition.animate.y).toBe(0);
    expect(motion.toastTransition.transition.duration).toBe(0.25);
  });

  it("MAX_STAGGER_ITEMS 必须是 8", () => {
    expect(motion.MAX_STAGGER_ITEMS).toBe(8);
  });
});
