(() => {
  const install = () => {
    if (document.getElementById("personal-links")) return;
    const nav = document.createElement("nav");
    nav.id = "personal-links";
    nav.style.cssText =
      "padding:10px 20px;background:#fff;border-bottom:1px solid #ddd;display:flex;gap:20px;flex-wrap:wrap";
    for (const [text, href] of [
      ["Участники", "/v4/accounts.html?manage=1"],
      ["Мой профиль и пароль", "/v4/accounts.html"],
    ]) {
      const a = document.createElement("a");
      a.textContent = text;
      a.href = href;
      nav.append(a);
    }
    document.body.prepend(nav);
  };
  document.addEventListener("DOMContentLoaded", install);
})();
