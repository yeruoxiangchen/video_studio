import { expect, test } from "@playwright/test";

test("draft survives leaving a project and IME Enter never submits", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建项目", exact: true }).first().click();
  const name = `输入恢复 ${Date.now()}`;
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建并打开" }).click();
  const input = page.getByRole("textbox", { name: "剪辑需求" });
  await expect(input).toBeVisible();
  await expect(page.getByRole("button", { name: "发送剪辑需求" })).toBeDisabled();
  await input.fill("保留结尾的原声，缩短开头");
  await input.dispatchEvent("keydown", {key: "Enter", code: "Enter", isComposing: true});
  await expect(page.getByRole("alert")).toHaveCount(0);
  let sessionListRequests = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && new URL(request.url()).pathname === "/api/v1/editing/sessions") {
      sessionListRequests++;
    }
  });
  await page.reload();
  const project = page.getByRole("button", { name: `打开项目 ${name}`, exact: true });
  await expect(project.or(page.getByRole("alert")).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(project).toBeVisible();
  expect(sessionListRequests).toBe(0);
  await project.click();
  await expect(input).toHaveValue("保留结尾的原声，缩短开头");
});
