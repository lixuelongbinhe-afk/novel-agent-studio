import { expect, test } from "@playwright/test";

test("route, list, dialog, and button motion run in the browser", async ({ page }) => {
  const createdProjectIds: number[] = [];
  try {
    await page.addInitScript(() => localStorage.setItem("onboarding-done", "true"));
    for (let index = 1; index <= 9; index += 1) {
      const response = await page.request.post("/api/studio/projects", {
        data: { title: `动画项目 ${index}`, idea: `用于验证第 ${index} 个列表项的入场行为。` }
      });
      expect(response.ok()).toBe(true);
      const overview = await response.json() as { project: { id: number } };
      createdProjectIds.push(overview.project.id);
    }

    let releaseProjects: () => void = () => {};
    const projectGate = new Promise<void>((resolve) => {
      releaseProjects = resolve;
    });
    await page.route("**/api/studio/projects", async (route) => {
      await projectGate;
      await route.continue();
    });
    await page.goto("/");

    const listSamplesPromise = page.evaluate(() => new Promise<Array<Array<string | null>>>((resolve) => {
      const samples: Array<Array<string | null>> = [];
      const capture = () => {
        const rows = Array.from(document.querySelectorAll(".project-row"));
        if (rows.length > 0) samples.push(rows.map((row) => row.getAttribute("style")));
      };
      const observer = new MutationObserver(capture);
      observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["style"] });
      window.setTimeout(() => {
        observer.disconnect();
        resolve(samples);
      }, 700);
    }));
    releaseProjects();
    await page.getByRole("button", { name: "全部项目" }).click();
    const listSamples = await listSamplesPromise;
    await page.unroute("**/api/studio/projects");
    await expect(page.locator(".project-row")).toHaveCount(9);
    expect(listSamples.some((sample) => (
      sample.length === 9 &&
      sample.slice(0, 8).some((style) => style?.includes("opacity")) &&
      sample[8] === null
    ))).toBe(true);

    const routeSamplesPromise = page.evaluate(() => new Promise<string[]>((resolve) => {
      const samples: string[] = [];
      const capture = () => {
        document.querySelectorAll(".route-motion").forEach((element) => {
          const style = element.getAttribute("style");
          if (style) samples.push(style);
        });
      };
      const observer = new MutationObserver(capture);
      observer.observe(document.querySelector(".nas-main")!, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["style"]
      });
      window.setTimeout(() => {
        observer.disconnect();
        resolve(samples);
      }, 500);
    }));
    await page.getByTitle("模型与 API").click();
    const routeSamples = await routeSamplesPromise;
    await expect(page.getByRole("heading", { name: "模型与 API" })).toBeVisible();
    expect(routeSamples.some((style) => style.includes("opacity") && !style.includes("opacity: 1"))).toBe(true);

    const addService = page.getByRole("button", { name: "添加服务" });
    const box = await addService.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    expect(await addService.evaluate((element) => getComputedStyle(element).transform)).toContain("matrix");

    const dialogSamplesPromise = page.evaluate(() => new Promise<string[]>((resolve) => {
      const samples: string[] = [];
      const capture = () => {
        const dialog = document.querySelector(".dialog.provider-dialog");
        const style = dialog?.getAttribute("style");
        if (style) samples.push(style);
      };
      const observer = new MutationObserver(capture);
      observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["style"] });
      window.setTimeout(() => {
        observer.disconnect();
        resolve(samples);
      }, 500);
    }));
    await page.mouse.up();
    const dialogSamples = await dialogSamplesPromise;
    await expect(page.locator(".dialog.provider-dialog")).toBeVisible();
    expect(dialogSamples.some((style) => style.includes("scale") && !style.includes("scale(1)"))).toBe(true);
  } finally {
    for (const projectId of createdProjectIds) {
      await page.request.delete(`/api/studio/projects/${projectId}`);
    }
  }
});
