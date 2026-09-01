import { expect, test } from "@playwright/test";
import { mockStoryApi } from "./support/story-api";

const startModes = [
  { label: "空白稿纸", mode: "blank", submit: "创建第一张稿纸" },
  { label: "导入旧稿", mode: "import", submit: "创建并导入" },
  { label: "和 Agent 一起搭建", mode: "setup", submit: "创建并打开工坊" },
] as const;

test.describe("新建小说向导", () => {
  for (const scenario of startModes) {
    test(`支持${scenario.label}入口`, async ({ page }) => {
      const mock = await mockStoryApi(page);
      await page.goto("/");

      await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
      await page.getByRole("button", { name: "新建小说" }).first().click();

      const dialog = page.getByRole("dialog", { name: "新建小说" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("radio", { name: new RegExp(scenario.label) }).click();
      await dialog.getByRole("button", { name: "继续" }).click();
      await dialog.getByRole("textbox", { name: "小说名称" }).fill(`测试${scenario.label}`);
      await dialog.getByRole("button", { name: scenario.submit }).click();

      await expect.poll(
        () => mock.requests.filter((request) => request.method === "POST" && request.path === "/projects").length,
      ).toBe(1);
      const createRequest = mock.requests.find(
        (request) => request.method === "POST" && request.path === "/projects",
      );
      expect(createRequest?.body).toMatchObject({
        start_mode: scenario.mode,
        title: `测试${scenario.label}`,
      });
      await expect(dialog).toBeHidden();

      if (scenario.mode === "import") {
        await expect(page.getByRole("dialog", { name: "导入旧稿" })).toBeVisible();
      }
      if (scenario.mode === "setup") {
        await expect(page.getByRole("heading", { name: "设定工坊" })).toBeVisible();
      }
    });
  }
});
