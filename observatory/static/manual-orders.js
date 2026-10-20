"use strict";
// This UI is the only submission entry point. No timer or notification calls it.
const OwnerOrders = Object.freeze({
  summary(t) {
    const box = document.createElement("div");
    box.className = "order-summary";
    const line = text => { const p = document.createElement("p"); p.textContent = text; box.append(p); };
    if (t.asset === "equity") {
      line(`${t.side.toUpperCase()} ${t.dollar_amount ? "$" + t.dollar_amount + " of " + t.symbol : t.quantity + " " + t.symbol + " shares"}`);
      if (t.limit_price) line(`Limit $${t.limit_price} per share · value at limit $${(Number(t.limit_price) * t.quantity).toFixed(2)}, before fees.`);
    } else {
      for (const leg of t.legs) {
        line(`${leg.side.toUpperCase()} TO ${leg.position_effect.toUpperCase()} ${t.quantity * leg.ratio_quantity} × ${t.symbol} ${leg.expiration} $${leg.strike} ${leg.right.toUpperCase()} · multiplier ${leg.multiplier}`);
      }
      if (t.limit_price) line(`Limit premium: $${t.limit_price}${t.direction ? " net " + t.direction : " per share"}.`);
      if (t.limit_price && t.legs.length === 1) line(`Premium at limit: $${(Number(t.limit_price) * t.quantity * t.legs[0].multiplier).toFixed(2)}, plus broker fees.`);
      line("Review expiration and exercise exposure. An option can expire worthless or result in an underlying position.");
    }
    if (t.stop_price) line(`Stop trigger: $${t.stop_price}. A stop-market price is not a guaranteed fill price; a stop-limit can remain unfilled.`);
    if (t.type === "market") line("Market order: execution price is not capped. Review the current bid and ask.");
    const sessions = {regular_hours: "Regular hours", extended_hours: "Regular + extended hours",
      all_day_hours: "24 Hour Market (eligible stocks)", regular_curb_hours: "Regular + curb (eligible index options)",
      regular_curb_overnight_hours: "Regular + curb + overnight (eligible index options)"};
    line(`${(t.type || "limit").replaceAll("_", " ")} · ${sessions[t.market_hours] || t.market_hours} · ${t.time_in_force === "gtc" ? "good till canceled" : "good for day"}. Submission does not guarantee execution.`);
    if (t.type?.startsWith("stop_")) line("Stops only operate during regular market hours; they do not protect an after-hours position.");
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
    const hasAlerts = Object.keys(idea.preview.order_checks || {}).length > 0;
    let alertCheck;
    if (hasAlerts) {
      text("Broker alerts are shown above. Acknowledging them does not override broker eligibility, funds, or collateral requirements; the broker may reject the order.");
      const alertLabel = document.createElement("label"); alertLabel.className = "acknowledge";
      alertCheck = document.createElement("input"); alertCheck.type = "checkbox";
      const alertWords = document.createElement("span"); alertWords.textContent = "I have read and acknowledge these exact broker alerts.";
      alertLabel.append(alertCheck, alertWords); box.append(alertLabel);
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
    const enable = () => { button.disabled = !check.checked || (hasAlerts && !alertCheck.checked); };
    check.onchange = enable;
    if (alertCheck) alertCheck.onchange = enable;
    button.onclick = async () => {
      if (!check.checked || (hasAlerts && !alertCheck.checked)) return;
      button.disabled = check.disabled = true;
      if (alertCheck) alertCheck.disabled = true;
      notice("Checking this exact order and submitting your request. Wait for the broker response; do not submit again elsewhere.");
      try {
        const response = await api(`/api/trades/${idea.id}/submit`, {
          terms_hash: idea.terms_hash, preview_hash: idea.preview_hash, acknowledge_real_order: true,
          acknowledge_broker_alerts: !!alertCheck?.checked
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
