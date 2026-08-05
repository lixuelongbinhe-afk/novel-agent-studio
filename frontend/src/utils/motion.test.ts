import { describe, expect, it } from "vitest";
import * as motion from "./motion";

describe("motion constants", () => {
  it("routeTransition 必须是 160ms 的轻微上浮", () => {
    expect(motion.routeTransition.initial).toEqual({ y: 4, opacity: 0 });
    expect(motion.routeTransition.animate).toEqual({ y: 0, opacity: 1 });
    expect(motion.routeTransition.exit).toEqual({ y: -2, opacity: 0 });
    expect(motion.routeTransition.transition.duration).toBe(0.16);
  });

  it("staggerContainer 必须是 35ms 间隔", () => {
    expect(motion.staggerContainer.animate.transition.staggerChildren).toBe(0.035);
  });

  it("fadeInUp 必须是 160ms y: 5→0", () => {
    expect(motion.fadeInUp.initial).toEqual({ opacity: 0, y: 5 });
    expect(motion.fadeInUp.animate.y).toBe(0);
    expect(motion.fadeInUp.animate.transition.duration).toBe(0.16);
  });

  it("dialogCard 必须是 scale 0.985→1", () => {
    expect(motion.dialogCard.initial.scale).toBe(0.985);
    expect(motion.dialogCard.animate.scale).toBe(1);
    expect(motion.dialogCard.transition.duration).toBe(0.18);
  });

  it("stageTransition 必须提供阶段空间切换", () => {
    expect(motion.stageTransition.initial).toEqual({ opacity: 0, y: 10, scale: 0.995 });
    expect(motion.stageTransition.exit.y).toBe(-6);
    expect(motion.stageTransition.transition.duration).toBe(0.24);
  });

  it("inspectorTransition 必须从右侧滑入", () => {
    expect(motion.inspectorTransition.closed.x).toBe(28);
    expect(motion.inspectorTransition.open.x).toBe(0);
    expect(motion.inspectorTransition.open.transition.duration).toBe(0.26);
  });

  it("consoleTransition 必须支持高度展开", () => {
    expect(motion.consoleTransition.initial.height).toBe(0);
    expect(motion.consoleTransition.animate.height).toBe("auto");
    expect(motion.consoleTransition.transition.duration).toBe(0.28);
  });

  it("toastTransition 必须从顶部轻落", () => {
    expect(motion.toastTransition.initial.y).toBe(-10);
    expect(motion.toastTransition.animate.y).toBe(0);
    expect(motion.toastTransition.transition.duration).toBe(0.2);
  });

  it("MAX_STAGGER_ITEMS 必须是 8", () => {
    expect(motion.MAX_STAGGER_ITEMS).toBe(8);
  });
});
