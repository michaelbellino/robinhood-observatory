// Browser-free unit checks of owner controls. All API calls below are in-memory mocks.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.textContent='';this.checked=false;this.disabled=false;}
  append(...children) {this.children.push(...children);}
}
const context=vm.createContext({document:{createElement:tag=>new Element(tag)}});
vm.runInContext(readFileSync('observatory/static/manual-orders.js','utf8'),context);
const controls=vm.runInContext('OwnerOrders',context);
const flatten=element=>[element,...element.children.flatMap(flatten)];
const terms={asset:'equity',symbol:'TEST',side:'buy',quantity:'1',limit_price:'1',type:'limit',market_hours:'all_day_hours',time_in_force:'gtc'};
const summary=flatten(controls.summary(terms)).map(x=>x.textContent).join(' ');
assert.match(summary,/24 Hour Market/);assert.match(summary,/good till canceled/);
const stops=flatten(controls.summary({...terms,type:'stop_limit',market_hours:'regular_hours',stop_price:'0.9'})).map(x=>x.textContent).join(' ');
assert.match(stops,/Stop trigger: \$0.9/);assert.match(stops,/Stops only operate during regular market hours/);
const dollar=flatten(controls.summary({...terms,type:'market',quantity:null,dollar_amount:'1.00',limit_price:null,market_hours:'regular_hours'})).map(x=>x.textContent).join(' ');
assert.match(dollar,/BUY \$1.00 of TEST/);assert.doesNotMatch(dollar,/NaN|null shares/);
const posts=[];
const idea={id:'fixture',status:'awaiting_review',terms_hash:'terms',preview_hash:'preview',payload:{terms},preview:{order_checks:{alert_type:'FIXTURE_WARNING'}}};
const rendered=controls.render(idea,null,async(path,body)=>{posts.push({path,body});return {message:'Synthetic result only'};},async()=>{},()=>{});
assert.equal(posts.length,0);
const inputs=flatten(rendered).filter(x=>x.tagName==='input');
const button=flatten(rendered).find(x=>x.tagName==='button');
assert.equal(inputs.length,2);assert.equal(button.disabled,true);
inputs[1].checked=true;inputs[1].onchange();assert.equal(button.disabled,true);
await button.onclick();assert.equal(posts.length,0);
inputs[0].checked=true;inputs[0].onchange();assert.equal(button.disabled,false);
await button.onclick();assert.equal(posts.length,1);
assert.equal(posts[0].body.acknowledge_broker_alerts,true);
assert.equal(posts[0].body.preview_hash,'preview');assert.equal(button.disabled,true);
const unknown=controls.render(idea,{state:'unknown',retry_allowed:false},async()=>{throw new Error('Must not call');},async()=>{},()=>{});
assert.equal(flatten(unknown).filter(x=>x.tagName==='button').length,0);
console.log('Owner controls: exact summaries, two acknowledgments, no automatic submission, ambiguous result lock passed.');
