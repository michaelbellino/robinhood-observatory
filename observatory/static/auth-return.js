// Preserve a review anchor through owner sign-in. Never forward URL queries.
(() => {
  function update() {
    if (!["/", "/trades", "/workbench"].includes(location.pathname)) return;
    const anchor = /^#[A-Za-z0-9_-]{1,128}$/.test(location.hash) ? location.hash : "";
    const destination = location.pathname + anchor;
    for (const link of document.querySelectorAll('a[href^="/auth/login"]')) {
      link.href = "/auth/login?next=" + encodeURIComponent(destination);
    }
  }
  update();
  window.addEventListener("hashchange", update);
  window.addEventListener("pageshow", update);
})();
