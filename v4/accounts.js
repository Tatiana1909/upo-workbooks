"use strict";
const root = document.getElementById("account-app"),
  message = document.getElementById("message"),
  params = new URLSearchParams(location.search),
  sessionKey = "ifcm-personal-session";
let session = null,
  me = null,
  units = [],
  records = [],
  invites = [],
  preview = [],
  courseOptions = [];
try {
  session = JSON.parse(sessionStorage.getItem(sessionKey) || "null");
} catch {}
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function say(text, error = false) {
  message.className = error ? "error" : "success";
  message.textContent = text;
}
async function api(path, body, method = "GET", extra = {}) {
  const response = await fetch("/api/v1/accounts" + path, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(session ? { Authorization: "Bearer " + session.token } : {}),
      ...extra,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const r = await response.json();
  if (!response.ok)
    throw Error(
      typeof r.detail === "string" ? r.detail : "Проверьте заполнение полей",
    );
  return r;
}
function setSession(s) {
  session = s;
  sessionStorage.setItem(sessionKey, JSON.stringify(s));
  for (const key of [
    "builder-student-session",
    "ifcm-fire-session-student",
    "ifcm-fire-session-teacher",
    "ifcm-workbook-admin-session",
  ])
    sessionStorage.removeItem(key);
  if (s.account.roles.includes("participant"))
    for (const key of ["builder-student-session", "ifcm-fire-session-student"])
      sessionStorage.setItem(key, JSON.stringify(s));
  if (
    s.account.roles.includes("admin") ||
    s.account.roles.includes("teacher")
  ) {
    if (s.account.roles.includes("admin") || s.account.realms.includes("upo"))
      sessionStorage.setItem("ifcm-workbook-admin-session", JSON.stringify(s));
    if (s.account.roles.includes("admin") || s.account.realms.includes("fire"))
      sessionStorage.setItem("ifcm-fire-session-teacher", JSON.stringify(s));
  }
}
function clearSession() {
  for (const k of [
    sessionKey,
    "builder-student-session",
    "ifcm-fire-session-student",
    "ifcm-fire-session-teacher",
    "ifcm-workbook-admin-session",
  ])
    sessionStorage.removeItem(k);
  session = null;
  me = null;
}
function data(form) {
  const d = Object.fromEntries(new FormData(form));
  for (const k of ["no_patronymic", "no_personnel_number", "confirm_namesake"])
    d[k] = form.elements[k]?.checked || false;
  return d;
}
const field = (name, label, type = "text", required = true) =>
  `<label>${label}<input name="${name}" type="${type}" ${required ? "required" : ""} ${type === "password" ? 'minlength="10" maxlength="200" autocomplete="new-password"' : 'maxlength="160"'}></label>`;
function profileFields(selfRegistration = false) {
  return `<div class="grid">${field("last_name", "Фамилия")}${field("first_name", "Имя")}${field("patronymic", "Отчество", "text", false)}<label><input type="checkbox" name="no_patronymic">Нет отчества</label>${selfRegistration ? field("unit", "Подразделение") : `<label>Подразделение<select aria-label="Подразделение" name="unit" required><option value="">Выберите подразделение</option>${units.map((x) => `<option>${esc(x)}</option>`).join("")}</select></label>`}${field("position", "Должность", "text", selfRegistration)}${field("personnel_number", "Табельный номер", "text", false)}<label><input type="checkbox" name="no_personnel_number">Отсутствует / пока неизвестен</label></div>`;
}
function bindOptional(form) {
  for (const [flag, key] of [
    ["no_patronymic", "patronymic"],
    ["no_personnel_number", "personnel_number"],
  ]) {
    const update = () => {
      form.elements[key].required = !form.elements[flag].checked;
      form.elements[key].disabled = form.elements[flag].checked;
    };
    form.elements[flag].onchange = update;
    update();
  }
}
async function submit(form, action) {
  form.onsubmit = async (e) => {
    e.preventDefault();
    const buttons = [...form.querySelectorAll("button")];
    buttons.forEach((b) => (b.disabled = true));
    form.querySelector(".form-error")?.remove();
    try {
      await action(data(form));
    } catch (x) {
      const error = document.createElement("p");
      error.className = "error form-error";
      error.setAttribute("role", "alert");
      error.textContent = x.message;
      form.prepend(error);
      error.scrollIntoView({ block: "nearest" });
      say(x.message, true);
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  };
}
function returnTo() {
  const target = params.get("return");
  if (!target) return false;
  const url = new URL(target, location.origin);
  if (
    url.origin === location.origin &&
    url.pathname.startsWith("/v4/") &&
    !url.pathname.endsWith("/accounts.html")
  ) {
    location.assign(url.href);
    return true;
  }
  return false;
}
function login() {
  root.innerHTML = `<section class="card"><h1>Персональный вход</h1><form id="login">${field("login", "Логин")}${field("password", "Пароль", "password")}<button>Войти</button></form><button class="secondary" id="register">Зарегистрироваться</button><p>Если вы забыли логин или пароль, обратитесь к преподавателю или администратору.</p></section>`;
  root.querySelector("[name=password]").removeAttribute("minlength");
  root.querySelector("[name=password]").autocomplete = "current-password";
  root.querySelector("[name=login]").maxLength = 500;
  submit(document.getElementById("login"), async (d) => {
    setSession(await api("/session", d, "POST"));
    me = session.account;
    if (me.must_change) profile();
    else if (!returnTo()) await home();
  });
  document.getElementById("register").onclick = registration;
}
async function registration() {
  let token = params.get("invite") || "";
  root.innerHTML = `<section class="card"><h1>Регистрация участника</h1><form id="invite">${field("invitation", "Код приглашения")}<button>Продолжить</button></form></section>`;
  root.querySelector("[name=invitation]").value = token;
  const proceed = async (d) => {
    const r = await api(
      "/invitation?token=" + encodeURIComponent(d.invitation),
    );
    token = d.invitation;
    units = r.units;
    root.innerHTML = `<section class="card"><h1>Регистрация участника</h1><p>${esc(r.label)} ${esc(r.group_name)}</p><form id="registration">${profileFields(true)}<label>Ваш логин<input id="registrationLogin" readonly autocomplete="off"></label>${field("password", "Установите пароль", "password")}${field("password_repeat", "Повторите пароль", "password")}<small>От 10 до 200 символов. Логин — ваши фамилия, имя и отчество. Регистр букв при входе не важен.</small><button>Создать аккаунт</button></form></section>`;
    const form = document.getElementById("registration");
    if (r.unit) form.elements.unit.value = r.unit;
    bindOptional(form);
    const updateLogin = () => {
      document.getElementById("registrationLogin").value = [
        form.elements.last_name.value,
        form.elements.first_name.value,
        form.elements.no_patronymic.checked ? "" : form.elements.patronymic.value,
      ].map((s) => s.trim().replace(/\s+/g, " ")).filter(Boolean).join(" ");
    };
    form.addEventListener("input", updateLogin);
    form.addEventListener("change", updateLogin);
    updateLogin();
    submit(form, async (d) => {
      setSession(await api("/register", { ...d, invitation: token }, "POST"));
      me = session.account;
      history.replaceState(null, "", "/v4/accounts.html");
      profile();
      say(
        "Аккаунт создан. Ваш логин: " +
          me.login +
          ". Сохраните его для следующих входов.",
      );
    });
  };
  submit(document.getElementById("invite"), proceed);
  if (token)
    try {
      await proceed({ invitation: token });
    } catch (x) {
      say(x.message, true);
    }
}
function profile() {
  root.innerHTML = `<section class="card"><h1>Мой профиль</h1><p><b>${esc(me.full_name)}</b></p><p>Логин: <b>${esc(me.login)}</b><br>Подразделение: ${esc(me.unit)}<br>Табельный номер: ${esc(me.personnel_number || "Не указан")}<br>ID: ${esc(me.id)}</p>${me.must_change ? "<p>Установите собственный пароль для продолжения работы.</p>" : '<a class="button" href="/v4/index.html">Открыть каталог</a>'}<button id="logout" class="secondary">Выйти</button><h2>Сменить пароль</h2><form id="password">${field("current_password", "Текущий пароль", "password")}${field("new_password", "Новый пароль", "password")}${field("password_repeat", "Повторите новый пароль", "password")}<button>Сохранить пароль</button></form></section>`;
  root.querySelector("[name=current_password]").removeAttribute("minlength");
  submit(document.getElementById("password"), async (d) => {
    await api("/password", d, "POST");
    clearSession();
    login();
    say("Пароль изменён. Войдите с новым паролем.");
  });
  document.getElementById("logout").onclick = async () => {
    try {
      await api("/logout", {}, "POST");
    } finally {
      clearSession();
      login();
    }
  };
}
function listInput(name, label, value = "") {
  return `<label>${label}<input name="${name}" value="${esc(value)}"><small>Через запятую. Для курсов укажите идентификаторы опубликованных версий.</small></label>`;
}
function parseList(s) {
  return String(s || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}
function choices(name, title, items, selected = []) {
  return `<fieldset><legend>${esc(title)}</legend>${items.map((x) => `<label><input type="checkbox" name="${name}" value="${esc(x.id)}" ${selected.includes(x.id) ? "checked" : ""}>${esc(x.title)}</label>`).join("") || "<p>Доступных вариантов пока нет.</p>"}</fieldset>`;
}
function coursesInput(value = "") {
  const selected = parseList(value);
  return choices(
    "courses",
    "Назначенные курсы",
    courseOptions.filter((x) => x.is_latest !== false || selected.includes(x.id)).map((x) => ({
      id: x.id,
      title: x.title + (x.is_latest === false ? " · прежняя версия " + x.version : " · актуальная версия"),
    })),
    selected,
  );
}
async function home() {
  me = await api("/me");
  if (me.must_change || !params.has("manage")) {
    profile();
    return;
  }
  if (!me.roles.some((x) => ["admin", "teacher"].includes(x))) {
    profile();
    say("Раздел доступен преподавателю и администратору", true);
    return;
  }
  await management();
}
async function management() {
  [records, units, invites, courseOptions] = await Promise.all([
    api(""),
    api("/units"),
    api("/invitations/list"),
    api("/courses/list"),
  ]);
  root.innerHTML = `<section class="card"><h1>Управление участниками</h1><button id="add">Добавить участника</button><button id="inviteNew">Создать приглашение</button><button id="audit">Журнал аккаунтов</button><button id="profile">Мой профиль</button>${me.roles.includes("admin") ? '<button id="unitAdd">Добавить подразделение</button>' : ""}<label>Поиск по ФИО, логину или табельному номеру<input id="search"></label><div class="scroll"><table><thead><tr><th>ФИО / логин</th><th>Подразделение / должность</th><th>Табельный номер</th><th>Роли / статус</th><th>Действия</th></tr></thead><tbody id="people"></tbody></table></div></section><section class="card"><h2>Загрузка из Excel</h2><p>Заголовки: Фамилия; Имя; Отчество; Подразделение; Должность; Табельный номер. Табельный номер храните в Excel как текст, чтобы сохранить начальные нули.</p><input id="import" type="file" accept=".xlsx,.csv"><div id="preview"></div></section><section class="card"><h2>Приглашения</h2>${invites.map((i) => `<p>${esc(i.label)} · ${esc(i.unit || "Все подразделения")} · ${i.uses}/${i.max_uses} · до ${esc(new Date(i.expires_at * 1000).toLocaleString("ru-RU"))} ${i.active ? `<button data-renew-invite="${esc(i.id)}">Получить новый код</button><button data-revoke="${esc(i.id)}">Отозвать</button>` : "отозвано"}</p>`).join("") || "<p>Приглашений нет.</p>"}</section><dialog id="modal"></dialog>`;
  drawPeople();
  document.getElementById("search").oninput = drawPeople;
  document.getElementById("profile").onclick = profile;
  document.getElementById("add").onclick = () => edit();
  document.getElementById("inviteNew").onclick = inviteForm;
  document.getElementById("audit").onclick = showAudit;
  if (document.getElementById("unitAdd"))
    document.getElementById("unitAdd").onclick = () => {
      modal(
        `<h2>Подразделение</h2><form id="unitForm">${field("name", "Название")}<button>Добавить</button></form>`,
      );
      submit(document.getElementById("unitForm"), async (d) => {
        await api("/units", d, "POST");
        await management();
      });
    };
  root.querySelectorAll("[data-revoke]").forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          await api("/invitations/" + b.dataset.revoke + "/revoke", {}, "POST");
          await management();
        } catch (x) {
          say(x.message, true);
        }
      }),
  );
  root.querySelectorAll("[data-renew-invite]").forEach((button) => {
    button.onclick = async () => {
      const invitation = invites.find((i) => i.id === button.dataset.renewInvite);
      modal(`<h2>Код приглашения «${esc(invitation?.label || "Приглашение")}»</h2><form id="renewInvitationCode">${invitationCodeField()}<p>После сохранения прежний код и ссылка перестанут работать. Зарегистрированные участники сохранят доступ.</p><button>Сохранить код</button></form>`);
      submit(document.getElementById("renewInvitationCode"), async (data) => {
        const result = await api("/invitations/" + button.dataset.renewInvite + "/renew-code", data, "POST");
        showInvitationCode(result.token, invitation?.label || "Приглашение");
      });
    };
  });
  document.getElementById("import").onchange = importFile;
}
function drawPeople() {
  const q = document.getElementById("search").value.toLowerCase();
  document.getElementById("people").innerHTML = records
    .filter((r) =>
      [r.full_name, r.login, r.personnel_number || ""]
        .join(" ")
        .toLowerCase()
        .includes(q),
    )
    .map(
      (r) =>
        `<tr><td>${esc(r.full_name)}<br><small>${esc(r.login)}</small></td><td>${esc(r.unit)}<br>${esc(r.position)}</td><td>${esc(r.personnel_number || "Не указан")}</td><td>${esc(r.roles.map((x) => ({ participant: "Участник", teacher: "Преподаватель", admin: "Администратор" })[x] || x).join(", "))}<br>${esc(r.status === "active" ? "Активен" : "Заблокирован")}</td><td><button data-edit="${r.id}">Карточка</button><button data-reset="${r.id}" class="secondary">Сбросить пароль</button></td></tr>`,
    )
    .join("");
  root
    .querySelectorAll("[data-edit]")
    .forEach(
      (b) =>
        (b.onclick = () => edit(records.find((r) => r.id === b.dataset.edit))),
    );
  root.querySelectorAll("[data-reset]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (!confirm("Сбросить пароль и завершить все сеансы пользователя?"))
          return;
        try {
          const r = await api(
            "/" + b.dataset.reset + "/reset-password",
            {},
            "POST",
          );
          say(
            "Временный пароль показан однократно: " +
              r.temporary_password +
              "\nПередайте его пользователю. При входе потребуется смена.",
          );
          await management();
        } catch (x) {
          say(x.message, true);
        }
      }),
  );
}
function modal(html) {
  const d = document.getElementById("modal");
  d.innerHTML =
    '<button id="closeModal" class="secondary">Закрыть</button>' + html;
  document.getElementById("closeModal").onclick = () => d.close();
  if (!d.open) d.showModal();
}
function edit(r) {
  const admin = me.roles.includes("admin");
  modal(
    `<h2>${r ? "Карточка аккаунта" : "Новый аккаунт"}</h2><form id="edit">${r ? field("full_name", "ФИО") + field("unit", "Подразделение") + field("position", "Должность", "text", false) + field("personnel_number", "Табельный номер", "text", false) : profileFields() + '<label><input name="confirm_namesake" type="checkbox">Подтверждаю создание отдельного аккаунта для тёзки после проверки</label>'}${coursesInput(r?.courses.join(", "))}${
      admin
        ? `<fieldset><legend>Роли</legend>${["participant", "teacher", "admin"].map((x) => `<label><input type="checkbox" name="role_${x}" ${r?.roles.includes(x) || (!r && x === "participant") ? "checked" : ""}>${esc({ participant: "Участник", teacher: "Преподаватель", admin: "Администратор" }[x])}</label>`).join("")}${choices(
            "realms",
            "Направления преподавателя",
            [
              { id: "upo", title: "1С:УПО" },
              { id: "fire", title: "Пожарная безопасность" },
            ],
            r?.realms || [],
          )}${choices(
            "permissions",
            "Разрешённые действия",
            [
              { id: "view", title: "Просмотр результатов" },
              { id: "review", title: "Проверка работ" },
              { id: "edit", title: "Редактирование тетрадей" },
              { id: "publish", title: "Публикация версий" },
            ],
            r?.permissions || ["view"],
          )}${choices(
            "units",
            "Подразделения преподавателя",
            units.map((x) => ({ id: x, title: x })),
            r?.units || [],
          )}</fieldset>`
        : ""
    }${r ? '<label>Статус<select name="status"><option value="active">Активен</option><option value="blocked">Заблокирован</option></select></label>' : "<p>Система создаст логин и временный пароль.</p>"}<button>Сохранить</button></form>`,
  );
  const f = document.getElementById("edit");
  if (r) {
    for (const k of [
      "full_name",
      "unit",
      "position",
      "personnel_number",
      "status",
    ])
      f.elements[k].value = r[k] || "";
  } else bindOptional(f);
  submit(f, async (d) => {
    d.courses = new FormData(f).getAll("courses");
    if (admin) {
      d.roles = ["participant", "teacher", "admin"].filter(
        (x) => f.elements["role_" + x].checked,
      );
      for (const k of ["realms", "permissions", "units"])
        d[k] = new FormData(f).getAll(k);
    }
    if (r) d.revision = r.revision;
    const result = await api(r ? "/" + r.id : "", d, r ? "PUT" : "POST");
    if (r?.id === me.id) {
      clearSession();
      login();
      say("Карточка сохранена. Войдите повторно.");
      return;
    }
    await management();
    say(
      result.temporary_password
        ? "Аккаунт создан. Логин: " +
            result.login +
            "\nВременный пароль показан однократно: " +
            result.temporary_password
        : "Карточка сохранена. Пользователю потребуется повторный вход.",
    );
  });
}
function invitationCodeField() {
  return '<label>Ваш код приглашения<input name="code" minlength="4" maxlength="32" autocomplete="off" placeholder="Например, BUH2026 или БУХ01"></label><p>От 4 до 32 символов: буквы, цифры и дефис. Регистр букв не важен. Если оставить поле пустым, будет создан короткий код из 6 символов.</p>';
}
function inviteForm() {
  modal(
    `<h2>Приглашение к регистрации</h2><form id="invitation">${field("label", "Название приглашения")}${invitationCodeField()}${field("group_name", "Группа", "text", false)}<label>Подразделение<select aria-label="Подразделение" name="unit"><option value="">Выбирает участник</option>${units.map((x) => `<option>${esc(x)}</option>`).join("")}</select></label>${coursesInput()}${field("days", "Срок действия в днях", "number")}${field("max_uses", "Максимум регистраций", "number")}<button>Создать</button></form>`,
  );
  const f = document.getElementById("invitation");
  f.elements.days.value = 7;
  f.elements.max_uses.value = 100;
  submit(f, async (d) => {
    d.courses = new FormData(f).getAll("courses");
    const r = await api("/invitations/create", d, "POST");
    try {
      await management();
    } catch (error) {
      say("Приглашение создано. Не удалось обновить список: " + error.message, true);
    }
    showInvitationCode(r.token, d.label);
  });
}
function showInvitationCode(token, label) {
  const url = location.origin + "/v4/accounts.html?invite=" + encodeURIComponent(token);
  modal(`<h2>Приглашение «${esc(label)}» готово</h2>
    <p>Передайте участникам ссылку — код в ней уже заполнен. При регистрации вручную используйте код ниже.</p>
    <label>Код приглашения<input id="invitationCode" readonly value="${esc(token)}" autocomplete="off"></label>
    <button id="copyInvitationCode" type="button">Копировать код</button>
    <label>Ссылка для регистрации<textarea id="invitationLink" rows="3" readonly>${esc(url)}</textarea></label>
    <button id="copyInvitationLink" type="button">Копировать ссылку</button>
    <p id="invitationCopyStatus" role="status"></p>
    <p>Скопируйте код или ссылку до закрытия окна. Если потеряете их, в разделе «Приглашения» можно получить новый код; прежний перестанет работать.</p>`);
  for (const [buttonId, fieldId] of [["copyInvitationCode", "invitationCode"], ["copyInvitationLink", "invitationLink"]]) {
    document.getElementById(buttonId).onclick = async () => {
      const input = document.getElementById(fieldId);
      const status = document.getElementById("invitationCopyStatus");
      try {
        await navigator.clipboard.writeText(input.value);
        status.textContent = "Скопировано";
      } catch {
        input.focus();
        input.select();
        status.textContent = "Текст выделен. Нажмите Ctrl+C или выберите «Копировать».";
      }
    };
  }
}
async function showAudit() {
  try {
    const rows = await api("/audit/log");
    modal(
      '<h2>Журнал аккаунтов</h2><div class="scroll"><table><thead><tr><th>Дата</th><th>Действие</th><th>Кто / участник</th><th>Сведения</th></tr></thead><tbody>' +
        rows
          .map(
            (r) =>
              `<tr><td>${esc(new Date(r.at).toLocaleString("ru-RU"))}</td><td>${esc(r.action)}</td><td>${esc(r.actor || "Самостоятельная регистрация")}<br>${esc(r.subject || "")}</td><td><pre>${esc(JSON.stringify(r.details, null, 2))}</pre></td></tr>`,
          )
          .join("") +
        "</tbody></table></div>",
    );
  } catch (x) {
    say(x.message, true);
  }
}
async function importFile(e) {
  const f = e.target.files[0];
  if (!f) return;
  try {
    if (f.size > 5000000) throw Error("Файл: не более 5 МБ");
    const r = await fetch("/api/v1/accounts/import/preview", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + session.token,
        "X-File-Format": f.name.endsWith(".xlsx") ? "xlsx" : "csv",
      },
      body: f,
    });
    preview = await r.json();
    if (!r.ok) throw Error(preview.detail);
    const area = document.getElementById("preview");
    area.innerHTML =
      '<p>Проверьте строки перед созданием аккаунтов.</p><div class="scroll"><table><tr><th>Строка</th><th>ФИО</th><th>Результат</th></tr>' +
      preview
        .map(
          (x) =>
            `<tr><td>${x.row}</td><td>${esc([x.data.last_name, x.data.first_name, x.data.patronymic].join(" "))}</td><td>${esc(x.valid ? "Готово к загрузке" : x.error)}</td></tr>`,
        )
        .join("") +
      '</table></div><form id="commit">' +
      coursesInput() +
      "<button>Создать аккаунты по корректным строкам</button></form>";
    submit(document.getElementById("commit"), async (d) => {
      const result = await api(
        "/import/commit",
        {
          rows: preview.filter((x) => x.valid).map((x) => x.data),
          courses: new FormData(document.getElementById("commit")).getAll(
            "courses",
          ),
        },
        "POST",
      );
      await management();
      modal(
        "<h2>Выдача доступов</h2><p>Временные пароли показаны однократно. Передайте каждому сотруднику его доступ.</p>" +
          result
            .map(
              (r) =>
                `<p><b>${esc(r.full_name)}</b><br>Логин: ${esc(r.login)}<br>Временный пароль: ${esc(r.temporary_password)}</p>`,
            )
            .join(""),
      );
    });
  } catch (x) {
    say(x.message, true);
  }
}
(async () => {
  try {
    if (params.has("invite")) await registration();
    else if (session) await home();
    else login();
  } catch (x) {
    clearSession();
    login();
    say(x.message, true);
  }
})();
