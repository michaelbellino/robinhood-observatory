"use strict";
// Read-only order details. Final placement happens manually in Robinhood.
const OwnerOrders = Object.freeze({
  contracts(quantity, ratio = 1) {
    // Whole option quantities can exceed JavaScript's safe integer range.
    const [coefficient, exponent = "0"] = String(quantity).toLowerCase().split("e");
    const [whole, fraction = ""] = coefficient.split(".");
    const digits = BigInt(whole + fraction), scale = Number(exponent) - fraction.length;
    const integer = scale >= 0 ? digits * 10n ** BigInt(scale) : digits / 10n ** BigInt(-scale);
    return (integer * BigInt(ratio)).toString();
  },
  copyText(t) {
    const lines = ["Robinhood manual-entry ticket — no order submitted by Observatory"];
    if (t.asset === "equity") {
      lines.push(`${t.side.toUpperCase()} ${t.dollar_amount ? "$" + t.dollar_amount + " of " + t.symbol : t.quantity + " " + t.symbol + " shares"}`);
    } else {
      for (const leg of t.legs) {
        lines.push(`${leg.side.toUpperCase()} TO ${leg.position_effect.toUpperCase()} ${this.contracts(t.quantity, leg.ratio_quantity)} × ${t.symbol} ${leg.expiration} $${leg.strike} ${leg.right.toUpperCase()} (ratio ${leg.ratio_quantity}; multiplier ${leg.multiplier}; contract ID ${leg.option_id || "unavailable"})`);
      }
    }
    if (t.asset === "option") lines.push(`Strategy quantity: ${t.quantity}; total leg contracts: ${t.legs.reduce((sum, leg) => sum + BigInt(this.contracts(t.quantity, leg.ratio_quantity)), 0n)}`);
    lines.push(`Order type: ${(t.type || "limit").replaceAll("_", " ")}`);
    if (t.limit_price) lines.push(`Limit: $${t.limit_price}${t.direction ? " net " + t.direction : t.asset === "option" ? " premium per underlying unit, " + (t.legs[0].side === "buy" ? "debit" : "credit") : " per share"}`);
    if (t.stop_price) lines.push(`Stop: $${t.stop_price} (${t.asset === "option" ? "option contract price" : "underlying share price"}; verify trigger basis in Robinhood)`);
    lines.push(`Session: ${t.market_hours.replaceAll("_", " ")}`);
    lines.push(`Time in force: ${t.time_in_force === "gtc" ? "good till canceled" : "good for day"}`);
    lines.push("Verify the account, exact contracts, current quotes, fees and broker alerts in Robinhood before placing an order.");
    return lines.join("\n");
  },
  packet(idea, at = Date.now()) {
    const t = idea.payload.terms;
    const quotes = t.asset === "equity" ? [idea.preview?.quote_data] : idea.preview?.option_quotes || [];
    const fresh = value => {const age = at - Date.parse(value); return Number.isFinite(age) && age >= -5000 && age <= 60000;};
    const coverage = t.asset === "equity" || (quotes.length === t.legs.length && new Set(quotes.map(q => q?.instrument_id)).size === t.legs.length && t.legs.every(leg => quotes.some(q => q?.instrument_id === leg.option_id)));
    const quoteFresh = coverage && quotes.length > 0 && quotes.every(q => q && Number.isFinite(Number(q.bid_price)) && Number.isFinite(Number(q.ask_price)) && Number(q.bid_price) > 0 && Number(q.ask_price) >= Number(q.bid_price) &&
      (t.asset === "equity" ? fresh(q.venue_bid_time) && fresh(q.venue_ask_time) : fresh(q.updated_at)));
    const current = idea.status === "ready_for_manual_submission" && Date.parse(idea.expires_at) > at && fresh(idea.preview_at) && quoteFresh;
    const lines = ["MANUAL REVIEW COPY — NOT A BROKER ORDER OR APPROVAL",
      `Proposal: ${idea.id} · version ${idea.version || 1}`,
      `Terms hash: ${idea.terms_hash || "unavailable"}`,
      `Account label: ${idea.account || "unavailable — verify in Robinhood"}`,
      `Status at copy: ${idea.status}; copied ${new Date(at).toISOString()}`,
      `Review deadline: ${idea.expires_at || "unavailable"}`,
      `Broker preview received: ${idea.preview_at || "unavailable"}`,
      current ? "Reviewed proposal only. Recheck live quotes and broker requirements before manual placement." : "NOT READY FOR PLACEMENT: review, quote freshness, or expiry is unresolved. Refresh and review before using these terms.",
      this.copyText(t)];
    for (const q of quotes.filter(Boolean)) lines.push(`Quote ${q.instrument_id || t.symbol}: bid ${q.bid_price ?? "unknown"}, ask ${q.ask_price ?? "unknown"}; source bid time ${q.venue_bid_time || q.updated_at || "unknown"}; source ask time ${q.venue_ask_time || q.updated_at || "unknown"}`);
    if (idea.broker_order_id) lines.push(`Already linked broker order: ${idea.broker_order_id}. Do not duplicate this order.`);
    lines.push("Copying or reviewing this ticket never places, modifies, or cancels an order. The Robinhood link is not a prefilled ticket.");
    return lines.join("\n");
  },
  handoff(idea, notice) {
    const box = document.createElement("details"), heading = document.createElement("summary"), area = document.createElement("textarea"), copy = document.createElement("button");
    heading.textContent = "Exact manual review copy (not a broker order)";
    area.readOnly = true; area.rows = 14; area.style.width = "100%";
    area.setAttribute("aria-label", "Exact order review packet; select to copy manually");
    area.value = this.packet(idea);
    copy.textContent = "Copy review packet";
    copy.onclick = async () => {
      copy.disabled = true; area.value = this.packet(idea); box.open = true;
      try {
        await navigator.clipboard.writeText(area.value);
        notice("Review packet copied. Verify its readiness warning and terms; no order was submitted.");
      } catch {
        area.focus(); area.select();
        notice("Clipboard unavailable. The exact review packet is selected below; use your device’s Copy command. No order was submitted.");
      } finally { copy.disabled = false; }
    };
    box.append(heading, area, copy); return box;
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
        line(`${leg.side.toUpperCase()} TO ${leg.position_effect.toUpperCase()} ${this.contracts(t.quantity, leg.ratio_quantity)} × ${t.symbol} ${leg.expiration} $${leg.strike} ${leg.right.toUpperCase()} · multiplier ${leg.multiplier}`);
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


