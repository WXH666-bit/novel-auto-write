import { expect, test } from "@playwright/test";
import { mockStoryApi, storyProjectFixture } from "./support/story-api";

test("登录页切换和登录按钮可用", async ({ page }) => {
  const requests: Array<{ method: string; path: string }> = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    requests.push({ method, path });
    const fulfill = (payload: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });

    if (path === "/auth/me") {
      await fulfill({ detail: "not authenticated" }, 401);
      return;
    }
    if (path === "/auth/config") {
      await fulfill({
        mode: "username",
        verification_required: false,
        password_reset_available: false,
      });
      return;
    }
    if (path === "/auth/login" && method === "POST") {
      await fulfill({
        user: {
          id: "user-login",
          username: "writer",
          display_name: "测试作者",
          is_email_verified: true,
          is_active: true,
        },
        csrf_token: "login-csrf",
      });
      return;
    }
    if (path === "/projects") {
      await fulfill({ projects: [] });
      return;
    }
    if (path === "/providers") {
      await fulfill({ providers: [] });
      return;
    }
    if (path === "/providers/default") {
      await fulfill({}, 404);
      return;
    }
    if (path === "/account/preferences") {
      await fulfill({ auto_summary_enabled: true, preferences_version: 1 });
      return;
    }
    await fulfill({});
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "继续你的故事" })).toBeVisible();
  await page.getByRole("button", { name: "注册新账号" }).click();
  await expect(page.getByRole("heading", { name: "建立你的编剧室" })).toBeVisible();
  await page.getByRole("button", { name: /已有账号，返回登录/ }).click();
  await expect(page.getByRole("heading", { name: "继续你的故事" })).toBeVisible();

  await page.getByLabel("用户名").fill("writer");
  await page.getByLabel("密码").fill("test-password");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
  expect(
    requests.some(
      (request) => request.method === "POST" && request.path === "/auth/login",
    ),
  ).toBe(true);
});

test("账号页返回、改密和注销展开按钮可用", async ({ page }) => {
  const mock = await mockStoryApi(page, {
    initialProjects: [storyProjectFixture],
  });
  await page.goto("/");
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("menuitem", { name: "账号与安全" }).click();
  await expect(page.getByRole("heading", { name: "账号与安全" })).toBeVisible();

  await page.getByLabel("当前密码").fill("current-password");
  await page.getByLabel("新密码").fill("next-password-123");
  await page.getByRole("button", { name: "更新密码" }).click();
  await expect(page.getByText("密码已更新，其他设备已退出。", { exact: true })).toBeVisible();
  await expect.poll(() =>
    mock.requests.some(
      (request) =>
        request.method === "POST" && request.path === "/auth/change-password",
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "注销账号", exact: true }).click();
  await expect(page.getByText("这是不可逆操作。输入当前密码确认删除账号、小说和系统凭据。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "注销账号", exact: true }).click();
  await expect(page.getByLabel("输入当前密码确认注销账号")).toHaveCount(0);

  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
});

for (const action of ["退出当前设备", "退出所有设备"] as const) {
  test(`${action}按钮会清除当前界面会话`, async ({ page }) => {
    const mock = await mockStoryApi(page, {
      initialProjects: [storyProjectFixture],
    });
    await page.goto("/");
    await page.getByRole("button", { name: "打开账号菜单" }).click();
    await page.getByRole("menuitem", { name: "账号与安全" }).click();
    await page.getByRole("button", { name: action }).click();

    await expect(page.getByRole("heading", { name: "继续你的故事" })).toBeVisible();
    const expectedPath = action === "退出当前设备" ? "/auth/logout" : "/auth/logout-all";
    expect(
      mock.requests.some(
        (request) => request.method === "POST" && request.path === expectedPath,
      ),
    ).toBe(true);
  });
}
