"use strict";
(() => {
 const byId=id=>document.getElementById(id);
 let csrf="", version=0, pending=null;
 const readList=(id,separator)=>byId(id).value.split(separator).map(x=>x.trim()).filter(Boolean);
 async function request(path,body){
  const options={credentials:"same-origin",headers:{}};
  if(body!==undefined){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf};options.body=JSON.stringify(body);}
  const response=await fetch(path,options), data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:Array.isArray(data.detail)?data.detail.map(x=>x.msg).join("; "):"Unable to update research limits.");
  return data;
 }
 function show(data){
  version=data.version;
  byId("mandate-state").textContent=data.mode.replaceAll("_"," ");
  byId("mandate-detail").textContent=data.account?`Account: ${data.account}. Version ${data.version}. ${data.blocker}`:data.blocker;
  byId("mandate-disable").disabled=!csrf||!data.enabled;
 }
 async function load(){show(await request("/api/research-mandate"));}
 byId("mandate-form").addEventListener("submit",async event=>{
  event.preventDefault();
  try {
   const expiration=new Date(byId("mandate-expires").value);
   if(Number.isNaN(expiration.valueOf()))throw new Error("Choose an expiration time.");
   const strategies=Array.from(document.querySelectorAll('input[name="mandate-strategy"]:checked')).map(e=>e.value);
   const body={expected_version:version,confirm_account_last4:byId("mandate-account").value.trim(),universe:byId("mandate-universe").value,
    symbols:readList("mandate-symbols",",").map(x=>x.toUpperCase()),excluded_symbols:readList("mandate-excluded",",").map(x=>x.toUpperCase()),
    exclusions:readList("mandate-exclusions",/\n/),strategies,proposed_budget_usd:byId("mandate-budget").value,max_loss_per_trade_usd:byId("mandate-per-risk").value,
    max_aggregate_loss_usd:byId("mandate-aggregate").value,max_collateral_usd:byId("mandate-collateral").value,
    min_holding_days:Number(byId("mandate-min-days").value),max_holding_days:Number(byId("mandate-max-days").value),
    notifications:byId("mandate-notifications").value,expires_at:expiration.toISOString(),acknowledge_recommendations_only:byId("mandate-ack").checked};
   const key=JSON.stringify(body);
   if(!pending||pending.key!==key)pending={key,body:{...body,request_id:crypto.randomUUID()}};
   const preview={...body,confirm_account_last4:`••••${body.confirm_account_last4}`};
   if(!window.confirm(`Confirm these research limits? No order will be placed.\n\n${JSON.stringify(preview,null,2)}`))return;
   show(await request("/api/research-mandate",pending.body));pending=null;
   byId("mandate-account").value="";byId("mandate-ack").checked=false;
   byId("mandate-detail").textContent+=" Saved; the recommendation pipeline is still blocked until all checks are verified.";
  } catch(error){byId("mandate-detail").textContent=error.message;}
 });
 byId("mandate-disable").addEventListener("click",async()=>{
  try {if(!window.confirm("Disable these research limits? No order will be changed."))return;
   show(await request("/api/research-mandate/disable",{request_id:crypto.randomUUID(),expected_version:version}));
  } catch(error){byId("mandate-detail").textContent=error.message;}
 });
 (async()=>{try {const session=await request("/api/session");if(session.scope!=="control"||!session.csrf)return;
  csrf=session.csrf;byId("mandate-save").disabled=false;await load();
 }catch(error){byId("mandate-detail").textContent=error.message;}})();
})();
