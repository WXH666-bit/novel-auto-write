import { expect, test } from "@playwright/test";
import {
  mockStoryApi,
  storyProjectFixture,
} from "./support/story-api";

test("从项目卡片安全删除小说", async ({ page }) => {
  const mock = await mockStoryApi(page, {
    initialProjects: [
      storyProjectFixture,
      {
        ...storyProjectFixture,
        id: "project-2",
        title: "潮汐手记",
        current_chapter_id: "chapter-2",
      },
    ],
  });
  await page.goto("/");

  await expect(page.getByText("2 个项目", { exact: true })).toBeVisible();
  const projectCard = page.locator(".project-card").filter({ hasText: "雾中灯塔" });
  await projectCard.hover();
  await projectCard
    .getByRole("button", { name: "删除小说《雾中灯塔》" })
    .click();

  const dialog = page.getByRole("dialog", { name: "删除小说" });
  await expect(dialog).toContainText("正文、人物卡、故事图谱、Agent 对话和历史版本");
  await expect(dialog.getByRole("button", { name: "永久删除小说" })).toBeDisabled();
  await dialog.getByRole("textbox", { name: "输入小说名称确认删除" }).fill("雾中灯");
  await expect(dialog.getByRole("button", { name: "永久删除小说" })).toBeDisabled();

  await dialog.getByRole("button", { name: "保留小说" }).click();
  await expect(dialog).toBeHidden();
  expect(mock.requests.filter((request) => request.method === "DELETE")).toHaveLength(0);

  await projectCard.hover();
  await projectCard
    .getByRole("button", { name: "删除小说《雾中灯塔》" })
    .click();
  await dialog
    .getByRole("textbox", { name: "输入小说名称确认删除" })
    .fill("雾中灯塔");
  await dialog.getByRole("button", { name: "永久删除小说" }).click();

  await expect.poll(
    () =>
      mock.requests.filter(
        (request) =>
          request.method === "DELETE" && request.path === "/projects/project-1",
      ).length,
  ).toBe(1);
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { name: "雾中灯塔" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "潮汐手记" })).toBeVisible();
  await expect(page.getByText("1 个项目", { exact: true })).toBeVisible();
  await expect(page.getByText("《雾中灯塔》已删除。", { exact: true })).toBeVisible();
});

test("项目卡片导入按钮始终写入被点击的小说", async ({ page }) => {
  const secondProject = {
    ...storyProjectFixture,
    id: "project-2",
    title: "潮汐手记",
    current_chapter_id: "chapter-2",
  };
  const mock = await mockStoryApi(page, {
    initialProjects: [storyProjectFixture, secondProject],
  });
  await page.goto("/");

  const projectCard = page.locator(".project-card").filter({ hasText: "潮汐手记" });
  await projectCard.hover();
  await projectCard
    .getByRole("button", { name: "向《潮汐手记》导入旧稿" })
    .click();

  const dialog = page.getByRole("dialog", { name: "导入旧稿" });
  await expect(dialog).toBeVisible();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "旧稿.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("第一章 旧港\n潮声漫过旧港。", "utf8"),
  });
  await expect(dialog.locator('.import-chapter-row input').first()).toHaveValue(
    "第一章 · 旧港",
  );
  await dialog.getByRole("button", { name: "拆分章节" }).click();
  await expect(dialog.locator(".import-chapter-row")).toHaveCount(2);
  await dialog.getByRole("button", { name: "与上一章合并" }).click();
  await expect(dialog.locator(".import-chapter-row")).toHaveCount(1);
  await dialog.getByRole("button", { name: "取消选择章节" }).click();
  await expect(
    dialog.getByRole("button", { name: "确认并导入" }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "选择章节" }).click();
  await expect(
    dialog.getByRole("button", { name: "确认并导入" }),
  ).toBeEnabled();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "POST" &&
        request.path === "/projects/project-2/import/preview",
    ),
  ).toBe(true);
  expect(
    mock.requests.some(
      (request) => request.path === "/projects/project-1/import/preview",
    ),
  ).toBe(false);

  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toBeHidden();

  await projectCard.hover();
  await projectCard
    .getByRole("button", { name: "向《潮汐手记》导入旧稿" })
    .click();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "旧稿.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("第一章 旧港\n潮声漫过旧港。", "utf8"),
  });
  await dialog.getByRole("button", { name: "确认并导入" }).click();
  await expect(dialog).toBeHidden();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "POST" &&
        request.path === "/projects/project-2/import/commit",
    ),
  ).toBe(true);
});

test("设置页返回按钮回到进入设置前的页面", async ({ page }) => {
  await mockStoryApi(page, { initialProjects: [storyProjectFixture] });
  await page.goto("/");

  await page.getByRole("button", { name: "工作室设置" }).click();
  await expect(page.getByRole("heading", { name: "模型与生成" })).toBeVisible();
  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();

  await page.getByRole("button", { name: "打开雾中灯塔" }).click();
  await page.getByRole("button", { name: "更多操作" }).click();
  await page.getByRole("menuitem", { name: "模型设置" }).click();
  await expect(page.getByRole("heading", { name: "模型与生成" })).toBeVisible();
  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(
    page.getByRole("textbox", { name: "第一章 · 灯塔亮起正文" }),
  ).toBeVisible();
});

test("快捷命令栏的五个动作都有明确结果", async ({ page }) => {
  const mock = await mockStoryApi(page, {
    initialProjects: [storyProjectFixture],
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();

  const runCommand = async (name: string) => {
    await page.getByRole("button", { name: "打开命令栏" }).click();
    const command = page.getByRole("dialog", { name: "快捷命令" });
    await expect(command).toBeVisible();
    await command.getByRole("button", { name: new RegExp(name) }).click();
  };

  await runCommand("生成下一章");
  const generation = page.getByRole("dialog", { name: "生成下一章" });
  await expect(generation).toBeVisible();
  await generation.getByRole("button", { name: "关闭" }).click();

  await runCommand("导入旧稿");
  const importer = page.getByRole("dialog", { name: "导入旧稿" });
  await expect(importer).toBeVisible();
  await importer.getByRole("button", { name: "关闭" }).click();

  await runCommand("打开审核包");
  await expect(page.getByText("当前没有待处理的审核包。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭提示" }).click();

  await runCommand("保存当前草稿");
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "PATCH" && request.path === "/chapters/chapter-1",
    ),
  ).toBe(true);

  await runCommand("打开设置");
  await expect(page.getByRole("heading", { name: "模型与生成" })).toBeVisible();
  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
});

test("模型设置中的新增、测试、保存、默认项、密钥和偏好按钮可用", async ({ page }) => {
  const mock = await mockStoryApi(page, {
    initialProjects: [storyProjectFixture],
  });
  await page.goto("/");
  await page.getByRole("button", { name: "工作室设置" }).click();

  const advanced = page.getByRole("button", { name: /高级参数/ });
  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByLabel("请求超时（毫秒）")).toBeVisible();
  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "false");

  await page.getByRole("button", { name: "测试连接" }).click();
  await expect(page.getByText(/连接成功 ·/)).toBeVisible();
  await page.getByRole("button", { name: "保存连接" }).click();
  await expect(page.getByText("连接已保存。是否设为账户默认项由你决定。", { exact: true })).toBeVisible();

  const memoryToggle = page.locator(
    '.memory-preference-card input[type="checkbox"]',
  );
  await page.locator(".memory-preference-card .toggle-track").click();
  await expect(memoryToggle).not.toBeChecked();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "PATCH" &&
        request.path === "/account/preferences" &&
        JSON.stringify(request.body).includes('"auto_summary_enabled":false'),
    ),
  ).toBe(true);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出项目 ZIP" }).click();
  await downloadPromise;
  expect(
    mock.requests.some(
      (request) =>
        request.method === "GET" &&
        request.path === "/projects/project-1/export",
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "新增", exact: true }).click();
  await page.getByLabel("显示名称").fill("备用模型");
  await page.getByLabel("默认模型").fill("mock-writer");
  await page.getByLabel(/API Key/).fill("test-key");
  await page.getByRole("button", { name: "测试连接" }).click();
  await expect(page.getByText("连接成功 · mock-writer", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存连接" }).click();
  await expect.poll(() =>
    mock.requests.some(
      (request) => request.method === "POST" && request.path === "/providers",
    ),
  ).toBe(true);

  await page
    .locator(".provider-editor-footer")
    .getByRole("button", { name: "设为账户默认" })
    .click();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "PUT" &&
        request.path === "/providers/provider-created/default",
    ),
  ).toBe(true);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除已保存密钥" }).click();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "DELETE" &&
        request.path === "/providers/provider-created/key",
    ),
  ).toBe(true);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "DELETE" &&
        request.path === "/providers/provider-created",
    ),
  ).toBe(true);
});
