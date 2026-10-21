const $ = id => document.getElementById(id);
const state = {file:null, network:null, result:null, lastRun:null, phase:null, job:null, cadDirty:false, sourceExplicit:false, pressureExplicit:false};
const cadFields = new Set(['cad-unit','source-x','source-y','diameter']);
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = id => Number($(id).value);
const fmt = (value, digits=1) => Number.isFinite(value) ? value.toFixed(digits) : '—';

async function api(url, options={}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    let message = data.detail || 'The request could not be completed.';
    if (Array.isArray(message)) message = message.map(item => `${item.loc.slice(1).join('.')}: ${item.msg}`).join('; ');
    throw new Error(message);
  }
  return data;
}
function error(message='') {
  $('error-message').textContent = message;
  $('error-message').hidden = !message;
}
function setPhase(phase) {
  state.phase = phase;
  $('setup-fields').disabled = !!phase;
  $('import-button').disabled = !!phase;
  $('empty-import').disabled = !!phase;
  $('optimize-button').disabled = phase === 'import' || phase === 'cancel' || (phase === 'optimize' && !state.job) || (!phase && !state.file);
  $('optimize-button').querySelector('span').textContent = phase === 'optimize' ? 'Cancel optimization' : phase === 'import' ? 'Reading CAD…' : phase === 'cancel' ? 'Cancelling…' : 'Optimize plan';
  $('plan-canvas').setAttribute('aria-busy', String(!!phase));
}
function getSettings() {
  return {algorithm:'ga', max_outlets:number('budget'), source_pressure_bar:number('source-pressure'),
    min_pressure_bar:number('min-pressure'), pump_capacity_lps:number('capacity'),
    outlet_demand_lps:number('demand'), coverage_radius_m:number('radius'),
    diameter_override_mm:number('diameter')};
}
function clearResult() {
  state.result = null;
  $('metric-coverage').innerHTML = '—<small>%</small>';
  $('coverage-check').hidden = true;
  $('coverage-check').textContent = '';
  $('metric-outlets').textContent = '—';
  $('metric-pressure').innerHTML = '—<small>bar</small>';
  $('pressure-check').hidden = true;
  $('pressure-check').textContent = '';
  $('pressure-check').classList.remove('near-limit');
  $('plan-status').textContent = state.network ? 'CAD IMPORTED' : 'WAITING FOR CAD';
  $('plan-status').classList.remove('ready');
  $('canvas-note').hidden = !state.network;
  $('canvas-note').textContent = 'Imported drawing · outlets will appear after optimization';
  $('progress-track').hidden = true;
  renderPlan();
}
async function importCAD() {
  if (!state.file) return;
  setPhase('import');error();
  state.network = null;state.lastRun = null;clearResult();
  $('file-label').textContent = state.file.name;
  $('run-message').textContent = 'Reading CAD geometry…';
  const body = new FormData();
  body.append('file', state.file);
  body.append('metres_per_unit', $('cad-unit').value);
  if (state.sourceExplicit) {
    body.append('source_x', $('source-x').value);
    body.append('source_y', $('source-y').value);
  }
  body.append('diameter_mm', $('diameter').value);
  try {
    state.network = await api('/api/import', {method:'POST',body});
    const imported = state.network.import_details;
    if (imported && !state.sourceExplicit) {
      $('source-x').value = imported.source_coordinates[0];
      $('source-y').value = imported.source_coordinates[1];
    }
    if (imported?.suggested_source_pressure_bar != null && !state.pressureExplicit) $('source-pressure').value = imported.suggested_source_pressure_bar;
    state.cadDirty = false;
    // K is the user's budget ceiling. Available CAD candidates limit selection,
    // but reimporting or moving the source must not silently rewrite that budget.
    $('network-label').textContent = `${state.network.nodes.filter(node=>node.candidate).length} candidates · ${state.network.pipes.length} pipes`;
    $('run-message').textContent = 'Drawing imported. Your setup is ready.';
    clearResult();
  } catch (failure) {
    state.cadDirty = true;error(failure.message);
    $('run-message').textContent = 'Check your drawing units, supply point, and CAD layers.';
    $('network-label').textContent = 'CAD coordinates · metres';
    renderPlan();
    throw failure;
  } finally {setPhase(null);}
}
function renderPlan() {
  const network = state.network;
  $('empty-state').hidden = !!network;
  $('plan-svg').toggleAttribute('hidden', !network);
  if (!network) { $('plan-svg').innerHTML = '';return; }
  const points = [...network.nodes.map(node=>[node.x,node.y]), ...network.zones.flatMap(zone=>zone.polygon), ...(network.drawing_lines||[]).flat()];
  const x0 = Math.min(...points.map(point=>point[0])), x1 = Math.max(...points.map(point=>point[0]));
  const y0 = Math.min(...points.map(point=>point[1])), y1 = Math.max(...points.map(point=>point[1]));
  const width=Math.max(300,$('plan-canvas').clientWidth-48),height=Math.max(250,$('plan-canvas').clientHeight-48),margin=38;
  const scale=Math.min((width-2*margin)/Math.max(x1-x0,1),(height-2*margin)/Math.max(y1-y0,1));
  const left=(width-(x1-x0)*scale)/2,top=(height-(y1-y0)*scale)/2;
  const x=value=>left+(value-x0)*scale,y=value=>top+(y1-value)*scale;
  const nodes=Object.fromEntries(network.nodes.map(node=>[node.id,node]));
  const solution=state.result?.solution,selected=new Set(solution?.placements||[]);
  let drawing='';
  for (const zone of network.zones) {
    const path=zone.polygon.map(point=>`${x(point[0])},${y(point[1])}`).join(' ');
    const relative=zone.risk/Math.max(...network.zones.map(z=>z.risk),.001);
    const color=relative>=.7?'#d4a47b':relative>=.4?'#b9bf8c':'#b1c7a1';
    const zoneCoverage=solution?.zones[zone.id];
    drawing+=`<polygon points="${path}" fill="${color}" fill-opacity=".10" stroke="${color}" stroke-opacity=".4" stroke-dasharray="4 4" stroke-width=".8"><title>${escapeHTML(zone.label)} · risk ${zone.risk}${zoneCoverage!==undefined?' · '+fmt(zoneCoverage,2)+'% of zone area covered':''}</title></polygon>`;
  }
  for (const [a,b] of network.drawing_lines||[]) drawing+=`<line x1="${x(a[0])}" y1="${y(a[1])}" x2="${x(b[0])}" y2="${y(b[1])}" stroke="#6b7970" stroke-width="1.7"/>`;
  for (const pipe of network.pipes) {
    const a=nodes[pipe.start],b=nodes[pipe.end],flow=solution?.flows_lps[pipe.id];
    drawing+=`<line x1="${x(a.x)}" y1="${y(a.y)}" x2="${x(b.x)}" y2="${y(b.y)}" stroke="${flow&&Math.abs(flow)>.001?'#78986f':'#b5c2ab'}" stroke-width="${flow&&Math.abs(flow)>.001?2.2:1.5}"><title>${escapeHTML(pipe.id)} · ${fmt(pipe.length_m)} m${flow!==undefined?' · '+fmt(flow)+' L/s':''}</title></line>`;
  }
  for (const id of selected) {
    const node=nodes[id];drawing+=`<circle cx="${x(node.x)}" cy="${y(node.y)}" r="${number('radius')*scale}" fill="#7fa363" fill-opacity=".075" stroke="#97b478" stroke-width=".7" stroke-dasharray="3 5" stroke-opacity=".45"/>`;
  }
  for (const node of network.nodes) {
    const px=x(node.x),py=y(node.y),active=selected.has(node.id);
    if (node.id===network.source_id) drawing+=`<rect x="${px-7}" y="${py-7}" width="14" height="14" rx="3" fill="#659699" stroke="#fff" stroke-width="2"/><text x="${px}" y="${py+24}" text-anchor="middle" fill="#648a86" font-size="10" font-family="sans-serif">SUPPLY</text>`;
    else {
      const pressure=solution?.pressures_bar[node.id];
      const nearLimit=active && pressure-state.result.settings.min_pressure_bar<=.05;
      const color=nearLimit?'#a66e28':'#497b43';
      drawing+=`<circle cx="${px}" cy="${py}" r="${active?7:node.candidate?3:2}" fill="${active?color:node.candidate?'#e7edde':'#b6c2ad'}" stroke="${active?'#fff':'#9cac8c'}" stroke-width="${active?2:1}"><title>${escapeHTML(node.id)}${active?' · delivered '+fmt(pressure,3)+' bar · required '+fmt(state.result.settings.min_pressure_bar,2)+' bar':''}</title></circle>`;
      if(active) {
        const labelRight=px<width-105;
        drawing+=`<text x="${px+(labelRight?12:-12)}" y="${py<margin+15?py+24:py-9}" text-anchor="${labelRight?'start':'end'}" fill="${color}" font-size="10" font-family="sans-serif">${escapeHTML(node.id)} · ${fmt(pressure,2)} bar</text>`;
      }
    }
  }
  $('plan-svg').setAttribute('viewBox',`0 0 ${width} ${height}`);
  $('plan-svg').innerHTML=drawing;
}
async function optimizePlan() {
  error();
  if (!state.network || state.cadDirty) await importCAD();
  if (!state.network) return;
  clearResult();setPhase('optimize');
  $('progress-track').hidden=false;$('progress-fill').style.width='2%';
  $('plan-status').textContent='OPTIMIZING';
  $('run-message').textContent='Finding a layout that meets your water limits…';
  try {
    const settings=getSettings();
    const start=await api('/api/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network:state.network,settings})});
    state.job=start.job_id;setPhase('optimize');
    while (state.job) {
      const job=await api('/api/jobs/'+state.job);
      if (job.status==='complete') {
        state.result=job.result;
        const solution=job.result.solution;
        if (solution) {
          const coverage=solution.coverage_pct;
          const coverageText=coverage===100?'100':coverage>99.99?'&gt;99.99':
            coverage>0&&coverage<.01?'&lt;0.01':fmt(coverage,2);
          $('metric-coverage').innerHTML=coverageText+'<small>%</small>';
          $('coverage-check').hidden=false;
          $('coverage-check').textContent='Zone area · overlaps counted once';
          $('metric-outlets').textContent=solution.placements.length;
          $('metric-pressure').innerHTML=fmt(solution.min_active_pressure_bar,2)+'<small>bar</small>';
          const required=job.result.settings.min_pressure_bar;
          const margin=solution.constraints.pressure_margin_bar;
          $('pressure-check').hidden=false;
          $('pressure-check').textContent=`Required ${fmt(required,2)} bar · margin ${margin>=0?'+':''}${fmt(margin,3)} bar`;
          $('pressure-check').classList.toggle('near-limit',margin<=.05);
          $('plan-status').textContent='FEASIBLE LAYOUT';$('plan-status').classList.add('ready');
          $('canvas-note').hidden=false;$('canvas-note').textContent='Best feasible layout found · area + water checks passed';
          $('run-message').textContent='Plan optimized. All water and budget limits pass.';
          const previous=state.lastRun;
          const sameLayout=previous?.network===state.network &&
            previous.settings.min_pressure_bar!==settings.min_pressure_bar &&
            Object.keys(settings).every(key=>key==='min_pressure_bar'||settings[key]===previous.settings[key]) &&
            [...solution.placements].sort().join(',')===previous.placements;
          if(sameLayout) $('run-message').textContent=`Same layout: all selected outlets still meet ${fmt(required,2)} bar. The pressure margin has changed.`;
          else if(margin<=.05) $('run-message').textContent=`All limits pass. Lowest outlet is only ${fmt(margin,3)} bar above the required pressure.`;
          state.lastRun={network:state.network,settings,placements:[...solution.placements].sort().join(',')};
          $('progress-fill').style.width='100%';renderPlan();
        } else {
          $('plan-status').textContent='REVIEW SETUP';
          error(`The search found no feasible layout at a required outlet pressure of ${fmt(settings.min_pressure_bar,2)} bar. Review source pressure, demand, capacity, and outlet limits.`);
          $('run-message').textContent='Adjust your setup and optimize again.';
        }
        break;
      }
      if (job.status==='failed') throw new Error(job.error||'Optimization failed.');
      if (job.status==='cancelled') {clearResult();$('run-message').textContent='Optimization cancelled. Your drawing is ready.';break;}
      const progress=job.progress||{};
      $('progress-fill').style.width=Math.min(95,Math.max(3,100*(progress.evaluated||0)/(progress.evaluation_budget||250)))+'%';
      $('run-message').textContent=state.phase==='cancel'?'Finishing the current water check…':`Checking layouts · ${progress.evaluated||0} evaluated`;
      await new Promise(resolve=>setTimeout(resolve,400));
    }
  } catch(failure) {
    error(failure.message);$('plan-status').textContent='REVIEW SETUP';$('run-message').textContent='Review your inputs and try again.';
  } finally {state.job=null;setPhase(null);$('progress-track').hidden=true;}
}
$('import-button').onclick=$('empty-import').onclick=()=>{if(!state.phase)$('cad-file').click();};
$('cad-file').onchange=async()=>{
  const file=$('cad-file').files[0];if(!file)return;
  error();
  if(!file.name.toLowerCase().endsWith('.dxf')) {error('Choose an ASCII DXF file. Convert DWG to DXF first.');return;}
  if(file.size>4*1024*1024) {error('The CAD file must be smaller than 4 MB.');return;}
  state.file=file;state.cadDirty=true;
  if(!$('setup-form').reportValidity())return;
  try {await importCAD();} catch(_) { /* Import error is shown in the setup panel. */ }
};
$('setup-form').onsubmit=async event=>{
  event.preventDefault();
  if(state.job) {
    setPhase('cancel');
    try {await api('/api/jobs/'+state.job+'/cancel',{method:'POST'});}catch(failure){error(failure.message);setPhase('optimize');}
    return;
  }
  if(state.phase||!state.file||!$('setup-form').reportValidity())return;
  try {await optimizePlan();} catch(_) { /* CAD errors already have a visible explanation. */ }
};
$('setup-fields').addEventListener('input',event=>{
  if(state.phase)return;
  if(event.target.id==='source-x'||event.target.id==='source-y') state.sourceExplicit=true;
  if(event.target.id==='source-pressure') state.pressureExplicit=true;
  error();clearResult();
  if(cadFields.has(event.target.id)) {state.cadDirty=true;$('plan-status').textContent='DRAWING INPUTS CHANGED';}
  $('run-message').textContent=state.file?'Setup changed. Optimize to update your plan.':'Import a CAD drawing to begin.';
});
window.addEventListener('resize',renderPlan);
renderPlan();
