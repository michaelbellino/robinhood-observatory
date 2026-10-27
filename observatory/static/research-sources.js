"use strict";
(() => {
  const get = id => document.getElementById(id);
  const rows = get("research-source-rows");
  let csrf = "", pending = null;
  const show = message => { get("research-source-notice").textContent = message; };
  const refreshAdd = () => { get("research-source-add").disabled = !csrf || rows.children.length >= 5; };
  function row(source = {}) {
    const box = document.createElement("div"); box.className = "order-form";
    for (const [key, label, pattern] of [["symbol", "Ticker", "[A-Z][A-Z0-9.\\-]{0,11}"], ["cik", "SEC CIK", "[0-9]{1,10}"]]) {
      const field = document.createElement("label"), input = document.createElement("input");
      field.textContent = label; input.dataset.field = key; input.value = source[key] || "";
      input.pattern = pattern; input.maxLength = key === "symbol" ? 12 : 10;
      input.placeholder = key === "symbol" ? "Ticker" : "CIK number";
      if (key === "symbol") input.addEventListener("input", () => { input.value = input.value.toUpperCase(); });
      field.append(input); box.append(field);
    }
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove company";
    remove.onclick = () => { box.remove(); refreshAdd(); }; box.append(remove); rows.append(box); refreshAdd();
  }
  async function request(path, body) {
    const options = {credentials: "same-origin", headers: {}};
    if (body !== undefined) { options.method = "POST"; options.headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf}; options.body = JSON.stringify(body); }
    const response = await fetch(path, options), data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the source fields and sign-in status.");
    return data;
  }
  get("research-source-add").onclick = () => row();
  get("research-source-form").onsubmit = async event => {
    event.preventDefault(); get("research-source-save").disabled = true;
    try {
      const sources = [...rows.children].map(box => Object.fromEntries([...box.querySelectorAll("input")].map(input => [input.dataset.field, input.value.trim()]))).filter(source => source.symbol || source.cik);
      if (sources.some(source => !source.symbol || !source.cik)) throw new Error("Each company needs both its ticker and SEC CIK.");
      const values = {sources, sec_user_agent: "StockObservatory " + get("research-source-contact").value.trim(), research_generation_enabled: get("research-source-generate").checked};
      const signature = JSON.stringify(values);
      if (!pending || pending.signature !== signature) pending = {signature, body: {...values, request_id: crypto.randomUUID()}};
      await request("/api/research/sources", pending.body); pending = null;
      get("research-source-status").textContent = `${sources.length} configured · candidate drafting ${values.research_generation_enabled ? "requested" : "off"}`;
      get("research-source-contact").value = "";
      show("Sources saved. The worker will check them on its next eligible research pass; recommendations still require verified data and risk limits.");
    } catch (error) { show(error.message); }
    finally { get("research-source-save").disabled = !csrf; }
  };
  (async () => {
    try {
      const session = await request("/api/session");
      if (session.scope !== "control" || !session.csrf) { get("research-source-status").textContent = "Sign in to configure"; return; }
      csrf = session.csrf;
      const status = await request("/api/research/sources");
      for (const source of status.sources) row(source);
      if (!status.sources.length) row();
      get("research-source-generate").checked = status.research_generation_enabled;
      get("research-source-status").textContent = `${status.sources.length} configured · candidate drafting ${status.research_generation_enabled ? "requested" : "off"}`;
      get("research-source-save").disabled = false; refreshAdd();
    } catch (error) { get("research-source-status").textContent = "Unavailable"; show(error.message); }
  })();
})();
