(function () {
  "use strict";

  const courseKey = document.body.dataset.course;
  const sourceCourse = window.WORKBOOK_COURSES && window.WORKBOOK_COURSES[courseKey];
  if (!sourceCourse) {
    document.body.innerHTML = "<main><h1>Материалы курса не найдены</h1></main>";
    return;
  }

  const course = {
    ...sourceCourse,
    subtitle: sourceCourse.intro,
    result: sourceCourse.modules[sourceCourse.modules.length - 1].outcome,
    criticalErrors: sourceCourse.critical,
    modules: sourceCourse.modules.map(module => ({
      ...module,
      time: module.minutes,
      shortTitle: module.title.replace(/^(День|Блок)\s+\d+\s*/u, ""),
      concepts: module.concepts.map(item => `${item.title}. ${item.text}`),
      materials: module.materials.split(";").map(item => item.trim()).filter(Boolean),
      quiz: module.quiz.map(item => ({
        text: item.q,
        options: item.o,
        correct: item.a,
        explanation: item.e,
      })),
    })),
  };

  const storageKey = `ifcm-workbook-${course.id}`;
  const blankState = () => ({ checks: {}, notes: {}, answers: {}, done: {}, visited: { intro: true } });
  let state = loadState();

  function loadState() {
    try {
      return Object.assign(blankState(), JSON.parse(localStorage.getItem(storageKey) || "{}"));
    } catch (_) {
      return blankState();
    }
  }

  function saveState() {
    localStorage.setItem(storageKey, JSON.stringify(state));
    updateProgress();
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function sectionId(index) {
    return `m${index + 1}`;
  }

  function quizStats() {
    let correct = 0;
    let answered = 0;
    let total = 0;
    course.modules.forEach((module, mi) => {
      module.quiz.forEach((question, qi) => {
        total += 1;
        const value = state.answers[`${mi}-${qi}`];
        if (value !== undefined) {
          answered += 1;
          if (Number(value) === question.correct) correct += 1;
        }
      });
    });
    return { correct, answered, total };
  }

  function completionStats() {
    const totalChecks = course.modules.reduce((sum, module) => sum + module.practice.length, 0);
    const checked = Object.values(state.checks).filter(Boolean).length;
    const quiz = quizStats();
    const totalDone = checked + quiz.answered;
    const total = totalChecks + quiz.total;
    return {
      totalChecks,
      checked,
      quiz,
      percent: total ? Math.round((totalDone / total) * 100) : 0,
    };
  }

  function renderShell() {
    const navItems = course.modules.map((module, index) => `
      <a class="nav-link" data-target="${sectionId(index)}" href="#${sectionId(index)}">
        <span class="nav-number">${index + 1}</span>
        <span>${esc(module.shortTitle || module.title)}</span>
        <span class="nav-status" aria-hidden="true"></span>
      </a>`).join("");

    document.body.innerHTML = `
      <header class="topbar">
        <a class="brand" href="index.html" aria-label="К выбору курса">
          <img src="assets/ifcm-logo.svg" alt="iFCM">
          <span>Рабочая тетрадь</span>
        </a>
        <div class="top-actions">
          <span class="progress-label"><strong id="progressText">0%</strong> выполнено</span>
          <button class="button button-ghost" id="printButton" type="button">Печать / PDF</button>
        </div>
      </header>
      <div class="layout">
        <aside class="sidebar" aria-label="Навигация по тетради">
          <div class="course-mini-title">${esc(course.title)}</div>
          <div class="progress-track"><span id="progressBar"></span></div>
          <nav>
            <a class="nav-link" data-target="intro" href="#intro"><span class="nav-number">→</span><span>Старт</span><span class="nav-status"></span></a>
            ${navItems}
            <a class="nav-link" data-target="final" href="#final"><span class="nav-number">✓</span><span>Итоги</span><span class="nav-status"></span></a>
          </nav>
          <div class="sidebar-actions">
            <button class="text-button" id="exportButton" type="button">Сохранить результаты</button>
            <button class="text-button danger" id="resetButton" type="button">Сбросить прогресс</button>
          </div>
        </aside>
        <main id="workbookMain">
          ${renderIntro()}
          ${course.modules.map(renderModule).join("")}
          ${renderFinal()}
        </main>
      </div>`;
  }

  function renderIntro() {
    return `
      <section class="workbook-section" id="intro">
        <div class="eyebrow">Интерактивный практикум · ${esc(course.duration)}</div>
        <h1>${esc(course.title)}</h1>
        <p class="lead">${esc(course.subtitle)}</p>
        <div class="hero-grid">
          <article class="info-card"><span class="card-kicker">Формат</span><strong>${course.modules.length} учебных модулей</strong><p>Краткая теория, практика в тестовой базе, заметки и проверка знаний.</p></article>
          <article class="info-card"><span class="card-kicker">Результат</span><strong>${esc(course.result)}</strong><p>Прогресс сохраняется автоматически в этом браузере.</p></article>
        </div>
        <div class="callout"><strong>Как работать с тетрадью</strong><p>Читайте опорный материал, выполняйте действия в учебной базе, отмечайте чек‑лист и отвечайте на вопросы. Ошибка в тесте — повод вернуться к шагам, а не просто выбрать другой вариант.</p></div>
        <h2>Правила учебной работы</h2>
        <ul class="rules">${course.rules.map(item => `<li>${esc(item)}</li>`).join("")}</ul>
        <div class="section-footer"><button class="button next-button" data-next="m1" type="button">Начать обучение</button></div>
      </section>`;
  }

  function renderModule(module, moduleIndex) {
    const id = sectionId(moduleIndex);
    const practice = module.practice.map((item, itemIndex) => {
      const key = `${moduleIndex}-${itemIndex}`;
      return `<label class="check-item"><input type="checkbox" data-check="${key}" ${state.checks[key] ? "checked" : ""}><span>${esc(item)}</span></label>`;
    }).join("");

    const concepts = module.concepts.map(item => `<li>${esc(item)}</li>`).join("");
    const materials = module.materials.map(item => `<li>${esc(item)}</li>`).join("");
    const quiz = module.quiz.map((question, questionIndex) => renderQuestion(question, moduleIndex, questionIndex)).join("");
    const note = state.notes[moduleIndex] || "";
    const next = moduleIndex === course.modules.length - 1 ? "final" : sectionId(moduleIndex + 1);

    return `
      <section class="workbook-section" id="${id}">
        <div class="eyebrow">Модуль ${moduleIndex + 1} из ${course.modules.length} · ${esc(module.time)}</div>
        <h1>${esc(module.title)}</h1>
        <div class="outcome"><span>Результат модуля</span><strong>${esc(module.outcome)}</strong></div>
        <div class="content-grid">
          <article class="panel">
            <h2>Опорный материал</h2>
            <ul>${concepts}</ul>
          </article>
          <article class="panel accent-panel">
            <h2>Материалы курса</h2>
            <ul>${materials}</ul>
          </article>
        </div>
        <article class="practice-card">
          <div class="section-heading"><span class="step-badge">Практика</span><h2>Выполните в тестовой базе</h2></div>
          <div class="checklist">${practice}</div>
          <label class="note-label" for="note-${moduleIndex}">${esc(module.prompt)}</label>
          <textarea id="note-${moduleIndex}" data-note="${moduleIndex}" rows="5" placeholder="Запишите наблюдение, вывод или последовательность действий…">${esc(note)}</textarea>
        </article>
        <article class="quiz-card">
          <div class="section-heading"><span class="step-badge red">Проверка</span><h2>Контрольные вопросы</h2></div>
          ${quiz}
        </article>
        <div class="module-complete">
          <label><input type="checkbox" data-done="${moduleIndex}" ${state.done[moduleIndex] ? "checked" : ""}> Модуль разобран, вопросы преподавателю зафиксированы</label>
        </div>
        <div class="section-footer">
          <button class="button next-button" data-next="${next}" type="button">${next === "final" ? "Перейти к итогам" : "Следующий модуль"}</button>
        </div>
      </section>`;
  }

  function renderQuestion(question, moduleIndex, questionIndex) {
    const key = `${moduleIndex}-${questionIndex}`;
    const selected = state.answers[key];
    const options = question.options.map((option, optionIndex) => `
      <label class="quiz-option ${selected !== undefined && optionIndex === question.correct ? "is-correct" : ""} ${selected !== undefined && Number(selected) === optionIndex && optionIndex !== question.correct ? "is-wrong" : ""}">
        <input type="radio" name="q-${key}" value="${optionIndex}" data-answer="${key}" ${Number(selected) === optionIndex ? "checked" : ""}>
        <span>${esc(option)}</span>
      </label>`).join("");
    const feedback = selected === undefined ? "" : `
      <div class="feedback ${Number(selected) === question.correct ? "good" : "bad"}">
        <strong>${Number(selected) === question.correct ? "Верно." : "Нужно уточнить."}</strong> ${esc(question.explanation)}
      </div>`;
    return `<fieldset class="question" data-question="${key}"><legend>${questionIndex + 1}. ${esc(question.text)}</legend>${options}${feedback}</fieldset>`;
  }

  function renderFinal() {
    const stats = completionStats();
    const score = stats.quiz.total ? Math.round((stats.quiz.correct / stats.quiz.total) * 100) : 0;
    return `
      <section class="workbook-section" id="final">
        <div class="eyebrow">Итоги практикума</div>
        <h1>Результаты и памятка</h1>
        <div class="stats-grid">
          <article class="stat-card"><strong id="finalProgress">${stats.percent}%</strong><span>общий прогресс</span></article>
          <article class="stat-card"><strong id="finalChecks">${stats.checked}/${stats.totalChecks}</strong><span>практических шагов</span></article>
          <article class="stat-card"><strong id="finalScore">${score}%</strong><span>верных ответов</span></article>
        </div>
        <div class="callout"><strong>Курс считается проработанным, если</strong><p>выполнены практические действия, разобраны ошибки теста, зафиксированы вопросы и понятна граница ответственности вашей роли.</p></div>
        <h2>Критические ошибки, которых важно избегать</h2>
        <ul class="danger-list">${course.criticalErrors.map(item => `<li>${esc(item)}</li>`).join("")}</ul>
        <h2>Ваши записи</h2>
        <div id="notesSummary" class="notes-summary">${renderNotesSummary()}</div>
        <div class="section-footer split">
          <button class="button button-secondary" id="reviewButton" type="button">Вернуться к незавершённому</button>
          <button class="button" id="finalExportButton" type="button">Сохранить результаты</button>
        </div>
      </section>`;
  }

  function renderNotesSummary() {
    const notes = course.modules.map((module, index) => ({ title: module.title, note: (state.notes[index] || "").trim() })).filter(item => item.note);
    if (!notes.length) return "<p class=\"muted\">Заметки появятся здесь после заполнения полей в модулях.</p>";
    return notes.map(item => `<article><strong>${esc(item.title)}</strong><p>${esc(item.note).replaceAll("\n", "<br>")}</p></article>`).join("");
  }

  function updateProgress() {
    const stats = completionStats();
    const score = stats.quiz.total ? Math.round((stats.quiz.correct / stats.quiz.total) * 100) : 0;
    document.getElementById("progressText").textContent = `${stats.percent}%`;
    document.getElementById("progressBar").style.width = `${stats.percent}%`;
    const finalProgress = document.getElementById("finalProgress");
    if (finalProgress) finalProgress.textContent = `${stats.percent}%`;
    const finalChecks = document.getElementById("finalChecks");
    if (finalChecks) finalChecks.textContent = `${stats.checked}/${stats.totalChecks}`;
    const finalScore = document.getElementById("finalScore");
    if (finalScore) finalScore.textContent = `${score}%`;
    const notesSummary = document.getElementById("notesSummary");
    if (notesSummary) notesSummary.innerHTML = renderNotesSummary();

    course.modules.forEach((_, index) => {
      const link = document.querySelector(`[data-target="${sectionId(index)}"]`);
      if (link) link.classList.toggle("is-done", Boolean(state.done[index]));
    });
  }

  function showSection(target) {
    const id = document.getElementById(target) ? target : "intro";
    document.querySelectorAll(".workbook-section").forEach(section => section.classList.toggle("is-active", section.id === id));
    document.querySelectorAll(".nav-link").forEach(link => link.classList.toggle("is-active", link.dataset.target === id));
    state.visited[id] = true;
    saveState();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function refreshQuestion(key) {
    const [mi, qi] = key.split("-").map(Number);
    const fieldset = document.querySelector(`[data-question="${key}"]`);
    if (!fieldset) return;
    const temp = document.createElement("div");
    temp.innerHTML = renderQuestion(course.modules[mi].quiz[qi], mi, qi);
    fieldset.replaceWith(temp.firstElementChild);
  }

  function exportResults() {
    const stats = completionStats();
    const lines = [
      course.title,
      `Дата сохранения: ${new Date().toLocaleString("ru-RU")}`,
      `Общий прогресс: ${stats.percent}%`,
      `Практика: ${stats.checked} из ${stats.totalChecks}`,
      `Тест: ${stats.quiz.correct} верных из ${stats.quiz.total}`,
      "",
      "ЗАМЕТКИ",
    ];
    course.modules.forEach((module, index) => {
      lines.push("", `${index + 1}. ${module.title}`, state.notes[index] || "—");
    });
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${course.id}-results.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function firstIncomplete() {
    for (let i = 0; i < course.modules.length; i += 1) {
      if (!state.done[i]) return sectionId(i);
    }
    return "final";
  }

  function bindEvents() {
    document.addEventListener("change", event => {
      const target = event.target;
      if (target.matches("[data-check]")) {
        state.checks[target.dataset.check] = target.checked;
        saveState();
      }
      if (target.matches("[data-done]")) {
        state.done[target.dataset.done] = target.checked;
        saveState();
      }
      if (target.matches("[data-answer]")) {
        state.answers[target.dataset.answer] = Number(target.value);
        saveState();
        refreshQuestion(target.dataset.answer);
      }
    });

    document.addEventListener("input", event => {
      if (event.target.matches("[data-note]")) {
        state.notes[event.target.dataset.note] = event.target.value;
        saveState();
      }
    });

    document.addEventListener("click", event => {
      const next = event.target.closest("[data-next]");
      if (next) {
        location.hash = next.dataset.next;
      }
    });

    document.getElementById("printButton").addEventListener("click", () => window.print());
    document.getElementById("exportButton").addEventListener("click", exportResults);
    document.getElementById("finalExportButton").addEventListener("click", exportResults);
    document.getElementById("reviewButton").addEventListener("click", () => { location.hash = firstIncomplete(); });
    document.getElementById("resetButton").addEventListener("click", () => {
      if (confirm("Сбросить все отметки, ответы и заметки в этой тетради?")) {
        localStorage.removeItem(storageKey);
        location.reload();
      }
    });
    window.addEventListener("hashchange", () => showSection(location.hash.slice(1)));
  }

  renderShell();
  bindEvents();
  updateProgress();
  showSection(location.hash.slice(1) || "intro");
}());
