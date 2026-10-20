"use strict";
// This UI is the only submission entry point. No timer or notification calls it.
const OwnerOrders = Object.freeze({
  summary(t) {
    const box = document.createElement("div");
    box.className = "order-summary";
    const line = text => { const p = document.createElement("p"); p.textContent = text; box.append(p); };
    if (t.asset === "equity") {
      line(`${t.side.toUpperCase()} ${t.quantity} ${t.symbol} shares · limit $${t.limit_price} per share`);
      line(`Limit value: $${(Number(t.limit_price) * t.quantity).toFixed(2)}, before applicable fees.`);
    } else {
      for (const leg of t.legs) {
        line(`${leg.side.toUpperCase()} TO ${leg.position_effect.toUpperCase()} ${t.quantity * leg.ratio_quantity} × ${t.symbol} ${leg.expiration} $${leg.strike} ${leg.right.toUpperCase()} · multiplier ${leg.multiplier}`);
      }
      line(`Limit premium: $${t.limit_price}${t.direction ? " net " + t.direction : " per share"}.`);
      if (t.legs.length === 1) line(`Premium at limit: $${(Number(t.limit_price) * t.quantity * t.legs[0].multiplier).toFixed(2)}, plus fees shown in the broker preview.`);
      line("Review expiration and exercise exposure. An option can expire worthless or result in an underlying position.");
    }
    line("Limit order · regular hours · good for day. Submission does not guarantee execution.");
    return box;
  },
  render(idea, submission, api, reload, notice) {
    const box = document.createElement("section");
    const text = value => { const p = document.createElement("p"); p.textContent = value; box.append(p); };
    if (submission) {
      text(`Submission: ${submission.state.replaceAll("_", " ")}`);
      if (submission.message) text(submission.message);
      if (submission.order) {
        text(`Robinhood order ${submission.order.id} · ${submission.order.state}`);
        const order = submission.order;
        text(`Filled quantity reported by broker: ${order.processed_quantity ?? order.cumulative_quantity ?? "not reported"}.`);
      }
      if (!submission.retry_allowed) {
        if (submission.state === "unknown") text("Check this order in Robinhood before taking further action. Other submissions are blocked until the uncertainty is resolved.");
        return box;
      }
    }
    if (idea.status !== "awaiting_review" || !idea.preview) return box;
    const t = idea.payload.terms;
    const supported = t.asset === "equity" || (t.legs.length === 1 &&
      ((t.legs[0].side === "buy" && t.legs[0].position_effect === "open") ||
       (t.legs[0].side === "sell" && t.legs[0].position_effect === "close")));
    if (!supported) { text("Submit this structure in Robinhood. Direct entry here currently supports stock limits and opening or closing single-leg long calls/puts."); return box; }
    if (Object.keys(idea.preview.order_checks || {}).length) {
      text("Resolve the broker alerts shown above before direct submission. You can review them in Robinhood.");
      return box;
    }
    const label = document.createElement("label"), check = document.createElement("input"), words = document.createElement("span");
    check.type = "checkbox";
    label.className = "acknowledge";
    words.textContent = "I reviewed this exact order, the current quote, fees, and risks. My next click submits a real order using my brokerage account.";
    label.append(check, words);
    const button = document.createElement("button");
    button.className = "submit-order";
    button.textContent = `Submit ${t.asset === "equity" ? "stock" : "options"} order`;
    button.disabled = true;
    check.onchange = () => { button.disabled = !check.checked; };
    button.onclick = async () => {
      if (!check.checked) return;
      button.disabled = check.disabled = true;
      notice("Checking this exact order and submitting your request. Wait for the broker response; do not submit again elsewhere.");
      try {
        const response = await api(`/api/trades/${idea.id}/submit`, {
          terms_hash: idea.terms_hash, preview_hash: idea.preview_hash, acknowledge_real_order: true
        });
        await reload();
        notice(response.message || "Submission is being processed. Refresh to check its status.");
      } catch (error) {
        notice(`${error.message} Refresh submission status before taking another action.`);
        // Never restore the old submit control on an ambiguous browser failure.
        try { await reload(); } catch (_) { /* Keep disabled until a successful refresh. */ }
      }
    };
    box.append(label, button);
    return box;
  }
});
