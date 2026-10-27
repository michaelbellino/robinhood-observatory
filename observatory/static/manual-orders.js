"use strict";
// Read-only order details. Final placement happens manually in Robinhood.
const OwnerOrders = Object.freeze({
  copyText(t) {
    const lines = ["Robinhood manual-entry ticket — no order submitted"];
    if (t.asset === "equity") {
      lines.push(`${t.side.toUpperCase()} ${t.dollar_amount ? "$" + t.dollar_amount + " of " + t.symbol : t.quantity + " " + t.symbol + " shares"}`);
    } else {
      for (const leg of t.legs) {
        lines.push(`${leg.side.toUpperCase()} TO ${leg.position_effect.toUpperCase()} ${t.quantity * leg.ratio_quantity} × ${t.symbol} ${leg.expiration} $${leg.strike} ${leg.right.toUpperCase()} (multiplier ${leg.multiplier})`);
      }
    }
    lines.push(`Order type: ${(t.type || "limit").replaceAll("_", " ")}`);
    if (t.limit_price) lines.push(`Limit: $${t.limit_price}${t.direction ? " net " + t.direction : " per share"}`);
    if (t.stop_price) lines.push(`Stop: $${t.stop_price} (${t.asset === "option" ? "option contract price" : "underlying share price"}; verify trigger basis in Robinhood)`);
    lines.push(`Session: ${t.market_hours.replaceAll("_", " ")}`);
    lines.push(`Time in force: ${t.time_in_force === "gtc" ? "good till canceled" : "good for day"}`);
    lines.push("Verify the account, exact contracts, current quotes, fees and broker alerts in Robinhood before placing an order.");
    return lines.join("\n");
  },
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
      if(submission.order_status) text(`Broker result: ${submission.order_status.replaceAll("_", " ")}`);
      if(submission.approved_at) text(`Approved ${new Date(submission.approved_at).toLocaleString()} · version ${submission.approval?.version || 1}`);
      if (submission.message) text(submission.message);
      if (submission.order) {
        text(`Robinhood order ${submission.order.id} · ${submission.order.state}`);
        const order = submission.order;
        text(`Filled quantity reported by broker: ${order.processed_quantity ?? order.cumulative_quantity ?? "not reported"}.`);
        if (order.average_price) text(`Average execution price reported by broker: $${order.average_price}.`);
        if (order.reject_reason) text(`Broker rejection reason: ${order.reject_reason}`);
      }
      if (!submission.retry_allowed) {
        if (submission.state === "unknown") text("Check this order in Robinhood before taking further action. Other submissions are blocked until the uncertainty is resolved.");
        return box;
      }
    }
    return box;
  }
});


