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
const copied=controls.copyText({...terms,type:'stop_limit',stop_price:'0.9'});
assert.match(copied,/no order submitted/);assert.match(copied,/BUY 1 TEST shares/);
assert.match(copied,/Limit: \$1 per share/);assert.match(copied,/Stop: \$0.9 \(underlying share price/);
const optionTicket=controls.copyText({...terms,asset:'option',direction:'credit',legs:[{side:'sell',position_effect:'open',ratio_quantity:1,expiration:'2030-01-18',strike:'10',right:'put',multiplier:100},{side:'buy',position_effect:'open',ratio_quantity:1,expiration:'2030-01-18',strike:'9',right:'put',multiplier:100}]});
assert.match(optionTicket,/SELL TO OPEN 1 × TEST 2030-01-18 \$10 PUT/);
assert.match(optionTicket,/BUY TO OPEN 1 × TEST 2030-01-18 \$9 PUT/);
assert.match(optionTicket,/net credit/);
const posts=[];
const idea={id:'fixture',status:'awaiting_review',terms_hash:'terms',preview_hash:'preview',payload:{terms},preview:{order_checks:{alert_type:'FIXTURE_WARNING'}}};
const rendered=controls.render(idea,null,async(path,body)=>{posts.push({path,body});},async()=>{},()=>{});
assert.equal(posts.length,0);
assert.equal(flatten(rendered).filter(x=>x.tagName==='button'||x.tagName==='input').length,0);
const unknown=controls.render(idea,{state:'unknown',retry_allowed:false},async()=>{throw new Error('Must not call');},async()=>{},()=>{});
assert.equal(flatten(unknown).filter(x=>x.tagName==='button'||x.tagName==='input').length,0);
assert.match(flatten(unknown).map(x=>x.textContent).join(' '),/Check this order in Robinhood/);
vm.runInContext(readFileSync('observatory/static/review-workflow.js','utf8'),context);
const review=vm.runInContext('ReviewWorkflow',context);
const research={evidence_mode:'paper',thesis:'Synthetic catalyst thesis',counterargument:'Synthetic contrary evidence',no_trade_alternative:'Wait for verified pricing',fees_usd:'1',slippage_usd:'0.5',execution_assumptions:'Conservative synthetic bid and ask',legs:[{contract:{id:'synthetic-contract'},quote:{bid:'0.4',ask:'0.6',received_at:'2030-01-01T12:00:00Z'}}],exits:[{purpose:'take_profit',condition:'Buy back at the target',basis:'option_premium',threshold:'0.1',action:'buy_to_close',mechanism:'alert_requires_owner_action',justification:'Synthetic hypothesis to test',failure_modes:'Owner action may be delayed'}],evidence:[{publisher:'Synthetic source',title:'Test evidence',published_at:'2030-01-01T11:00:00Z',retrieved_at:'2030-01-01T12:00:00Z',retrieval_status:'unverified',url:'https://example.test/source',claim:'Synthetic evidence only'}]};
const evidence=review.evidence({...idea,created_at:'2030-01-01T12:00:00Z',preview:null,payload:{terms,research_candidate:research,risk_assessment:{risk_basis:'synthetic',assignment_warning:'Assignment can create stock exposure',calculations:{payoff:{max_gain_usd:'48.5',max_loss_usd:'51.5',collateral_required_usd:'51.5',premium_cashflow_usd:'50',cashflow:'credit',breakeven_at_expiration:'99.5'}}}}});
const evidenceText=flatten(evidence).map(x=>x.textContent).join(' ');
assert.match(evidenceText,/Counterargument: Synthetic contrary evidence/);
assert.match(evidenceText,/Maximum loss at proposed entry: \$51.5/);
assert.match(evidenceText,/Credit received, not realized profit: \$50/);
assert.match(evidenceText,/basis option_premium/);
assert.match(evidenceText,/No trade alternative: Wait for verified pricing/);
assert.match(evidenceText,/IV unavailable/);
console.log('Owner controls: exact summaries, no submission action, ambiguous result warning passed.');

// Keep the manual handoff checks in the existing Python/CI UI test entrypoint.
await import("./manual_handoff_ui.mjs");
