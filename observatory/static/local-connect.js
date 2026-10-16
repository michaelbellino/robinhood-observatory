"use strict";
const statusText=document.getElementById("local-status"), approve=document.getElementById("local-approve");
const params=new URLSearchParams(location.search), port=Number(params.get("port")), challenge=params.get("challenge");
let localCsrf="";
async function loadLocal(){
  const r=await fetch("/api/session",{credentials:"same-origin"});
  if(r.status===401){statusText.textContent="Sign in to authorize the helper.";document.getElementById("local-signin").hidden=false;return;}
  if(!r.ok)throw new Error("Sign-in could not be verified.");
  const s=await r.json();
  if(s.scope!=="control"||!s.csrf)throw new Error("The owner must authorize this helper.");
  localCsrf=s.csrf;approve.hidden=false;statusText.textContent="Ready for your approval.";
}
approve.onclick=async()=>{approve.disabled=true;try{
  if(!Number.isInteger(port)||port<1024||port>65535||!/^[a-f0-9]{64}$/.test(challenge))throw new Error("Launch the helper again.");
  const r=await fetch("/api/robinhood/local-enrollment",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":localCsrf},body:JSON.stringify({port,challenge})});
  const d=await r.json();if(!r.ok)throw new Error(d.detail||"Helper authorization failed.");
  // Fragment is never sent in an HTTP request or server access log.
  const target=new URL(`http://127.0.0.1:${port}/enroll`);target.hash=new URLSearchParams({ticket:d.ticket,challenge}).toString();
  location.assign(target.href);
}catch(e){statusText.textContent=e.message;approve.disabled=false;}};
loadLocal().catch(e=>{statusText.textContent=e.message;});
