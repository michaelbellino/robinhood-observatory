"use strict";
const el=id=>document.getElementById(id),node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
let csrf="",signedIn=false,pendingProposal=null,submissions={};
const pendingOwnerTickets = {};
const stamp=v=>v?new Date(v).toLocaleString("en-US",{timeZone:"America/New_York",timeZoneName:"short"}):"Unavailable";
function notice(text){el("notice").textContent=text;}
async function api(path,body){const options={credentials:"same-origin",headers:{}};if(body!==undefined){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf};options.body=JSON.stringify(body);}const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(r.status===401?"Sign in to continue.":typeof data.detail==="string"?data.detail:Array.isArray(data.detail)?data.detail.map(x=>`${x.loc.slice(1).join(".")}: ${x.msg}`).join("; "):"Check the exact order fields and try again.");return data;}
function action(label,handler){const button=node("button",label);button.onclick=async()=>{button.disabled=true;try{await handler();}catch(e){notice(e.message);}finally{button.disabled=false;}};return button;}
function render(idea){const card=node("article"),p=idea.payload,t=p.terms,submission=submissions[idea.id];card.className="ticket";card.id=idea.id;
 card.append(node("h3",`${t.symbol} · ${t.asset} · ${t.dollar_amount ? "$"+t.dollar_amount : t.quantity+" "+(t.asset==="option"?"strategies/contracts":"shares")}`),node("strong",idea.status.replaceAll("_"," ")),node("p",`Review deadline: ${stamp(idea.expires_at)}`),node("p",p.rationale));
 const risks=node("p",p.risks);risks.className="risk";card.append(risks,node("p",`Methodology: ${p.methodology}`),node("p",`Sources: ${p.sources.join(" · ")}`));
 card.append(ReviewWorkflow.evidence(idea),OwnerOrders.summary(t));
 if(["awaiting_review","needs_review","expired"].includes(idea.status)&&(!submission||submission.retry_allowed)) card.append(ReviewWorkflow.editor(idea,api,load,notice));
 if(submission) controlsForStatus(card,idea);
 if(idea.preview){card.append(node("h4",`Broker preview · ${stamp(idea.preview_at)}`));if(idea.preview.market_data_disclosure){const disclosure=node("p",idea.preview.market_data_disclosure);disclosure.className="broker-disclosure";card.append(disclosure);}card.append(node("p","Review all broker alerts, quotes, fees and collateral below. Collateral describes the change against your existing positions and working orders."),node("pre",JSON.stringify(idea.preview,null,2)));}
 const controls=node("div");controls.className="review-controls";
 if(idea.status==="awaiting_review"&&(!submission||submission.retry_allowed)){
  controls.append(action("Refresh broker preview",async()=>{notice("Retrieving broker preview within the shared request limit. This can take about a minute.");await api(`/api/trades/${idea.id}/preview`,{});await load();notice("Current broker preview loaded. Review its terms and alerts.");}));
  controls.append(action("Reject idea",async()=>{await api(`/api/trades/${idea.id}/decision`,{request_id:crypto.randomUUID(),terms_hash:idea.terms_hash,decision:"reject",acknowledge_manual_submission:true});await load();}));
  if(idea.preview){const label=node("label"),check=node("input");check.type="checkbox";label.className="acknowledge";label.append(check,node("span","I reviewed the exact terms and broker alerts. I will enter and submit any order myself in Robinhood."));const fallback=node("details");fallback.append(node("summary","Enter this order in Robinhood"),label);card.append(fallback);const review=action("Prepare for Robinhood",async()=>{await api(`/api/trades/${idea.id}/decision`,{request_id:crypto.randomUUID(),terms_hash:idea.terms_hash,preview_hash:idea.preview_hash,decision:"prepare_in_robinhood",acknowledge_manual_submission:true});await load();notice("Review recorded. No order submitted. Enter and verify these terms in Robinhood.");});review.disabled=true;check.onchange=()=>review.disabled=!check.checked;fallback.append(review);}
 }
 if(idea.status==="ready_for_manual_submission"){
  card.append(node("p","No order has been submitted by Observatory. Verify the current market and exact contract details in Robinhood before submitting. This link opens Robinhood; it does not prefill the order."));
  const link=node("a","Open Robinhood");link.href="https://robinhood.com/";link.target="_blank";link.rel="noopener noreferrer";controls.append(link);
  controls.append(action("Copy exact order terms",async()=>{await navigator.clipboard.writeText(JSON.stringify(t,null,2));notice("Terms copied. Verify them in Robinhood.");}));
  card.append(node("p","Order linking is unavailable until account provenance is verified. Check order status in Robinhood."));
 }
 if(idea.broker_order)card.append(node("h4","Observed broker order"),node("pre",JSON.stringify(idea.broker_order,null,2)));
 card.append(OwnerOrders.render(idea,submission,api,load,notice),controls);return card;}
async function pushStatus(){const data=await api("/api/notifications");const active=data.devices.filter(d=>d.active);el("push-state").textContent=`${active.length} enabled device${active.length===1?"":"s"}`;el("test-push").disabled=!active.length;el("devices").replaceChildren(...data.devices.map(d=>{const row=node("div");row.className="device";row.append(node("span",`${d.active?"Enabled":"Disabled"} · ${stamp(d.created_at)}`));if(d.active)row.append(action("Disable device",async()=>{await api(`/api/notifications/disable/${d.id}`,{});await pushStatus();}));return row;}));el("deliveries").textContent=JSON.stringify(data.deliveries,null,2);el("push-detail").textContent="Push service acceptance does not confirm that your phone displayed the alert. Send a test and check your phone. ChatGPT event delivery is not connected yet.";}
async function load(){const data=await api("/api/trades");submissions=data.submissions||{};el("analysis-state").textContent=`Hosted AI: ${data.automatic_recommendations.replaceAll("_"," ")}. Observation analysis: ${data.analysis || "unavailable"}. Configured model and spending limits are unchanged.`;el("ideas").replaceChildren(...data.ideas.map(render));if(!data.ideas.length)el("ideas").textContent="No trade ideas yet. Automatic selection awaits your methodology; the review and notification workflow is ready for proposals.";await pushStatus();if(location.hash)document.getElementById(location.hash.slice(1))?.scrollIntoView({block:"start"});}
function controlsForStatus(card,idea){card.append(action("Reconcile with Robinhood",async()=>{notice("Reading broker order status. No order will be sent.");const result=await api(`/api/trades/${idea.id}/reconcile`,{});await load();notice(result.message||"Broker status checked.");}));}
el("enable-push").onclick=async()=>{try{if(!("serviceWorker" in navigator)||!("PushManager" in window))throw new Error("On iPhone, add this page to Home Screen, then open it there to enable notifications.");const permission=await Notification.requestPermission();if(permission!=="granted")throw new Error("Notifications are not allowed on this device.");await navigator.serviceWorker.register("/sw.js");const registration=await navigator.serviceWorker.ready;const {public_key}=await api("/api/notifications/key",{});const raw=atob(public_key.replace(/-/g,"+").replace(/_/g,"/")+"=".repeat((4-public_key.length%4)%4));const key=Uint8Array.from(raw,c=>c.charCodeAt(0));const subscription=await registration.pushManager.getSubscription()||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});await api("/api/notifications/subscribe",subscription.toJSON());await pushStatus();notice("Notifications enabled. Send a test to confirm receipt on your phone.");}catch(e){notice(e.message);}};
el("test-push").onclick=async()=>{try{const r=await api("/api/notifications/test",{});notice(`Test queued (${r.event_id}). Check your phone, then refresh delivery status.`);await pushStatus();}catch(e){notice(e.message);}};
el("refresh").onclick=()=>load().catch(e=>notice(e.message));
el("proposal-form").onsubmit=async event=>{event.preventDefault();try{const text=el("proposal-json").value;if(pendingProposal?.text!==text)pendingProposal={text,body:{...JSON.parse(text),request_id:crypto.randomUUID()}};await api("/api/trades",pendingProposal.body);pendingProposal=null;await load();notice("Proposal saved for review. No order submitted.");}catch(e){notice(e.message);}};
function stockControls() {
 const type=el("stock-type").value, limit=type==="limit"||type==="stop_limit", stop=type.startsWith("stop_");
 el("stock-limit-label").hidden=!limit;el("stock-limit").required=limit;
 el("stock-stop-label").hidden=!stop;el("stock-stop").required=stop;
 for(const option of el("stock-session").options)option.disabled=type!=="limit"&&option.value!=="regular_hours";
 if(type!=="limit")el("stock-session").value="regular_hours";
 el("stock-help").textContent=stop?"Stops operate during regular hours only. Stop-market execution is not price-capped; a stop-limit may not fill.":type==="market"?"Market execution is not price-capped. Fractional shares and dollar amounts are supported only during regular hours through this connection.":"Whole shares only for limit orders through this connection. Extended and overnight eligibility is checked with Robinhood; a limit order may remain unfilled.";
}
async function saveOwnerTicket(key,terms) {
 const text=JSON.stringify(terms);
 if(pendingOwnerTickets[key]?.text!==text)pendingOwnerTickets[key]={text,body:{request_id:crypto.randomUUID(),terms}};
 const item=await api("/api/trades/owner-ticket",pendingOwnerTickets[key].body);
 delete pendingOwnerTickets[key]; await load();
 notice("Order saved. Refresh its broker preview, then enter any order yourself in Robinhood.");
 el(item.id)?.scrollIntoView({behavior:"smooth",block:"start"});
}
el("stock-type").onchange=stockControls;stockControls();
el("stock-form").onsubmit=async event=>{
 event.preventDefault();el("save-stock").disabled=true;
 try {
  const type=el("stock-type").value, terms={asset:"equity",symbol:el("stock-symbol").value.trim().toUpperCase(),side:el("stock-side").value,type,market_hours:el("stock-session").value,time_in_force:el("stock-tif").value};
  terms[el("stock-size-mode").value]=el("stock-size").value;
  if(type==="limit"||type==="stop_limit")terms.limit_price=el("stock-limit").value;
  if(type.startsWith("stop_"))terms.stop_price=el("stock-stop").value;
  await saveOwnerTicket("stock",terms);
 } catch(e){notice(e.message);}finally{el("save-stock").disabled=!signedIn;}
};
el("advanced-form").onsubmit=async event=>{event.preventDefault();el("save-advanced").disabled=true;try{await saveOwnerTicket("advanced",JSON.parse(el("advanced-json").value));}catch(e){notice(e.message);}finally{el("save-advanced").disabled=!signedIn;}};
(async()=>{try{const session=await api("/api/session");csrf=session.csrf||"";signedIn=!!csrf&&session.scope==="control";el("signin").hidden=signedIn;el("enable-push").disabled=el("save-proposal").disabled=el("save-stock").disabled=el("save-advanced").disabled=!signedIn;await load();notice("Review inbox ready. Place any order manually in Robinhood.");}catch(e){notice(e.message);}})();


