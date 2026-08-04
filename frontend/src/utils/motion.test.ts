import { describe, expect, it } from "vitest";
import * as motion from "./motion";

describe("motion constants", () => {
  it("routeTransition 必须是 180ms 垂直滑动", () => {
    expect(motion.routeTransition.initial).toEqual({ x: 20, opacity: 0 });
    expect(motion.routeTransition.animate).toEqual({ x: 0, opacity: 1 });
    expect(motion.routeTransition.exit).toEqual({ x: -20, opacity: 0 });
    expect(motion.routeTransition.transition.duration).toBe(0.18);
  });

  it("staggerContainer 必须是 50ms 间隔", () => {
    expect(motion.staggerContainer.animate.transition.staggerChildren).toBe(0.05);
  });

  it("fadeInUp 必须是 150ms y: 8→0", () => {
    expect(motion.fadeInUp.initial).toEqual({ opacity: 0, y: 8 });
    expect(motion.fadeInUp.animate.y).toBe(0);
    expect(motion.fadeInUp.animate.transition.duration).toBe(0.15);
  });

  it("dialogCard 必须是 scale 0.96→1", () => {
    expect(motion.dialogCard.initial.scale).toBe(0.96);
    expect(motion.dialogCard.animate.scale).toBe(1);
    expect(motion.dialogCard.transition.duration).toBe(0.18);
  });

  it("MAX_STAGGER_ITEMS 必须是 8", () => {
    expect(motion.MAX_STAGGER_ITEMS).toBe(8);
  });
});
