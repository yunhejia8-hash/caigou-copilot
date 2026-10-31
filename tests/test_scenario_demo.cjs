const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const root=__dirname+'/../apps/suke_ops/suke_ops/public/scenario-demo/';
const {calculate}=require(root+'engine.js');
test('unconfirmed early shipment cannot cover original deadlines; zero expedite has zero cost',()=>{
 const r=calculate(0);assert.equal(r.free,220);assert.equal(r.sales,0);assert.equal(r.originalShortage,540);assert.equal(r.cost,0);
});
test('150 confirmed conditional expedited pieces cover sales but not original production deadline',()=>{
 const r=calculate(150);assert.equal(r.sales,300);assert.equal(r.remaining,70);assert.equal(r.originalShortage,240);assert.equal(r.nextProduction,240);assert.equal(r.cost,480);
});
test('whole-batch thresholds preserve production shortage after sales allocation',()=>{
 assert.equal(calculate(79).sales,0);assert.equal(calculate(80).sales,300);
 assert.equal(calculate(100).nextProduction,0);assert.equal(calculate(119).nextProduction,0);assert.equal(calculate(120).nextProduction,240);
 for(const q of [-1,601,1.5,NaN,Infinity,'150'])assert.throws(()=>calculate(q));
});
test('six independent synthetic fixtures include evidence, departmental feedback and next actions',()=>{
 const c={window:{}};vm.createContext(c);vm.runInContext(fs.readFileSync(root+'cases.js','utf8'),c);
 assert.equal(c.window.SCENARIOS.length,6);
 for(const s of c.window.SCENARIOS){assert.ok(s.facts.length);assert.ok(s.plans.length || s.event?.plans.length);assert.ok(s.actions.length);for(const role of ['总控','采购','销售','库存','生产'])assert.ok(s.talk.some(t=>t[0]===role),s.title+role);}
 const html=fs.readFileSync(root+'index.html','utf8');assert.ok(html.indexOf('engine.js')<html.indexOf('app.js'));
});
