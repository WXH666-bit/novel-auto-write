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

  test("关系图和关系表共享数据并自动保存", async ({ page }) => {
    const mock = await openStudio(page);

    await page.getByRole("button", { name: "故事图谱" }).click();
    await expect(page.getByRole("heading", { name: "把人物和情节线牵在一起" })).toBeVisible();
    await expect(page.getByText("当前章节 · 第一章 · 灯塔亮起")).toBeVisible();
    await expect(page.getByLabel("当前章节图谱缩略图")).toBeVisible();
    await expect(page.getByText("章节缩略图", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "关系表" }).click();
    await expect(page.getByRole("table", { name: "人物和情节关系" })).toBeVisible();
    await page.getByLabel("关系标签").first().fill("自动保存的线索");

    await expect.poll(() => mock.graphSaveCount).toBeGreaterThan(0);
    await expect
      .poll(() => mock.requests.filter((request) => request.path.includes("/story-graph/layout")).length)
      .toBeGreaterThan(0);
  });

  test("已保存关系的更新和删除按钮会真正写回同一条关系", async ({ page }) => {
    const mock = await openStudio(page);
    await page.getByRole("button", { name: "故事图谱" }).click();
    await page.getByRole("button", { name: "关系表" }).click();

    await page.getByLabel("关系标签").first().fill("继续追查");
    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "PATCH" &&
          request.path === "/projects/project-1/story-graph/edges/edge-1" &&
          JSON.stringify(request.body).includes("继续追查"),
      ),
    ).toBe(true);

    await page
      .getByRole("button", { name: "删除林渡与灯塔秘密的关系" })
      .click();
    await expect(page.getByText("0 条连线", { exact: true })).toBeVisible();
    await page.getByLabel("新关系标签").fill("重新结盟");
    await page.getByRole("button", { name: "添加", exact: true }).click();
    await expect(page.getByText("1 条连线", { exact: true })).toBeVisible();
    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.path === "/projects/project-1/story-graph/edges/edge-1",
      ),
    ).toBe(true);
    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "POST" &&
          request.path === "/projects/project-1/story-graph/edges" &&
          JSON.stringify(request.body).includes("重新结盟"),
      ),
    ).toBe(true);
  });

  test("人物页新增、扩展字段和保存按钮可用", async ({ page }) => {
    const mock = await openStudio(page);
    await page.getByRole("button", { name: /^人物/ }).first().click();
    await page.getByRole("button", { name: "新增人物" }).click();
    await expect(page.getByRole("heading", { name: "新增人物卷宗" })).toBeVisible();

    await page.getByLabel("姓名").fill("季衡");
    await page.getByPlaceholder("字段名，例如：秘密").fill("秘密");
    await page.getByPlaceholder("字段内容").fill("曾在旧港值夜");
    await page.getByRole("button", { name: "添加字段" }).click();
    await expect(page.getByRole("button", { name: "删除秘密" })).toBeVisible();
    await page.getByRole("button", { name: "删除秘密" }).click();
    await expect(page.getByRole("button", { name: "删除秘密" })).toHaveCount(0);

    await page.getByRole("button", { name: "保存人物卷宗" }).click();
    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "POST" &&
          request.path === "/projects/project-1/characters" &&
          JSON.stringify(request.body).includes("季衡"),
      ),
    ).toBe(true);
    await expect(page.getByText("人物卷宗已保存，当前设定已生效。", { exact: true })).toBeVisible();
  });

  test("关系图缩放和适配按钮可操作", async ({ page }) => {
    await openStudio(page);
    await page.getByRole("button", { name: "故事图谱" }).click();
    const viewport = page.locator(".react-flow__viewport");
    await expect(viewport).toBeVisible();
    const before = await viewport.getAttribute("style");
    await page.getByRole("button", { name: /zoom in/i }).click();
    await expect.poll(() => viewport.getAttribute("style")).not.toBe(before);
    await page.getByRole("button", { name: /zoom out/i }).click();
    await page.getByRole("button", { name: /fit view/i }).click();
    await expect(page.getByRole("application", { name: "可编辑故事关系图" })).toBeVisible();
  });

  test("Agent 改动在人物字段中展示后自动保存", async ({ page }) => {
    const mock = await openStudio(page);
    await page.getByRole("button", { name: /^人物/ }).first().click();
    await expect(page.getByRole("button", { name: "打开人物林渡" })).toBeVisible();

    const composer = page.getByRole("textbox", { name: "发送给 Agent 的消息" });
    await composer.fill("补充人物动机");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("改动已显示在当前内容", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    const characterField = page
      .locator(".character-field")
      .filter({ hasText: "深层动机" });
    const inlineDraft = characterField.locator(".agent-field-draft");
    await expect(inlineDraft).toBeVisible();
    await expect(inlineDraft).toContainText("守护灯塔");
    await expect(page.getByLabel("深层动机")).toHaveValue(
      "守住不该被遗忘的真相",
    );
    await expect(inlineDraft).toContainText("即将自动保存");
    await expect.poll(
      () =>
        mock.requests.some(
          (request) =>
            request.method === "POST" &&
            request.path ===
              "/projects/project-1/assistant/proposals/apply-batch" &&
            JSON.stringify(request.body).includes("proposal-1"),
        ),
      { timeout: 15_000 },
    ).toBe(true);
  });

  test("协作台不再显示 Agent 接受或拒绝按钮", async ({ page }) => {
    await openStudio(page);
    await page.getByRole("button", { name: /^人物/ }).first().click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("补充人物动机");
    await page.getByRole("button", { name: "发送" }).click();

    const liveBuild = page.getByRole("region", {
      name: "Agent 实时制作进度",
    });
    await expect(liveBuild).toBeVisible({ timeout: 15_000 });
    await expect(liveBuild.getByRole("button", { name: /接受|拒绝/ })).toHaveCount(0);
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

  test("Agent 提示固定在状态栏且不会推动输入区", async ({ page }) => {
    await openStudio(page);

    const dock = page.locator(".agent-dock");
    const rail = dock.getByLabel("Agent 当前状态");
    const composer = dock.locator(".agent-compose");
    await expect(rail).toContainText("Agent 已就位");
    await expect(rail).toHaveCSS("height", "48px");
    const before = await composer.boundingBox();

    await dock.getByRole("button", { name: "新建 Agent 对话" }).click();
    await expect(rail).toContainText("新的章节对话已准备好");
    const after = await composer.boundingBox();

    expect(before).not.toBeNull();
    expect(after).not.toBeNull();
    expect(after?.y).toBe(before?.y);
    expect(after?.height).toBe(before?.height);
  });

  test("历史对话使用首条指令命名并在独立面板中检索", async ({ page }) => {
    await openStudio(page);

    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("梳理林渡与灯塔守则的冲突");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("已整理人物设定。", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "查看历史对话" }).click();
    const history = page.getByRole("dialog", { name: "历史 Agent 会话" });
    await expect(history).toBeVisible();
    await expect(history.getByText("梳理林渡与灯塔守则的冲突", { exact: true })).toBeVisible();
    await expect(history.getByText("正在使用", { exact: true })).toBeVisible();

    await history.getByRole("textbox", { name: "搜索历史对话" }).fill("不存在");
    await expect(history.getByText("没有匹配的对话", { exact: true })).toBeVisible();
    await history.getByRole("button", { name: "关闭历史对话" }).click();
    await expect(history).toBeHidden();
    await expect(page.getByRole("textbox", { name: "发送给 Agent 的消息" })).toBeVisible();
  });

  test("不新建对话时连续消息留在同一个章节会话", async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantProposals: [],
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();

    const composer = page.getByRole("textbox", { name: "发送给 Agent 的消息" });
    await composer.fill("先分析这一章的节奏");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("已整理人物设定。", { exact: true })).toBeVisible();
    await composer.fill("继续说说结尾应该怎样收束");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("继续说说结尾应该怎样收束", { exact: true })).toBeVisible();

    const conversationCreates = mock.requests.filter(
      (request) =>
        request.method === "POST" &&
        request.path === "/projects/project-1/assistant/conversations",
    );
    const messagePosts = mock.requests.filter(
      (request) =>
        request.method === "POST" &&
        request.path === "/projects/project-1/assistant/conversations/assistant-1/messages",
    );
    expect(conversationCreates).toHaveLength(1);
    expect(messagePosts).toHaveLength(2);
  });

  test("全书协作按章进入左侧 Diff，整批接受后显示记忆进度", async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantProposal: {
        id: "global-chapter-proposal",
        operation: "edit_chapter",
        target_type: "chapter",
        target_id: "chapter-1",
        target: { type: "chapter", id: "chapter-1", chapter_id: "chapter-1" },
        summary: "统一第一章伏笔",
        patches: [
          {
            path: "replacement",
            value: "林渡在雾里看见灯塔亮起，守则上的旧墨也随之浮现。",
          },
        ],
        status: "proposed",
        base_memory_epoch: 3,
      },
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page.getByRole("button", { name: "全书协作" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("统一检查第一章的守则伏笔");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByRole("region", { name: "全书 Diff 章节索引" })).toContainText(
      "第 1 章",
    );
    await expect(page.getByRole("region", { name: "全书改动 Diff" })).toBeVisible();
    expect(
      mock.requests.some(
        (request) =>
          request.method === "POST" &&
          request.path === "/projects/project-1/assistant/proposals/apply-batch",
      ),
    ).toBe(false);

    await page.getByRole("button", { name: "全部接受" }).click();
    await expect(page.getByText("全书记忆正在后台整理", { exact: true })).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "全书记忆整理进度" })).toHaveAttribute(
      "aria-valuenow",
      "48",
    );
    await page.getByText("全书记忆正在后台整理", { exact: true }).click();
    await expect(page.getByText("下一版正在形成", { exact: true })).toBeVisible();
    await expect(page.getByText(/旧版全书记忆.*仍在安全使用/)).toBeVisible();
  });

  test("运行中的 Agent 可以停止且保留当前对话", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantHoldRun: true,
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("先试写一个开场");
    await page.getByRole("button", { name: "发送" }).click();

    const stop = page.getByRole("button", { name: "停止 Agent 当前任务" });
    await expect(stop).toBeEnabled();
    await stop.click();
    await expect(page.getByLabel("Agent 当前状态")).toContainText("已停止");
    await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  });

  test("Agent 正文在当前稿纸中实时出现并自动保存", async ({ page }) => {
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

    const manuscript = page.getByRole("textbox", {
      name: "第一章 · 灯塔亮起正文",
    });
    await expect(manuscript).toHaveValue(
      "林渡在雾里看见灯塔亮起。楼梯深处传来三声敲击。",
      { timeout: 15_000 },
    );
    await expect(manuscript).not.toBeEditable();
    await expect(page.locator(".manuscript-live-status")).toContainText(
      /Agent 正在写入正文|正在自动保存正文/,
    );
    await expect(page.getByRole("button", { name: /接受|拒绝/ })).toHaveCount(0);

    await expect
      .poll(
        () =>
          mock.requests.some(
            (request) =>
              request.method === "POST" &&
              request.path ===
                "/projects/project-1/assistant/proposals/apply-batch" &&
              JSON.stringify(request.body).includes("chapter-proposal-1"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
  });
  test("Agent 关系生成过程直接画进图谱并自动保存", async ({ page }) => {
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
    await expect(graphDraft).toContainText("连线正在自动保存");

    await expect
      .poll(
        () =>
          mock.requests.some(
            (request) =>
              request.method === "POST" &&
              request.path ===
                "/projects/project-1/assistant/proposals/apply-batch" &&
              JSON.stringify(request.body).includes("edge-proposal-1"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
  });

  test("图谱只保留自动写入和自动保存", async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
      assistantProposal: {
        id: "auto-edge-proposal",
        operation: "upsert_graph_edge",
        target_type: "relationship",
        target: { type: "relationship", id: "" },
        summary: "自动纳入关系",
        patches: [
          { path: "source_node_id", value: "node-char-1" },
          { path: "target_node_id", value: "node-thread-1" },
          { path: "relation_type", value: "守望" },
        ],
        status: "proposed",
        base_memory_epoch: 3,
      },
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开雾中灯塔" }).click();
    await page.getByRole("button", { name: "故事图谱" }).click();
    const toolbar = page.locator(".story-graph-toolbar");
    await expect(toolbar.getByRole("button", { name: /自动接受|需要批准|保存图谱/ })).toHaveCount(0);

    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("补一条本章人物关系");
    await page.getByRole("button", { name: "发送" }).click();
    await expect
      .poll(
        () =>
          mock.requests.some(
            (request) =>
              request.method === "POST" &&
              request.path ===
                "/projects/project-1/assistant/proposals/apply-batch" &&
              JSON.stringify(request.body).includes("auto-edge-proposal"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);

  });

  test("没有稿纸时写章请求会自动创建 Agent 草稿并落入正文预览", async ({ page }) => {
    const project = { ...emptyStoryProjectFixture };
    const mock = await mockStoryApi(page, {
      initialProjects: [project],
      assistantProposal: {
        id: "new-manuscript-proposal",
        operation: "edit_chapter",
        target_type: "chapter",
        target_id: "project-empty-chapter-1",
        target: {
          type: "chapter",
          id: "project-empty-chapter-1",
          chapter_id: "project-empty-chapter-1",
        },
        summary: "写入第一章草稿",
        patches: [
          {
            path: "new_content",
            value: "钟声响过三次，少年推开了无人值守的城门。",
          },
        ],
        status: "proposed",
      },
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开空白灯塔" }).click();
    await page
      .getByRole("textbox", { name: "发送给 Agent 的消息" })
      .fill("给我仿照这个风格写第一章");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByRole("heading", { name: "第1章 · Agent 草稿" })).toBeVisible();
    await expect(page.getByText("1 张稿纸", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "第1章 · Agent 草稿正文" }),
    ).toHaveValue("钟声响过三次，少年推开了无人值守的城门。", {
      timeout: 15_000,
    });
    const chapterCreateIndex = mock.requests.findIndex(
      (request) =>
        request.method === "POST" &&
        request.path === "/projects/project-empty/chapters",
    );
    const agentMessageIndex = mock.requests.findIndex(
      (request) =>
        request.method === "POST" &&
        request.path.includes("/assistant/conversations/assistant-1/messages"),
    );
    expect(chapterCreateIndex).toBeGreaterThanOrEqual(0);
    expect(agentMessageIndex).toBeGreaterThan(chapterCreateIndex);
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

  test("空项目的故事图谱按钮给出可执行入口", async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [emptyStoryProjectFixture],
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开空白灯塔" }).click();
    await page.getByRole("button", { name: "故事图谱" }).click();

    await expect(page.getByText("先为本章铺一张稿纸", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "新建稿纸" }).click();
    await expect(page.getByRole("heading", { name: "第1章 · 未命名稿纸" })).toBeVisible();
    await expect.poll(() =>
      mock.requests.some(
        (request) =>
          request.method === "POST" &&
          request.path === "/projects/project-empty/chapters",
      ),
    ).toBe(true);
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
