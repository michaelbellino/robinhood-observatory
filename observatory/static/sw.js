"use strict";
self.addEventListener("install",()=>self.skipWaiting());
self.addEventListener("activate",event=>event.waitUntil(self.clients.claim()));
// No fetch handler or cache: account pages/data must never be cached offline.
self.addEventListener("push",event=>{
 let data;try{data=event.data.json();}catch{return;}
 const expired=Date.parse(data.expires_at)<=Date.now();
 // Always show a visible message, even for delayed pushes; stale alerts never imply an actionable idea.
 const title=expired?"Observatory update expired":data.title;
 const body=expired?"This update arrived late. Open the inbox for current information.":data.body;
 event.waitUntil(self.registration.showNotification(title,{body,tag:data.id,renotify:false,icon:"/static/observatory.svg",data:{url:data.url}}));
});
self.addEventListener("notificationclick",event=>{
 event.notification.close();
 const proposed=event.notification.data?.url||"/trades";
 const target=new URL(proposed,self.location.origin);
 const url=target.origin===self.location.origin&&target.pathname==="/trades"?target.href:new URL("/trades",self.location.origin).href;
 event.waitUntil(self.clients.openWindow(url));
});
