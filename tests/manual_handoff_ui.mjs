// Isolated synthetic UI tests; clipboard only, no API or broker calls.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.style={};}
  append(...children) {this.children.push(...children);}
  setAttribute(name,value) {this[name]=value;}
  focus() {this.focused=true;}
  select() {this.selected=true;}
}
const clipboard=[];
const navigator={clipboard:{writeText:async value=>clipboard.push(value)}};
const context=vm.createContext({document:{createElement:tag=>new Element(tag)},navigator});
vm.runInContext(readFileSync('observatory/static/manual-orders.js','utf8'),context);
const controls=vm.runInContext('OwnerOrders',context);
const at=Date.parse('2030-01-01T12:00:00Z');
const terms={asset:'option',symbol:'TEST',quantity:'2',limit_price:'0.25',type:'limit',direction:'credit',market_hours:'regular_hours',time_in_force:'gfd',legs:[{option_id:'fixture1',side:'sell',position_effect:'open',ratio_quantity:2,expiration:'2030-01-18',strike:'100',right:'put',multiplier:100},{option_id:'fixture2',side:'buy',position_effect:'open',ratio_quantity:1,expiration:'2030-01-18',strike:'95',right:'put',multiplier:100}]};
const idea={id:'synthetic',version:2,terms_hash:'fixture-hash',account:'Fixture account',status:'ready_for_manual_submission',expires_at:'2030-01-01T12:05:00Z',preview_at:'2030-01-01T11:59:55Z',payload:{terms},preview:{option_quotes:terms.legs.map(leg=>({instrument_id:leg.option_id,bid_price:'.2',ask_price:'.3',updated_at:'2030-01-01T11:59:50Z'}))}};
assert.equal(controls.contracts("999999999999999999", 3), "2999999999999999997");
assert.equal(controls.contracts("1E+2", 2), "200");
assert.equal(controls.contracts("100.000", 2), "200");
const packet=controls.packet(idea,at);
assert.match(packet,/NOT A BROKER ORDER OR APPROVAL/);
assert.match(packet,/version 2/);assert.match(packet,/Terms hash: fixture-hash/);
assert.match(packet,/Account label: Fixture account/);
assert.match(packet,/SELL TO OPEN 4 × TEST/);assert.match(packet,/total leg contracts: 6/);
assert.match(packet,/ratio 2; multiplier 100; contract ID fixture1/);
assert.match(packet,/Limit: \$0.25 net credit/);assert.match(packet,/good for day/);
assert.match(packet,/source bid time 2030-01-01T11:59:50Z/);
assert.doesNotMatch(packet,/NOT READY/);
for (const change of [{status:'needs_review'},{expires_at:'2030-01-01T11:59:00Z'},{preview_at:null},{preview:{option_quotes:idea.preview.option_quotes.slice(1)}},{preview:{option_quotes:idea.preview.option_quotes.map(q=>({...q,updated_at:'2029-12-31T12:00:00Z'}))}},{preview:{option_quotes:idea.preview.option_quotes.map(q=>({...q,ask_price:'Infinity'}))}}]) assert.match(controls.packet({...idea,...change},at),/NOT READY/);
assert.match(controls.packet({...idea,broker_order_id:'existing'},at),/Do not duplicate/);
const single=controls.copyText({...terms,direction:null,legs:[terms.legs[0]]});
assert.match(single,/premium per underlying unit, credit/);
const notices=[];const panel=controls.handoff(idea,text=>notices.push(text));
const [,area,button]=panel.children;
assert.equal(area.readOnly,true);assert.equal(button.disabled,undefined);
await button.onclick();assert.equal(clipboard.length,1);assert.equal(button.disabled,false);assert.equal(panel.open,true);
assert.match(clipboard[0],/NOT READY/); // copied at real clock, never trust stale rendered readiness
navigator.clipboard.writeText=async()=>{throw Error('denied');};
await button.onclick();assert.equal(area.focused,true);assert.equal(area.selected,true);assert.equal(button.disabled,false);assert.match(notices.at(-1),/Clipboard unavailable/);
await button.onclick();assert.equal(button.disabled,false); // repeated failed copy remains recoverable
console.log('Manual handoff: exact terms, provenance, stale warning, clipboard fallback and repeat flow passed.');
