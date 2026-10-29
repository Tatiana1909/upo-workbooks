const { chromium } = require("playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.ACCOUNTS_UI_CHANNEL || undefined,
    executablePath: process.env.ACCOUNTS_UI_CHROME || undefined,
    // Special flags apply only to the portable browser used in the local sandbox.
    // Standard CI Chromium must retain its normal process isolation.
    args: process.env.ACCOUNTS_UI_CHROME ? [
      "--no-sandbox", "--disable-dev-shm-usage", "--no-zygote", "--single-process",
      "--use-gl=angle", "--use-angle=swiftshader", "--in-process-gpu",
    ] : [],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const base = process.env.ACCOUNTS_UI_URL || "http://127.0.0.1:8124";
  await page.goto(base + "/v4/accounts.html?manage=1");
  await page.getByLabel("Логин", { exact: true }).fill("demo-admin");
  await page.getByLabel("Пароль", { exact: true }).fill("demo-changed-123");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await page
    .getByRole("heading", { name: "Управление участниками", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Добавить участника", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Фамилия", { exact: true }).fill("Тестова");
  await dialog.getByLabel("Имя", { exact: true }).fill("Анна");
  await dialog.getByLabel("Отчество", { exact: true }).fill("Ивановна");
  await dialog.locator("select[name=unit]").selectOption("Игарка");
  await dialog.getByLabel("Должность", { exact: true }).fill("Кладовщик");
  await dialog.getByLabel("Отсутствует / пока неизвестен").check();
  await dialog.getByRole("checkbox", { name: /Учебная тетрадь/ }).check();
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await page
    .getByRole("status")
    .filter({ hasText: "Аккаунт создан." })
    .waitFor();
  assert.match(await page.getByRole("status").innerText(), /Временный пароль/);
  await page
    .getByRole("button", { name: "Создать приглашение", exact: true })
    .click();
  await dialog
    .getByLabel("Название приглашения", { exact: true })
    .fill("Тестовая группа");
  await dialog.locator("select[name=unit]").selectOption("Игарка");
  await dialog.getByRole("checkbox", { name: /Учебная тетрадь/ }).check();
  const [invitationResponse] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith("/accounts/invitations/create") && r.request().method() === "POST"),
    dialog.getByRole("button", { name: "Создать", exact: true }).click(),
  ]);
  assert.equal(invitationResponse.status(), 200, invitationResponse.status() === 200 ? "" : await invitationResponse.text());
  if (errors.length) throw Error(errors.join("\n"));
  await page.locator("#invitationLink").waitFor();
  const link = await page.locator("#invitationLink").inputValue();
  assert.match(link, /invite=/);
  await page.screenshot({ path: "/tmp/accounts-desktop.png", fullPage: false });
  const student = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const p = await student.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(link);
  await p
    .getByRole("heading", { name: "Регистрация участника", exact: true })
    .waitFor();
  await p.getByLabel("Фамилия", { exact: true }).fill("Сидоров");
  await p.getByLabel("Имя", { exact: true }).fill("Иван");
  await p.getByLabel("Нет отчества", { exact: true }).check();
  await p.getByLabel("Должность", { exact: true }).fill("Оператор");
  await p.getByLabel("Табельный номер", { exact: true }).fill("000123");
  await p
    .getByLabel("Установите пароль", { exact: true })
    .fill("student-password-123");
  await p
    .getByLabel("Повторите пароль", { exact: true })
    .fill("student-password-123");
  await p.getByRole("button", { name: "Создать аккаунт", exact: true }).click();
  await p.getByRole("heading", { name: "Учебная тетрадь для проверки", exact: true }).waitFor();
  assert.equal(
    await p.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    false,
  );
  await p.screenshot({ path: "/tmp/accounts-mobile.png", fullPage: true });
  await p.goto(base + "/v4/index.html");
  await p.locator('a[href*="builder/workbook.html?course="]').first().click();
  await p
    .getByRole("heading", { name: "Учебная тетрадь для проверки" })
    .waitFor();
  await p.getByText("Да", { exact: true }).click();
  await p.getByRole("button", { name: "Сохранить", exact: true }).click();
  await p.locator("#status").filter({ hasText: "Сохранено" }).waitFor();
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log(
    "PASS: management, manual creation, invitation, self registration, password setup, mobile layout, workbook progress; no browser errors",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
