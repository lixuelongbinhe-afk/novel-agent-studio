import { expect, test } from "@playwright/test";

test("collapsed sidebar keeps only in-bounds brand and status expand targets", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  await page.getByTitle("收起侧栏").first().click();
  const shell = page.locator(".nas-shell");
  const sidebar = page.locator(".nas-sidebar");
  const brand = page.locator("button.nas-brand-mark");
  const status = page.locator("button.sidebar-status-expand");
  await expect(shell).toHaveClass(/is-collapsed/);
  await expect(page.getByTitle("展开侧栏")).toHaveCount(2);

  const [sidebarBox, brandBox, statusBox] = await Promise.all([
    sidebar.boundingBox(),
    brand.boundingBox(),
    status.boundingBox()
  ]);
  expect(sidebarBox?.width).toBe(54);
  for (const box of [brandBox, statusBox]) {
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(sidebarBox!.x);
    expect(box!.x + box!.width).toBeLessThanOrEqual(sidebarBox!.x + sidebarBox!.width);
  }
  await page.screenshot({ path: "test-results/sidebar-collapsed.png" });

  await status.click();
  await expect(shell).not.toHaveClass(/is-collapsed/);
  await page.getByTitle("收起侧栏").first().click();
  await brand.click();
  await expect(shell).not.toHaveClass(/is-collapsed/);
});

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

  await page.reload();
  await expect(page.getByRole("button", { name: "生成全文审阅" })).toBeVisible();
  await expect(editor).not.toHaveValue("");
  await editor.fill("审阅阶段仍可修改并保存正文。");
  await page.getByTitle("保存").click();
  await expect(page.getByText("已保存")).toBeVisible();
  await expect(editor).toHaveValue("审阅阶段仍可修改并保存正文。");
  await page.screenshot({ path: "test-results/v2-review-editable.png" });
});

test("imports a half-finished novel into the reviewed continuation workflow", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: /导入半成品续写/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false);
  await page.screenshot({ path: "test-results/v2-continuation-import-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: /粘贴正文/ }).click();
  await page.getByLabel("新项目书名").fill("旧城来信");
  await page.getByLabel("小说正文").fill("# 第一卷 旧城\n## 第1章 雨夜\n雨落在石阶上。\n## 第2章 来信\n林舟拆开一封没有署名的信。");
  await page.getByLabel("目标总章节").fill("4");
  await page.getByLabel("目标总卷数").fill("2");
  await page.getByRole("button", { name: "导入并创建项目" }).click();

  await expect(page.getByRole("heading", { name: "旧城来信" })).toBeVisible();
  await expect(page.getByRole("button", { name: /资料审核/ })).toBeVisible();
  await page.getByRole("button", { name: /导入与解析/ }).click();
  await expect(page.getByRole("heading", { name: /原始只读副本/ })).toBeVisible();
  await expect(page.locator(".artifact-card").getByTitle("编辑")).toHaveCount(0);
  await page.screenshot({ path: "test-results/v2-continuation-original.png" });

  await page.getByRole("button", { name: /资料审核/ }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page.getByRole("heading", { name: "章节结构分析" })).toBeVisible({ timeout: 40_000 });
  await expect(page.getByRole("heading", { name: "原文文风档案" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "未完剧情线" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false);
  await page.screenshot({ path: "test-results/v2-continuation-analysis.png" });
});

test("long chat replies scroll inside the right rail without covering the workspace", async ({ page }) => {
  test.setTimeout(120_000);
  const api = "http://127.0.0.1:8010/api/studio";
  const createdResponse = await page.request.post(`${api}/projects`, {
    data: {
      title: "右栏滚动验证",
      idea: "验证大量对话不会撑破工作区。",
      entry_mode: "creative",
      target_words: 20_000,
      genre: "悬疑",
      chapter_count: 4,
      chapter_words: 2_000
    }
  });
  expect(createdResponse.ok()).toBe(true);
  const projectId = (await createdResponse.json()).project.id as number;
  for (let index = 0; index < 18; index += 1) {
    const response = await page.request.post(`${api}/projects/${projectId}/chat`, {
      data: {
        message: `请详细分析第 ${index + 1} 组人物关系、伏笔和时间线，并给出多段修改建议。`,
        stage: "world",
        use_demo_model: true
      }
    });
    expect(response.ok()).toBe(true);
  }

  await page.setViewportSize({ width: 1024, height: 720 });
  await page.goto(`/studio/${projectId}`);
  const rail = page.locator(".context-rail");
  const stream = page.locator(".chat-stream");
  const composer = page.locator(".chat-composer");
  await expect(composer).toBeVisible();
  const layout = await page.evaluate(() => {
    const railElement = document.querySelector<HTMLElement>(".context-rail")!;
    const streamElement = document.querySelector<HTMLElement>(".chat-stream")!;
    const composerElement = document.querySelector<HTMLElement>(".chat-composer")!;
    const railRect = railElement.getBoundingClientRect();
    const composerRect = composerElement.getBoundingClientRect();
    return {
      railBottom: railRect.bottom,
      composerBottom: composerRect.bottom,
      viewportHeight: window.innerHeight,
      streamScrollHeight: streamElement.scrollHeight,
      streamClientHeight: streamElement.clientHeight,
      pageOverflow: document.documentElement.scrollHeight > window.innerHeight + 1
    };
  });
  expect(layout.railBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.railBottom + 1);
  expect(layout.streamScrollHeight).toBeGreaterThan(layout.streamClientHeight);
  expect(layout.pageOverflow).toBe(false);
  await expect(rail).toBeVisible();
  await expect(stream).toBeVisible();
  await page.screenshot({ path: "test-results/v2-long-chat-scroll.png" });
});

test("deleting a project removes it from the persisted dashboard", async ({ page }) => {
  const api = "http://127.0.0.1:8010/api/studio";
  const title = `删除功能验证-${Date.now()}`;
  const createdResponse = await page.request.post(`${api}/projects`, {
    data: {
      title,
      idea: "验证首页删除按钮调用真实 API 并持久化删除结果。",
      entry_mode: "creative",
      target_words: 10_000,
      genre: "测试",
      chapter_count: 2,
      chapter_words: 2_000
    }
  });
  expect(createdResponse.ok()).toBe(true);

  await page.goto("/");
  const row = page.locator(".project-row").filter({ hasText: title });
  await expect(row).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByTitle("删除").click();
  await expect(row).toHaveCount(0);

  await page.reload();
  await expect(page.getByText(title)).toHaveCount(0);
  const dashboard = await page.request.get(`${api}/projects`);
  expect(dashboard.ok()).toBe(true);
  expect((await dashboard.json()).some((project: { title: string }) => project.title === title)).toBe(false);
});
