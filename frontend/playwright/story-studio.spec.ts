import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  emptyStoryProjectFixture,
  mockStoryApi,
  storyProjectFixture,
} from "./support/story-api";

async function openStudio(
  page: Page,
  assistantProposal?: Record<string, unknown>,
) {
  const mock = await mockStoryApi(page, {
    initialProjects: [storyProjectFixture],
    assistantProposal,
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
  await page.getByRole("button", { name: "打开雾中灯塔" }).click();
  await expect(
    page.getByRole("heading", { name: "第一章 · 灯塔亮起" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "第一章 · 灯塔亮起正文" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "和 Agent 一起写" }),
  ).toBeVisible();
  return mock;
}

test.describe("双栏协作台核心流程", () => {
  test("人物卡片支持键盘打开、Esc 关闭与 reduced-motion", async ({ page }) => {
    test.info().annotations.push({ type: "accessibility", description: "keyboard and reduced-motion" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openStudio(page);

    await page.getByRole("button", { name: /^人物/ }).first().click();

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
    await expect(page.getByText("当前章节 · 第一章 · 灯塔亮起")).toBeVisible();
    await expect(page.getByLabel("当前章节图谱缩略图")).toBeVisible();
    await expect(page.getByText("章节缩略图", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "关系表" }).click();
    await expect(page.getByRole("table", { name: "人物和情节关系" })).toBeVisible();
    await page.getByRole("button", { name: "保存图谱" }).click();

    await expect.poll(() => mock.graphSaveCount).toBeGreaterThan(0);
    await expect
      .poll(() => mock.requests.filter((request) => request.path.includes("/story-graph/layout")).length)
      .toBeGreaterThan(0);
  });

  test("Agent 改动直接显示在人物字段并可就地修改后接受", async ({ page }) => {
    const mock = await openStudio(page);
    await page.getByRole("button", { name: /^人物/ }).first().click();
    await expect(page.getByRole("button", { name: "打开人物林渡" })).toBeVisible();

    const composer = page.getByRole("textbox", { name: "发送给 Agent 的消息" });
    await composer.fill("补充人物动机");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("改动已显示在当前内容", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("实时提案", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "跟随 Agent 草稿" })).toHaveCount(0);
    const characterField = page
      .locator(".character-field")
      .filter({ hasText: "深层动机" });
    const inlineDraft = characterField.locator(".agent-field-draft");
    await expect(inlineDraft).toBeVisible();
    await expect(inlineDraft).toContainText("守护灯塔");
    await expect(page.getByLabel("深层动机")).toHaveValue("守住不该被遗忘的真相");
    await inlineDraft.getByRole("button", { name: "手动修改" }).click();
    await page
      .getByRole("textbox", { name: "深层动机 Agent 草稿手动修改" })
      .fill("守住最后一束灯光");
    await inlineDraft.getByRole("button", { name: "接受改动" }).click();
    await expect.poll(
      () => mock.requests.some(
        (request) =>
          request.method === "PATCH" &&
          request.path === "/projects/project-1/assistant/proposals/proposal-1" &&
          JSON.stringify(request.body).includes("守住最后一束灯光"),
      ),
    ).toBe(true);
    await expect.poll(
      () => mock.requests.some((request) => request.method === "POST" && request.path.includes("/assistant/proposals/proposal-1/apply")),
    ).toBe(true);
  });

  test("Agent 制作人物卡时同步铺开故事图谱并显示真实过渡", async ({ page }) => {
    await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantProposals: [
        {
          id: "character-draft-ji",
          operation: "create_character",
          target_type: "character",
          target: { type: "character", id: "" },
          summary: "新增季衡",
          patches: [
            { path: "name", value: "季衡", label: "姓名" },
            { path: "role", value: "巡夜人", label: "身份" },
            { path: "motivation", value: "查清灯塔异响", label: "动机" },
          ],
          status: "proposed",
        },
        {
          id: "character-draft-wu",
          operation: "create_character",
          target_type: "character",
          target: { type: "character", id: "" },
          summary: "新增阿芜",
          patches: [
            { path: "name", value: "阿芜", label: "姓名" },
            { path: "role", value: "守灯学徒", label: "身份" },
          ],
          status: "proposed",
        },
        {
          id: "relation-draft-1",
          operation: "upsert_graph_edge",
          target_type: "relationship",
          target: { type: "relationship", id: "" },
          summary: "建立互相试探的关系",
          patches: [
            { path: "source_name", value: "季衡", label: "来源" },
            { path: "target_name", value: "阿芜", label: "目标" },
            { path: "relation_type", value: "互相试探", label: "关系" },
          ],
          status: "proposed",
        },
      ],
    });

    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("创建季衡和阿芜的人物卡，并建立两人的关系图谱");
    await page.getByRole("button", { name: "发送" }).click();

    const liveBuild = page.getByRole("region", { name: "Agent 实时制作进度" });
    await expect(liveBuild).toBeVisible({ timeout: 15_000 });
    await expect(liveBuild).toContainText("人物 2");
    await expect(liveBuild).toContainText("节点 2");
    await expect(liveBuild).toContainText("关系 1");
    await expect(liveBuild).toHaveCSS("position", "sticky");
    await expect(page.getByRole("article", { name: "季衡 Agent 草稿" })).toBeVisible();
    await expect(page.getByRole("article", { name: "阿芜 Agent 草稿" })).toBeVisible();
    await expect(
      page
        .getByRole("article", { name: "季衡 Agent 草稿" })
        .locator(".character-draft-fields > div")
        .first(),
    ).toHaveCSS("animation-name", "agent-field-write");

    await liveBuild.getByRole("button", { name: /故事图谱/ }).click();
    await expect(page.getByRole("application", { name: "可编辑故事关系图" })).toBeVisible();
    await expect(page.locator(".story-flow-node.is-agent-draft")).toHaveCount(2);
    const liveEdge = page.locator(".react-flow__edge.agent-draft-edge");
    await expect(liveEdge).toHaveCount(1);
    await expect(liveEdge.locator(".react-flow__edge-path")).toHaveCSS(
      "animation-name",
      "agent-edge-writing",
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(liveEdge.locator(".react-flow__edge-path")).toHaveCSS(
      "animation-name",
      "none",
    );
  });

  test("Agent 思考时显示墨迹过渡并在开始输出后自动让位", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantEventDelayMs: 1_200,
    });

    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("梳理这一章的人物动机");
    await page.getByRole("button", { name: "发送" }).click();

    const thinking = page.getByRole("status", { name: "Agent 正在思考" });
    await expect(thinking).toBeVisible();
    await expect(thinking).toContainText("正在梳理当前章节与当前上下文");
    await expect(thinking.locator(".agent-thinking-dots i").first()).toHaveCSS(
      "animation-name",
      "agent-thinking-dot",
    );

    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(thinking.locator(".agent-thinking-dots i").first()).toHaveCSS(
      "animation-name",
      "none",
    );
    await expect(thinking).toBeHidden({ timeout: 15_000 });
    await expect(page.getByText("已整理人物设定。", { exact: true })).toBeVisible();
  });

  test("Agent 正文改动在稿纸标题处实时出现并可就地确认", async ({ page }) => {
    const mock = await openStudio(page, {
      id: "chapter-proposal-1",
      operation: "edit_chapter",
      target_type: "chapter",
      target_id: "chapter-1",
      target: { type: "chapter", id: "chapter-1" },
      summary: "补写灯塔异响",
      patches: [
        {
          path: "new_content",
          value: "林渡在雾里看见灯塔亮起。楼梯深处传来三声敲击。",
          label: "完整草稿",
        },
      ],
      status: "proposed",
      base_version: 1,
      base_memory_epoch: 3,
    });

    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("在这一章补一段异响");
    await page.getByRole("button", { name: "发送" }).click();

    const titleReview = page
      .locator(".manuscript-editor-head")
      .getByRole("region", { name: "Agent 正文改动操作" });
    await expect(titleReview).toBeVisible({ timeout: 15_000 });
    await expect(titleReview).toContainText("Agent 已准备一处改动");
    await expect(titleReview.getByRole("button", { name: "同意改变" })).toBeVisible();
    await expect(titleReview.getByRole("button", { name: "拒绝" })).toBeVisible();
    await expect(page.getByRole("button", { name: /待处理/ })).toHaveCount(0);

    await titleReview.getByRole("button", { name: "查看改动" }).click();
    const draft = page.getByRole("region", { name: "Agent 正文草稿对比" });
    await expect(draft).toBeVisible();
    await expect(draft).toBeInViewport();
    await expect(draft).toContainText("楼梯深处传来三声敲击");
    await draft.getByRole("button", { name: "手动调整" }).click();
    const editor = draft.getByRole("textbox", { name: "Agent 正文草稿预览" });
    await expect(editor).toBeEditable();
    await expect(draft.getByRole("button", { name: "放弃调整" })).toBeVisible();
    await editor.fill("林渡在雾里看见灯塔亮起。楼梯深处传来两声敲击。");
    await titleReview.getByRole("button", { name: "同意改变" }).click();

    await expect
      .poll(
        () =>
          mock.requests.some(
            (request) =>
              request.method === "PATCH" &&
              request.path ===
                "/projects/project-1/assistant/proposals/chapter-proposal-1" &&
              JSON.stringify(request.body).includes("两声敲击"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
    await expect
      .poll(
        () =>
          mock.requests.some(
            (request) =>
              request.method === "POST" &&
              request.path.includes("/chapter-proposal-1/apply"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
  });

  test("Agent 关系草稿直接画进图谱并在图上处理", async ({ page }) => {
    const mock = await openStudio(page, {
      id: "edge-proposal-1",
      operation: "upsert_graph_edge",
      target_type: "relationship",
      target: { type: "relationship", id: "" },
      summary: "增加追查阻力",
      patches: [
        { path: "source_node_id", value: "node-char-1", label: "来源" },
        { path: "target_node_id", value: "node-thread-1", label: "目标" },
        { path: "relation_type", value: "互相试探", label: "关系类型" },
        { path: "label", value: "隐瞒线索", label: "关系标签" },
      ],
      status: "proposed",
      base_memory_epoch: 3,
    });
    await page.getByRole("button", { name: "故事图谱" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("让林渡和灯塔秘密之间更有张力");
    await page.getByRole("button", { name: "发送" }).click();

    const graphDraft = page
      .locator(".story-graph-agent-draft")
      .filter({ hasText: "隐瞒线索" });
    await expect(graphDraft).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".react-flow__edge.agent-draft-edge")).toHaveCount(1, {
      timeout: 15_000,
    });
    await graphDraft.getByRole("button", { name: "手动修改" }).click();
    await graphDraft
      .getByRole("textbox", { name: "关系标签 Agent 草稿手动修改" })
      .fill("隐瞒真相");
    await graphDraft.getByRole("button", { name: "接受改动" }).click();

    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "PATCH" &&
          request.path ===
            "/projects/project-1/assistant/proposals/edge-proposal-1" &&
          JSON.stringify(request.body).includes("隐瞒真相"),
      ),
    ).toBe(true);
  });

  test("空项目保留写作、定人物和导入旧稿三个入口", async ({ page }) => {
    await mockStoryApi(page, { initialProjects: [emptyStoryProjectFixture] });
    await page.goto("/");
    await page.getByRole("button", { name: "打开空白灯塔" }).click();

    await expect(page.getByRole("heading", { name: "开始写作" })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始写正文" })).toBeVisible();
    await expect(page.getByRole("button", { name: "和 Agent 定人物" })).toBeVisible();
    await expect(page.getByRole("button", { name: "导入旧稿" })).toBeVisible();
  });

  test("1024px 保留内容与 Agent 双栏，390px 使用单栏切换", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 820 });
    await openStudio(page);
    const manuscript = page.getByRole("textbox", {
      name: "第一章 · 灯塔亮起正文",
    });
    const manuscriptBox = await manuscript.boundingBox();
    expect(manuscriptBox?.width || 0).toBeGreaterThan(250);
    await expect(page.getByRole("heading", { name: "和 Agent 一起写" })).toBeVisible();
    await page.getByRole("button", { name: "人物", exact: true }).click();
    await expect(page.getByRole("heading", { name: "林渡", exact: true }).first()).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("tab", { name: "内容", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Agent", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "发送给 Agent 的消息" })).toBeVisible();
    await page.getByRole("tab", { name: "内容", exact: true }).click();
    await expect(page.getByRole("heading", { name: "林渡", exact: true }).first()).toBeVisible();
  });

  test("视觉开关立即保存，并在重新读取后保持状态", async ({ page }) => {
    const mock = await mockStoryApi(page, { initialProjects: [storyProjectFixture] });
    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("menuitem", { name: "模型设置" }).click();

    const vision = page.getByRole("checkbox", { name: "支持视觉输入" });
    await expect(vision).toBeChecked();
    await vision.uncheck();
    await expect(page.getByText("视觉输入设置已保存。", { exact: true })).toBeVisible();
    await expect.poll(
      () => mock.requests.filter(
        (request) => request.method === "PATCH" && request.path === "/providers/provider-1",
      ).length,
    ).toBeGreaterThan(0);
    const saveRequest = mock.requests.find(
      (request) => request.method === "PATCH" && request.path === "/providers/provider-1",
    );
    expect(saveRequest?.body).toMatchObject({
      capabilities: { vision: false, tools: true },
    });

    await page.reload();
    await page.getByRole("button", { name: "打开账号菜单" }).click();
    await page.getByRole("menuitem", { name: "模型与生成" }).click();
    await expect(page.getByRole("checkbox", { name: "支持视觉输入" })).not.toBeChecked();
  });

  test("工作台不再请求或展示全局待处理入口", async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
    });

    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await expect(page.getByRole("button", { name: /待处理/ })).toHaveCount(0);
    expect(mock.requests.some((request) => request.path.includes("/attention"))).toBe(false);
  });
});
