// Source-level tests only: no browser, live page, or running server is accessed.
// Run with: node --test tests/test_frontend.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
const script = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const network = {
  source_id:'S', nodes:[{id:'S',x:0,y:0,candidate:false},{id:'N',x:10,y:0,candidate:true}],
  pipes:[{id:'P',start:'S',end:'N',length_m:10}],
  zones:[{id:'Z',label:'Test <room>',risk:1,polygon:[[0,0],[10,0],[10,10],[0,10]]}],
  import_details:{source_coordinates:[10,20],suggested_source_pressure_bar:7}
};
const solution = {
  placements:['N'],coverage_pct:50,min_active_pressure_bar:5,
  pressures_bar:{S:6,N:5},flows_lps:{P:5},zones:{Z:50},
  constraints:{pressure_margin_bar:1.5}
};

function harness() {
  const elements = new Map();
  for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    const tag = match[0], classes = new Set();
    const element = {
      id:match[1],value:tag.match(/\bvalue="([^"]*)"/)?.[1] || '',
      hidden:/\bhidden\b/.test(tag),disabled:/\bdisabled\b/.test(tag),
      textContent:'',innerHTML:'',style:{},files:[],clientWidth:900,clientHeight:480,
      listeners:{},attributes:{},valid:true,
      classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),
        toggle:(c,on)=>on?classes.add(c):classes.delete(c),contains:c=>classes.has(c)},
      setAttribute(k,v){this.attributes[k]=v;},
      toggleAttribute(k,on){if(on)this.attributes[k]='';else delete this.attributes[k];},
      addEventListener(k,fn){this.listeners[k]=fn;},
      reportValidity(){return this.valid;},click(){},
      querySelector(){return this.span ||= {textContent:''};}
    };
    elements.set(element.id,element);
  }
  elements.get('cad-unit').value='1';
  const calls=[];
  let respond=()=>{throw Error('Unexpected API request');};
  const context=vm.createContext({
    document:{getElementById(id){assert.ok(elements.has(id),`Missing HTML element: ${id}`);return elements.get(id);}},
    window:{addEventListener(){}},FormData,
    setTimeout:fn=>{fn();return 0;},
    fetch:async(url,options={})=>{calls.push({url,...options});return respond(url,options);}
  });
  vm.runInContext(script,context);
  return {
    get:id=>elements.get(id),state:vm.runInContext('state',context),calls,
    run:code=>vm.runInContext(code,context),
    respond:fn=>{respond=fn;},
    input(id,value){elements.get(id).value=String(value);elements.get('setup-fields').listeners.input({target:{id}});},
    prepare(){this.state.file={name:'test.dxf'};this.state.network=structuredClone(network);},
    complete(overrides={}){
      this.respond(url=>url==='/api/optimize'?ok({job_id:'job'}):
        ok({status:'complete',result:{settings:{min_pressure_bar:3.5},solution:{...structuredClone(solution),...overrides}}}));
    }
  };
}
const ok = data=>({ok:true,json:async()=>data});

test('startup is empty and optimization remains disabled before import',()=>{
  const h=harness();
  assert.equal(h.get('optimize-button').disabled,true);
  assert.equal(h.get('empty-state').hidden,false);
  assert.equal(h.get('plan-svg').innerHTML,'');
});

test('pump detection populates raw coordinates and pressure without rewriting K',async()=>{
  const h=harness();h.prepare();h.get('budget').value='12';
  h.respond(()=>ok(structuredClone(network)));
  await h.run('importCAD()');
  assert.equal(h.get('source-x').value,10);
  assert.equal(h.get('source-y').value,20);
  assert.equal(h.get('source-pressure').value,7);
  assert.equal(h.get('budget').value,'12');
  assert.equal(h.state.cadDirty,false);
  assert.equal(h.get('optimize-button').disabled,false);
});

test('manual supply coordinates and pressure survive CAD reimport',async()=>{
  const h=harness();h.prepare();
  h.input('source-x',110);h.input('source-y',70);h.input('source-pressure',5);
  h.respond(()=>ok(structuredClone(network)));
  await h.run('importCAD()');
  assert.equal(h.calls[0].body.get('source_x'),'110');
  assert.equal(h.calls[0].body.get('source_y'),'70');
  assert.equal(h.get('source-pressure').value,'5');
  assert.equal(h.get('source-x').value,'110');
});

test('changing a water input immediately clears obsolete results',async()=>{
  const h=harness();h.prepare();h.complete();await h.run('optimizePlan()');
  h.input('min-pressure',6.5);
  assert.equal(h.state.result,null);
  assert.equal(h.get('metric-outlets').textContent,'—');
  assert.equal(h.get('pressure-check').hidden,true);
  assert.equal(h.get('plan-status').classList.contains('ready'),false);
  assert.equal(h.state.cadDirty,false);
});

test('geometry changes rebuild CAD before the next optimization',async()=>{
  const h=harness();h.prepare();h.input('diameter',120);
  h.respond(url=>url==='/api/import'?ok(structuredClone(network)):
    url==='/api/optimize'?ok({job_id:'job'}):
    ok({status:'complete',result:{settings:{min_pressure_bar:3.5},solution:structuredClone(solution)}}));
  await h.run('optimizePlan()');
  assert.deepEqual(h.calls.map(c=>c.url),['/api/import','/api/optimize','/api/jobs/job']);
  assert.equal(h.calls[0].body.get('diameter_mm'),'120');
  assert.equal(JSON.parse(h.calls[1].body).settings.diameter_override_mm,120);
});

test('near-threshold layouts show delivered pressure, requirement and margin',async()=>{
  const h=harness();h.prepare();h.complete({constraints:{pressure_margin_bar:.02}});
  await h.run('optimizePlan()');
  assert.equal(h.get('metric-pressure').innerHTML,'5.00<small>bar</small>');
  assert.match(h.get('pressure-check').textContent,/Required 3.50 bar · margin \+0.020 bar/);
  assert.equal(h.get('pressure-check').classList.contains('near-limit'),true);
  assert.equal(h.get('setup-fields').disabled,false);
  assert.equal(h.state.job,null);
});

test('same-layout pressure-threshold changes explain the changed margin',async()=>{
  const h=harness();h.prepare();h.complete();await h.run('optimizePlan()');
  h.input('min-pressure',4.5);
  h.respond(url=>url==='/api/optimize'?ok({job_id:'job'}):
    ok({status:'complete',result:{settings:{min_pressure_bar:4.5},
      solution:{...structuredClone(solution),constraints:{pressure_margin_bar:.5}}}}));
  await h.run('optimizePlan()');
  assert.match(h.get('run-message').textContent,/Same layout.*4.50 bar.*margin has changed/);
});

for (const [coverage,display] of [[100,'100'],[99.999,'&gt;99.99'],[.0003,'&lt;0.01'],[0,'0.00']]) {
  test(`coverage ${coverage} is displayed without misleading rounding`,async()=>{
    const h=harness();h.prepare();h.complete({coverage_pct:coverage});
    await h.run('optimizePlan()');
    assert.equal(h.get('metric-coverage').innerHTML,display+'<small>%</small>');
  });
}

test('infeasible optimization clears the old layout and reports the threshold',async()=>{
  const h=harness();h.prepare();h.complete();await h.run('optimizePlan()');
  h.input('min-pressure',7.1);
  h.respond(url=>url==='/api/optimize'?ok({job_id:'job'}):ok({status:'complete',result:{solution:null}}));
  await h.run('optimizePlan()');
  assert.equal(h.state.result.solution,null);
  assert.equal(h.get('metric-outlets').textContent,'—');
  assert.equal(h.get('pressure-check').hidden,true);
  assert.match(h.get('error-message').textContent,/no feasible layout.*7.10 bar/);
  assert.doesNotMatch(h.get('plan-svg').innerHTML,/delivered/);
});

test('a failed import clears the previous network and unlocks the setup',async()=>{
  const h=harness();h.prepare();h.complete();await h.run('optimizePlan()');
  h.respond(()=>({ok:false,json:async()=>({detail:'No pipe segments found'})}));
  await assert.rejects(h.run('importCAD()'),/No pipe segments/);
  assert.equal(h.state.network,null);
  assert.equal(h.state.result,null);
  assert.equal(h.get('plan-svg').innerHTML,'');
  assert.equal(h.get('setup-fields').disabled,false);
  assert.equal(h.state.phase,null);
});

test('failed or cancelled jobs do not leave inputs locked or retain results',async()=>{
  for (const status of ['failed','cancelled']) {
    const h=harness();h.prepare();
    h.respond(url=>url==='/api/optimize'?ok({job_id:'job'}):ok({status,error:'Injected solver failure'}));
    await h.run('optimizePlan()');
    assert.equal(h.state.result,null);
    assert.equal(h.state.job,null);
    assert.equal(h.state.phase,null);
    assert.equal(h.get('setup-fields').disabled,false);
    assert.equal(h.get('optimize-button').disabled,false);
    assert.equal(h.get('progress-track').hidden,true);
  }
});

test('invalid native form validation prevents an optimization request',async()=>{
  const h=harness();h.prepare();h.get('setup-form').valid=false;
  await h.get('setup-form').onsubmit({preventDefault(){}});
  assert.equal(h.calls.length,0);
});

test('CAD labels are escaped before insertion into SVG',()=>{
  const h=harness();h.prepare();h.run('renderPlan()');
  assert.match(h.get('plan-svg').innerHTML,/Test &lt;room&gt;/);
  assert.doesNotMatch(h.get('plan-svg').innerHTML,/Test <room>/);
});
