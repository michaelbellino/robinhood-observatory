"use strict";
const el=id=>document.getElementById(id);
let csrf="", signedIn=false, pendingCommand=null, pendingTicket=null;
const stamp=value=>new Date(value).toLocaleString("en-US",{timeZone:"America/New_York",timeZoneName:"short"});
const usd=value=>new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:8}).format(Number(value));
function notice(message){el("test-notice").textContent=message;}
async function request(path,body){
 const options={credentials:"same-origin",headers:{}};
 if(body!==undefined){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf};options.body=JSON.stringify(body);}
 const response=await fetch(path,options),data=await response.json();
 if(!response.ok)throw new Error(response.status===401?"Sign in to use the workbench.":typeof data.detail==="string"?data.detail:"Check your ticket fields and try again.");
 return data;
}
function node(tag,text,className){const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;}
function showReply(data){const target=el("command-result");target.replaceChildren(node("p",data.reply));if(data.data)target.append(node("pre",JSON.stringify(data.data,null,2)));}
async function sendCommand(message){
 if(!signedIn){notice("Sign in to send a command.");return;}
 el("send-command").disabled=true;
 if(pendingCommand?.message!==message)pendingCommand={message,request_id:crypto.randomUUID()};
 try{const data=await request("/api/assistant",pendingCommand);pendingCommand=null;showReply(data);if(data.ticket)await refreshTickets();notice("Command complete. No brokerage order was sent.");}
 catch(error){notice(error.message);}finally{el("send-command").disabled=!signedIn;}
}
function renderTicket(ticket){
 const card=node("article",undefined,"ticket"),p=ticket.payload,t=p.terms;
 card.id=ticket.id;
 card.append(node("h3",`${t.asset==="option"?"Long option":"Stock"} · ${t.symbol} · ${usd(p.paper_debit_usd)} paper debit`),node("p",p.label,"small"));
 card.append(node("strong",ticket.status.replaceAll("_"," "),"ticket-state"),node("span",`Review deadline: ${stamp(ticket.expires_at)}`,"small"));
 const terms=node("dl");
 const fields=[["Action",`${t.side} ${t.quantity}${t.asset==="option"?" contract":" shares"}`],["Limit / time in force",`${usd(t.limit_price)} / ${t.time_in_force}`],["Fee allowance",usd(t.fees_allowance)],["Account",p.account]];
 if(t.asset==="option")fields.push(["Expiration",t.expiration],["Strike / type",`${t.strike} ${t.right}`],["Multiplier",String(t.multiplier)]);
 for(const [key,value] of fields)terms.append(node("dt",key),node("dd",value));
 card.append(terms,node("p",p.quote,"small"),node("p",p.limitations.join(". "),"muted small"));
 const details=node("details");details.append(node("summary","Ticket reference and exact recorded terms"),node("p",ticket.id),node("code",ticket.terms_hash),node("pre",JSON.stringify(p,null,2)));card.append(details);
 if(ticket.status==="awaiting_paper_review"){
  const label=node("label",undefined,"acknowledge"),check=node("input");check.type="checkbox";label.append(check,node("span","I reviewed these exact hypothetical terms. This is a paper test, not permission to trade."));
  const controls=node("div",undefined,"review-controls"),approve=node("button","Mark paper terms reviewed"),reject=node("button","Reject paper ticket");approve.disabled=reject.disabled=true;
  check.onchange=()=>{approve.disabled=reject.disabled=!check.checked;};
  for(const [button,decision] of [[approve,"reviewed"],[reject,"rejected"]]){
   const requestId=crypto.randomUUID();
   button.onclick=async()=>{approve.disabled=reject.disabled=true;try{await request(`/api/rehearsals/${ticket.id}/review`,{request_id:requestId,terms_hash:ticket.terms_hash,decision,acknowledge_paper_only:true});notice("Paper review recorded. Ticket remains unfilled; nothing was sent to Robinhood.");await refreshTickets();}catch(error){notice(error.message);approve.disabled=reject.disabled=!check.checked;}};
   controls.append(button);
  }card.append(label,controls);
 }
 return card;
}
async function refreshTickets(){const data=await request("/api/rehearsals");el("tickets").replaceChildren(...data.tickets.map(renderTicket));if(!data.tickets.length)el("tickets").textContent="No paper tests yet. Try ‘Test a stock ticket’ or ‘Test an option ticket’.";if(location.hash)document.getElementById(location.hash.slice(1))?.scrollIntoView({block:"start"});}
el("command-form").onsubmit=event=>{event.preventDefault();sendCommand(el("command").value.trim());};
for(const button of document.querySelectorAll("[data-command]"))button.onclick=()=>{el("command").value=button.dataset.command;sendCommand(button.dataset.command);};
el("reload-tickets").onclick=()=>refreshTickets().catch(error=>notice(error.message));
el("asset").onchange=()=>{const option=el("asset").value==="option";el("option-fields").hidden=!option;el("option-fields").disabled=!option;};
el("ticket-form").onsubmit=async event=>{
 event.preventDefault();const terms={asset:el("asset").value,symbol:el("symbol").value,quantity:el("quantity").value,limit_price:el("limit-price").value,fees_allowance:el("fees").value};
 if(terms.asset==="option")Object.assign(terms,{expiration:el("expiration").value,strike:el("strike").value,right:el("right").value,multiplier:Number(el("multiplier").value)});
 if(JSON.stringify(pendingTicket?.terms)!==JSON.stringify(terms))pendingTicket={request_id:crypto.randomUUID(),terms};
 el("create-ticket").disabled=true;
 try{await request("/api/rehearsals",pendingTicket);pendingTicket=null;notice("Synthetic paper ticket created. Review the exact terms below.");await refreshTickets();}catch(error){notice(error.message);}finally{el("create-ticket").disabled=!signedIn;}
};
(async()=>{try{const session=await request("/api/session");csrf=session.csrf||"";signedIn=session.scope==="control";el("test-signin").hidden=true;el("send-command").disabled=el("create-ticket").disabled=!signedIn;notice("Paper testing ready. $0 real money; no extra model or brokerage calls.");await refreshTickets();}catch(error){notice(error.message);}})();

