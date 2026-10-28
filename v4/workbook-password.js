/* Personal password actions shared by published workbook readers. */
window.WorkbookPassword = (() => {
  const key = 'ifcm-personal-session';
  let session, pending;
  async function api(path, body, token = session.token) {
    const response = await fetch('/api/v1/accounts' + path, {
      method: body ? 'POST' : 'GET',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
      ...(body ? {body: JSON.stringify(body)} : {})
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось изменить пароль');
    return data;
  }
  function dialog(required = false) {
    if (pending) return pending;
    pending = new Promise(resolve => {
      const d = document.createElement('dialog');
      d.setAttribute('aria-labelledby', 'workbook-password-title');
      d.style.cssText = 'width:min(480px,calc(100% - 32px));max-height:90vh;overflow:auto;border:1px solid #ddd;border-top:5px solid #b51927;border-radius:12px;padding:24px;color:#333;background:white';
      d.innerHTML = `<h2 id="workbook-password-title">Сменить пароль</h2><p>${required ? 'Вы вошли с одноразовым паролем. Установите собственный пароль, чтобы начать обучение.' : 'Укажите текущий и новый пароль.'}</p><form><label>Текущий пароль<input name="current_password" type="password" autocomplete="current-password" maxlength="200" required></label><label>Новый пароль<input name="new_password" type="password" autocomplete="new-password" minlength="10" maxlength="200" required></label><label>Повторите новый пароль<input name="password_repeat" type="password" autocomplete="new-password" minlength="10" maxlength="200" required></label><p>От 10 до 200 символов.</p><p role="alert"></p><button type="submit">Сохранить пароль</button>${required ? '' : '<button type="button" data-cancel>Отмена</button>'}</form>`;
      d.querySelectorAll('label').forEach(x => x.style.cssText = 'display:block;margin:14px 0');
      d.querySelectorAll('input').forEach(x => x.style.cssText = 'display:block;width:100%;box-sizing:border-box;padding:10px;font:inherit');
      const finish = () => { d.remove(); pending = null; resolve(session); };
      d.addEventListener('cancel', ev => { ev.preventDefault(); if (!required) finish(); });
      d.querySelector('[data-cancel]')?.addEventListener('click', finish);
      d.querySelector('form').onsubmit = async ev => {
        ev.preventDefault();
        const form = ev.target, button = form.querySelector('[type=submit]');
        button.disabled = true;
        const data = Object.fromEntries(new FormData(form));
        try {
          const login = session.account.login, old = session.token;
          await api('/password', data);
          const fresh = await api('/session', {login, password: data.new_password}, '');
          for (const name of [key, 'builder-student-session', 'ifcm-fire-session-student', 'ifcm-fire-session-teacher', 'ifcm-workbook-admin-session']) {
            let stored; try { stored = JSON.parse(sessionStorage.getItem(name)); } catch {}
            if (stored?.token === old) sessionStorage.setItem(name, JSON.stringify(fresh));
          }
          session = fresh;
          if (required) finish();
          else location.reload();
        } catch (error) { form.querySelector('[role=alert]').textContent = error.message; }
        finally { button.disabled = false; }
      };
      document.body.append(d); d.showModal();
    });
    return pending;
  }
  return {
    async prepare(auth, mount) {
      if (!auth?.account) return auth;
      session = auth;
      session.account = await api('/me');
      if (session.account.must_change) { mount(); await dialog(true); }
      return session;
    },
    bind(root, before) {
      root.querySelectorAll('[data-workbook-password]').forEach(button => {
        button.hidden = !session?.account;
        button.onclick = async ev => { ev.preventDefault(); if (before && await before() === false) return; dialog(false); };
      });
    }
  };
})();
