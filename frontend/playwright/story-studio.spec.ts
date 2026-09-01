import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockStoryApi, storyProjectFixture } from "./support/story-api";

async function openStudio(page: Page) {
  const mock = await mockStoryApi(page, { initialProjects: [storyProjectFixture] });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
  await page.getByRole("button", { name: "打开雾中灯塔" }).click();
  await expect(page.getByRole("button", { name: "工坊" })).toBeVisible();
  await page.getByRole("button", { name: "工坊" }).click();
  await expect(page.getByRole("heading", { name: "设定工坊" })).toBeVisible();
  return mock;
}

test.describe("设定工坊核心流程", () => {
  test("人物卡片支持键盘打开、Esc 关闭与 reduced-motion", async ({ page }) => {
    test.info().annotations.push({ type: "accessibility", description: "keyboard and reduced-motion" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openStudio(page);

    const card = page.getByRole("button", { name: "打开人物林渡" });
    await expect(card).toBeVisible();
    await card.focus();
    await page.keyboard.press("Enter");

    const detail = page.getByRole("dialog", { name: "林渡" });
    await expect(detail).toBeVisible();
    await expect(
      detail.locator("article").getByRole("button", { name: "关闭人物详情" }),
    ).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(detail).toBeHidden();
  });

  test("关系图和关系表共享可保存的图谱数据", async ({ page }) => {
    const mock = await openStudio(page);

    await page.getByRole("button", { name: "故事图谱" }).click();
    await expect(page.getByRole("heading", { name: "把人物和情节线牵在一起" })).toBeVisible();
    await page.getByRole("button", { name: "关系表" }).click();
    await expect(page.getByRole("table", { name: "人物和情节关系" })).toBeVisible();
    await page.getByRole("button", { name: "保存图谱" }).click();

    await expect.poll(() => mock.graphSaveCount).toBeGreaterThan(0);
    await expect
      .poll(() => mock.requests.filter((request) => request.path.includes("/story-graph/layout")).length)
      .toBeGreaterThan(0);
  });

  test("Agent 对话展示实时提案并可应用到人物表格", async ({ page }) => {
    const mock = await openStudio(page);
    await expect(page.getByRole("button", { name: "打开人物林渡" })).toBeVisible();

    const composer = page.getByRole("textbox", { name: "发送给 Agent 的消息" });
    await composer.fill("补充人物动机");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("实时提案", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "应用到表格" })).toBeVisible();
    await page.getByRole("button", { name: "应用到表格" }).click();
    await expect.poll(
      () => mock.requests.some((request) => request.method === "POST" && request.path.includes("/assistant/proposals/proposal-1/apply")),
    ).toBe(true);
  });
});
