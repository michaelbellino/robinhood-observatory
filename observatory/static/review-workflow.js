"use strict";
const ReviewWorkflow = Object.freeze({
  evidence(idea) {
    const box=document.createElement("section"), preview=idea.preview;
    const line=(label,value)=>{const p=document.createElement("p");p.textContent=`${label}: ${value}`;box.append(p);};
    line("Account",idea.account || "Unbound — create a new version before approval");
    line("Proposal version",idea.version || 1);
    line("Proposal created",new Date(idea.created_at).toLocaleString());
    const research = idea.payload.research_candidate;
    if (research) {
      const details = document.createElement("details"), title = document.createElement("summary");
      title.textContent = "Research evidence, risk and exit plan"; details.append(title); box.append(details);
      const fact = (label, value) => { const p = document.createElement("p"); p.textContent = `${label}: ${value ?? "unavailable"}`; details.append(p); };
      fact("Evidence mode", research.evidence_mode);
      for (const [label, key] of [["Thesis", "thesis"], ["Counterargument", "counterargument"], ["Invalidation", "invalidation"], ["Already priced in", "priced_in_assessment"], ["Catalyst time", "catalyst_at"], ["Timing uncertainty", "catalyst_timing_uncertainty"], ["Volatility", "volatility_comparison"], ["Skew", "skew_assessment"], ["Liquidity", "liquidity_assessment"], ["Event risk", "event_risk"], ["Portfolio effect", "portfolio_effect"], ["Assignment and exercise", "assignment_and_exercise"], ["Expiration / early assignment / ex-dividend / pin risk", "expiration_early_assignment_ex_dividend_pin"], ["Alternative strategy", "alternative_long_or_premium"], ["No trade alternative", "no_trade_alternative"]]) fact(label, research[key]);
      fact("Fees and slippage assumptions", `$${research.fees_usd} fees; $${research.slippage_usd} slippage. ${research.execution_assumptions}`);
      const risk = idea.payload.risk_assessment, payoff = risk?.calculations?.payoff;
      if (payoff) for (const [label, key] of [["Maximum gain at proposed entry", "max_gain_usd"], ["Maximum loss at proposed entry", "max_loss_usd"], ["Collateral required", "collateral_required_usd"], ["Assignment obligation", "assignment_obligation_usd"], ["Breakeven at expiration", "breakeven_at_expiration"]]) fact(label, payoff[key] == null ? "unavailable" : `$${payoff[key]}`);
      if (payoff) { fact(payoff.cashflow === "credit" ? "Credit received, not realized profit" : "Premium debit", `$${payoff.premium_cashflow_usd}`); if (payoff.gain_description) fact("Gain range", payoff.gain_description); if (payoff.shares_required) fact("Verified shares required", payoff.shares_required); }
      if (risk) fact("Risk basis", risk.risk_basis + ". " + risk.assignment_warning);
      for (const leg of research.legs) {
        const q = leg.quote;
        fact("Contract quote", `${leg.contract.id} · bid $${q.bid ?? "unavailable"}, ask $${q.ask ?? "unavailable"} · source ${q.quoted_at ?? q.bid_at ?? "unknown"}, received ${q.received_at}`);
        fact("Liquidity / volatility / Greeks", `Volume ${q.volume ?? "unavailable"}; open interest ${q.open_interest ?? "unavailable"}; IV ${q.implied_volatility ?? "unavailable"}; delta ${q.delta ?? "unavailable"}; gamma ${q.gamma ?? "unavailable"}; theta ${q.theta ?? "unavailable"}; vega ${q.vega ?? "unavailable"}; realized volatility ${research.realized_volatility ?? "unavailable"}`);
      }
      for (const trigger of research.exits) fact(trigger.purpose.replaceAll("_", " "), `${trigger.condition} · basis ${trigger.basis}${trigger.threshold == null ? "" : ", threshold " + trigger.threshold}${trigger.deadline ? ", deadline " + trigger.deadline : ""} · ${trigger.action} · ${trigger.mechanism}. ${trigger.justification} Failure modes: ${trigger.failure_modes}`);
      for (const source of research.evidence) fact(source.publisher, `${source.title} · published ${source.published_at}, retrieved ${source.retrieved_at} · ${source.retrieval_status} · ${source.url} · ${source.claim}`);
    }
    if(!preview) { line("Quote, estimated value and fees","Load a broker preview before approving"); return box; }
    const t=idea.payload.terms, q=preview.quote_data;
    if(q) {
      line("Bid / ask",`$${q.bid_price ?? "unavailable"} / $${q.ask_price ?? "unavailable"}`);
      line("Bid / ask source time",`${q.venue_bid_time || "unknown"} / ${q.venue_ask_time || "unknown"}`);
      const price=Number(t.side === "buy" ? q.ask_price : q.bid_price);
      if(t.dollar_amount) line("Requested value",`$${t.dollar_amount}; share quantity and execution price are uncertain`);
      else if(price>0 && Number.isFinite(price)) line("Estimated value",`$${(price * Number(t.quantity)).toFixed(2)} at the quoted ${t.side === "buy" ? "ask" : "bid"}, before fees; not an execution guarantee`);
    }
    for(const quote of preview.option_quotes || []) line("Contract quote time",`${quote.instrument_id}: ${quote.updated_at || "unknown"}`);
    line("Preview retrieved",new Date(idea.preview_at).toLocaleString());
    line("Fees",preview.fees ? JSON.stringify(preview.fees) : "Not supplied in this preview; do not assume zero");
    line("Final placement","Review and place any order yourself in Robinhood. An Observatory review is not a broker order or fill.");
    return box;
  },
  editor(idea,api,reload,notice) {
    const details=document.createElement("details"), title=document.createElement("summary"), form=document.createElement("form");
    title.textContent="Edit proposal — creates a new version"; details.append(title,form);
    const terms=structuredClone(idea.payload.terms), fields={};
    const add=(key,label,values)=>{
      const wrapper=document.createElement("label"), input=document.createElement(values?"select":"input");
      wrapper.textContent=label;
      if(values) for(const value of values) {const option=document.createElement("option");option.value=value;option.textContent=value.replaceAll("_"," ");input.append(option);}
      else {input.type="text";input.inputMode="decimal";}
      input.value=terms[key] ?? ""; wrapper.append(input);form.append(wrapper);fields[key]=input;
    };
    form.className="order-form";
    let advanced;
    if(terms.asset==="equity") {
      add("symbol","Symbol");fields.symbol.inputMode="text";
      add("side","Side",["buy","sell"]);
      add("quantity","Shares (leave empty for dollars)");add("dollar_amount","Dollar amount (leave empty for shares)");
      add("type","Order type",["limit","market","stop_limit","stop_market"]);
      add("limit_price","Limit price, where applicable");add("stop_price","Stop price, where applicable");
      add("market_hours","Session",["regular_hours","extended_hours","all_day_hours"]);
      add("time_in_force","Order duration",["gfd","gtc"]);
    } else {
      const label=document.createElement("label");label.textContent="Exact option terms";
      advanced=document.createElement("textarea");advanced.rows=14;advanced.value=JSON.stringify(terms,null,2);label.append(advanced);form.append(label);
    }
    const expiryLabel=document.createElement("label"), expiry=document.createElement("input");
    expiryLabel.textContent="New proposal valid for (minutes)";expiry.type="number";expiry.min=1;expiry.max=10080;expiry.value=15;expiry.required=true;expiryLabel.append(expiry);form.append(expiryLabel);
    const warning=document.createElement("p");warning.textContent="The old version and preview will be retired. Review the account, revised terms, and a new broker preview before approving. Order duration is separate from this review deadline.";warning.className="full-width";form.append(warning);
    const button=document.createElement("button");button.textContent="Save new version for review";form.append(button);
    let pending;
    form.onsubmit=async event=>{
      event.preventDefault();button.disabled=true;
      try {
        let edited=advanced ? JSON.parse(advanced.value) : {...terms};
        if(!advanced) for(const [key,input] of Object.entries(fields)) edited[key]=input.value.trim() || null;
        if(!advanced) edited.symbol=edited.symbol.toUpperCase();
        const draft={terms:edited,terms_hash:idea.terms_hash,valid_for_seconds:Number(expiry.value)*60};
        const signature=JSON.stringify(draft);
        if(pending?.signature!==signature)pending={signature,body:{...draft,request_id:crypto.randomUUID()}};
        const saved=await api(`/api/trades/${idea.id}/revise`,pending.body);
        await reload();location.hash=saved.id;notice("New version saved. No approval or order was carried forward.");
      } catch(error) { notice(error.message); } finally {button.disabled=false;}
    };
    return details;
  }
});

