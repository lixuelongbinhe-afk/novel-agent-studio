import { expect, test } from "@playwright/test";

test("V2 creation flow renders and generates a review item", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  await expect(page.getByText("Novel Agent Studio", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "项目" })).toBeVisible();
  await expect(page.getByText("创作阶段")).toBeVisible();
  await expect(page.getByText("完成字数")).toBeVisible();
  await expect(page.getByText("待审核")).toBeVisible();
  await expect(page.getByRole("button", { name: /新建第一本小说/ })).toBeVisible();
  await page.screenshot({ path: "test-results/v2-home-desktop.png" });

  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByLabel("书名").fill("雾港回声");
  await page.getByLabel("题材与创意").fill("一名港口档案员在漫长雨季调查失踪渡轮，并逐渐发现家族旧案。");
  await page.getByRole("button", { name: "填写详细设置" }).click();
  await page.getByLabel("题材", { exact: true }).fill("悬疑");
  await page.getByLabel("主题", { exact: true }).fill("记忆与真相");
  await page.getByLabel("文风", { exact: true }).fill("冷静克制，重视环境细节，第三人称限知。");
  await page.getByRole("button", { name: "创建项目" }).click();

  await expect(page.getByRole("heading", { name: "雾港回声" })).toBeVisible();
  await expect(page.getByText("创意简报", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /世界观与风格/ }).click();
  await expect(page.getByRole("heading", { name: "世界观与风格" })).toBeVisible();
  await expect(page.getByRole("option", { name: "手动" })).toBeAttached();
  await expect(page.getByRole("option", { name: "自动" })).toBeAttached();
  await expect(page.getByRole("option", { name: "倒计时" })).toBeAttached();
  await expect(page.getByText("提取参考文风")).toBeVisible();
  await page.screenshot({ path: "test-results/v2-studio-desktop.png" });

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: /开始生成/ }).click();
  await expect(page.getByRole("heading", { name: "世界观架构师", exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "规则审校员", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "定位与主题策划", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "文风与边界编辑", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "通过" }).first()).toBeVisible();
  await page.screenshot({ path: "test-results/v2-review-desktop.png" });

  await page.locator("label.file-action input").setInputFiles("src/test/e2e/fixtures/author-style.md");
  await expect(page.getByRole("heading", { name: "参考文风分析 · author-style.md" })).toBeVisible({ timeout: 30_000 });

  await page.locator(".artifact-card").first().getByTitle("编辑").click();
  await expect(page.getByRole("heading", { name: "审核、批注与版本比较" })).toBeVisible();
  await expect(page.getByText("审核批注")).toBeVisible();
  await expect(page.getByText("历史版本")).toBeVisible();
  await page.screenshot({ path: "test-results/v2-version-compare.png" });
  await page.getByRole("button", { name: "关闭审核编辑器" }).click();

  await page.getByTitle("审核").click();
  for (const title of ["定位与主题策划", "世界观架构师", "规则审校员", "文风与边界编辑", "参考文风分析 · author-style.md"]) {
    const item = page.locator(".review-item").filter({ hasText: title });
    if (await item.count()) {
      await item.getByTitle("通过").click();
      await expect(item).toHaveCount(0);
    }
  }
  await expect(page.locator(".project-heading")).toContainText("人物与关系");

  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(desktopOverflow).toBe(false);

  await page.setViewportSize({ width: 1024, height: 720 });
  await expect(page.getByRole("heading", { name: "雾港回声" })).toBeVisible();
  const studioCompactOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(studioCompactOverflow).toBe(false);
  await page.screenshot({ path: "test-results/v2-studio-compact.png" });

  await page.getByRole("link", { name: "模型与 API" }).click();
  await expect(page.getByRole("heading", { name: "模型与 API" })).toBeVisible();
  await expect(page.getByText(/Windows 凭据管理器/)).toBeVisible();
  await page.getByRole("button", { name: "添加服务" }).click();
  await expect(page.getByRole("heading", { name: "添加模型服务" })).toBeVisible();
  await expect(page.getByRole("button", { name: "DeepSeek", exact: true })).toHaveClass(/selected/);
  await expect(page.getByLabel("API 地址")).toHaveValue("https://api.deepseek.com/v1");
  await expect(page.getByLabel("模型名称")).toHaveValue("deepseek-chat");
  await page.screenshot({ path: "test-results/v2-deepseek-dialog.png" });
  await page.getByRole("button", { name: "取消" }).click();

  await expect(page.getByRole("heading", { name: "模型与 API" })).toBeVisible();
  const compactOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(compactOverflow).toBe(false);
  await page.screenshot({ path: "test-results/v2-models-compact.png" });
});

test("approved Agent draft is written into the chapter editor", async ({ page }) => {
  const api = "http://127.0.0.1:8010/api/studio";
  const createdResponse = await page.request.post(`${api}/projects`, {
    data: {
      title: "审核写回验证",
      idea: "验证 Agent 正文通过审核后写入章节。",
      entry_mode: "outline",
      target_words: 10_000,
      genre: "悬疑",
      chapter_count: 1,
      chapter_words: 2_000
    }
  });
  expect(createdResponse.ok()).toBe(true);
  const created = await createdResponse.json();
  const projectId = created.project.id as number;

  const importResponse = await page.request.post(`${api}/projects/${projectId}/outline/import`, {
    data: { text: "# 第一卷\n## 第一章 深渊之下", replace_existing: true }
  });
  expect(importResponse.ok()).toBe(true);
  const overviewResponse = await page.request.get(`${api}/projects/${projectId}`);
  const overview = await overviewResponse.json();
  const chapterId = overview.tree.chapters[0].id as number;
  const generateResponse = await page.request.post(`${api}/projects/${projectId}/generate/drafting`, {
    data: { chapter_id: chapterId, mode: "new", use_demo_model: true }
  });
  expect(generateResponse.ok()).toBe(true);

  await page.goto(`/studio/${projectId}`);
  await page.getByTitle("审核").click();
  const reviewItem = page.locator(".review-item").first();
  await expect(reviewItem.getByRole("button", { name: "通过并写入正文" })).toBeVisible();
  await reviewItem.getByRole("button", { name: "通过并写入正文" }).click();

  const editor = page.getByPlaceholder("正文");
  await expect(editor).not.toHaveValue("");
  await expect(page.getByText(/正文已通过并写入章节/)).toBeVisible();
  await page.screenshot({ path: "test-results/v2-draft-writeback.png" });
});
