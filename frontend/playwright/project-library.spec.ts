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
