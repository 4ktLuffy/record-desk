// Inventory runs are immutable; reviews append to their own history.
let inventoryReport = null;
const inventoryAction=(message,fn)=>run(message,fn,'inventory-notice');
const inventoryRoles = ['date','product','warehouse','opening','received','sold','closing'];
async function inventoryLists() {
  const sources = await api('/api/datasets'), runs = await api('/api/investigations');
  for (const [id, rows, label] of [['inventory-source',sources,r=>r.name],['inventory-history',runs,r=>r.source_name+' · '+r.created]]) {
    const el=$(id), previous=el.value;
    el.innerHTML='<option value="">Choose…</option>'+rows.map(r=>`<option value="${esc(r.id)}">${esc(label(r))}</option>`).join('');
    if(rows.some(r=>r.id===previous))el.value=previous;
  }
}
async function inventoryColumns() {
  $('inventory-contract').checked=false;
  $('inventory-mapping').innerHTML='';
  if(!$('inventory-source').value)return;
  const d=await api('/api/dataset/'+$('inventory-source').value);
  $('inventory-mapping').innerHTML=inventoryRoles.map(role=>`<div><label for="inventory-map-${role}">${esc(role)}</label><select id="inventory-map-${role}"><option value="">Choose column</option>${d.report.columns.map((c,i)=>`<option value="${i}">${esc(c.name)}</option>`).join('')}</select></div>`).join('');
}
function renderInventory() {
  const r=inventoryReport;if(!r)return;
  const flags=r.results.filter(x=>x.flags.length);
  $('inventory-export').hidden=false;
  $('inventory-report').innerHTML=`<h3>${esc(r.source_name)}</h3><p>${esc(r.note)}</p><p>Run ${esc(r.id)} · ${esc(r.created)} · ${esc(r.version)}</p><p>Training through ${esc(r.cutoffs.train_end)} · calibration through ${esc(r.cutoffs.calibration_end)} · investigation through ${esc(r.cutoffs.test_end)}</p><div class="stats">${Object.entries(r.counts).map(([k,v])=>`<div class="stat"><strong>${v}</strong><span>${esc(k.replaceAll('_',' '))}</span></div>`).join('')}</div><details><summary>Source fingerprint, mapping and fitted profiles</summary><pre>${esc(JSON.stringify({sha256:r.source_sha256,mapping:r.mapping,headers:r.headers,profiles:r.profiles},null,2))}</pre></details><details><summary>Quarantine · ${r.quarantine.length} records</summary><p>Excluded from fitting, calibration and investigation. Original cells are retained. Showing first 100; download includes all.</p>${r.quarantine.slice(0,100).map(x=>`<p><strong>Source record ${x.record}</strong> — ${esc(x.reasons.join(' '))}</p><pre>${esc(JSON.stringify(x.raw))}</pre>`).join('')}</details><details><summary>History coverage and statistical eligibility · ${r.series.length} series</summary><pre>${esc(JSON.stringify(r.series,null,2))}</pre></details><h3>${flags.length} flagged records</h3><div class="compare-grid"><div><label for="inventory-series">Product / location</label><select id="inventory-series"><option value="">All series</option>${r.series.map((x,i)=>`<option value="${i}">${esc(x.product)} / ${esc(x.warehouse)}</option>`).join('')}</select></div><div><label for="inventory-filter">Review queue</label><select id="inventory-filter"><option value="flagged">Flagged records</option><option value="unreviewed">Flagged, no decision yet</option><option value="all">All investigated records</option><option value="reviewed">With review history</option></select></div></div><div id="inventory-timeline"></div><div id="inventory-queue"></div><label for="inventory-record">Record to inspect</label><select id="inventory-record"></select><div id="inventory-evidence"></div>`;
  $('inventory-record').onchange=renderInventoryRecord;
  $('inventory-series').onchange=renderInventoryQueue;
  $('inventory-filter').onchange=renderInventoryQueue;
  renderInventoryQueue();
}
function renderInventoryRecord(){
  const r=inventoryReport, record=Number($('inventory-record').value), row=r.results.find(x=>x.record===record);
  if(!row){$('inventory-evidence').innerHTML='';return;}
  const history=r.reviews.filter(x=>x.record===record);
  $('inventory-evidence').innerHTML=`<h3>Source record ${record}</h3><p>Opening ${row.opening} + received ${row.received} − sold ${row.sold} = expected closing ${row.expected_closing}. Observed closing: ${row.closing}; difference: ${row.delta} units.</p><p>Prior-day source record: ${row.prior_record??'unavailable'} · opening/previous closing difference: ${row.continuity_delta??'unknown'}. Statistical score: ${row.score===null?'Skipped — '+esc(row.statistical_skip_reason):row.score.toFixed(3)}.</p><details><summary>Original source cells</summary><pre>${esc(JSON.stringify(r.headers.map((h,i)=>({column:h,value:row.raw[i]})),null,2))}</pre></details><label for="inventory-decision">Review decision</label><select id="inventory-decision"><option>needs investigation</option><option>confirmed issue</option><option>expected event</option></select><label for="inventory-note">Evidence or context (required)</label><textarea id="inventory-note" maxlength="2000" rows="3"></textarea><button id="inventory-save-review">Append review decision</button><h4>Append-only review history</h4>${history.length?history.map(h=>`<p><strong>${esc(h.decision)}</strong> · ${esc(h.created)}<br>${esc(h.note)}</p>`).join(''):'<p>No review decisions yet.</p>'}`;
  $('inventory-save-review').onclick=()=>inventoryAction('Saving review history…',async()=>{const series=$('inventory-series').value,filter=$('inventory-filter').value;inventoryReport=await api('/api/investigation-review',{run_id:r.id,record,decision:$('inventory-decision').value,note:$('inventory-note').value});renderInventory();$('inventory-series').value=series;$('inventory-filter').value=filter==='unreviewed'?'reviewed':filter;renderInventoryQueue();$('inventory-record').value=String(record);renderInventoryRecord();notice('Review appended. Previous decisions remain in history.');});
}
$('inventory-refresh').onclick=()=>inventoryAction('Refreshing inventory workspace…',async()=>{await inventoryLists();notice('Datasets and runs refreshed.');});
$('inventory-source').onchange=()=>inventoryAction('Loading source columns…',async()=>{await inventoryColumns();notice('Map columns and confirm the stock contract.');});
$('inventory-demo').onclick=()=>inventoryAction('Saving synthetic inventory CSV…',async()=>{const d=await api('/api/investigation-demo',{});await inventoryLists();await refreshDatasets();$('inventory-source').value=d.dataset_id;await inventoryColumns();for(const role of inventoryRoles)$('inventory-map-'+role).value=String(d.mapping[role]);$('inventory-train').value=d.train_end;$('inventory-calibration').value=d.calibration_end;$('inventory-end').value=d.test_end;$('inventory-contract').checked=false;notice('Synthetic source ready, including two invalid records. Check the mapping and confirm the daily stock contract.');});
$('inventory-run').onclick=()=>inventoryAction('Validating source and saving investigation…',async()=>{if(!$('inventory-source').value)throw Error('Choose a saved CSV.');if(!$('inventory-contract').checked)throw Error('Confirm the stock contract first.');const mapping={};for(const role of inventoryRoles){const el=$('inventory-map-'+role);if(!el||el.value==='')throw Error('Map all seven fields.');mapping[role]=Number(el.value);}inventoryReport=await api('/api/investigation-run',{dataset_id:$('inventory-source').value,mapping,train_end:$('inventory-train').value,calibration_end:$('inventory-calibration').value,test_end:$('inventory-end').value,contract:'daily-whole-units-v1'});await inventoryLists();$('inventory-history').value=inventoryReport.id;renderInventory();notice('Investigation saved with source evidence and quarantine reasons.');});
$('inventory-history').onchange=()=>inventoryAction('Opening saved investigation…',async()=>{if(!$('inventory-history').value)return;inventoryReport=await api('/api/investigation/'+$('inventory-history').value);renderInventory();notice('Saved investigation reopened.');});
$('inventory-export').onclick=()=>{if(!inventoryReport)return;const url=URL.createObjectURL(new Blob([JSON.stringify(inventoryReport,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='investigation-'+inventoryReport.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
inventoryLists().catch(e=>notice(e.message,true));

function renderInventoryQueue(){
  const r=inventoryReport, key=$('inventory-series').value, series=key===''?null:r.series[Number(key)];
  const same=x=>!series||(x.product===series.product&&x.warehouse===series.warehouse);
  const reviewed=new Set(r.reviews.map(x=>x.record)), mode=$('inventory-filter').value;
  const rows=r.results.filter(x=>same(x)&&(mode==='all'||mode==='reviewed'&&reviewed.has(x.record)||mode==='flagged'&&x.flags.length||mode==='unreviewed'&&x.flags.length&&!reviewed.has(x.record)));
  $('inventory-evidence').innerHTML='';
  $('inventory-record').innerHTML='<option value="">Choose record</option>'+rows.map(x=>`<option value="${x.record}">#${x.record} · ${esc(x.date)} · ${esc(x.product)} / ${esc(x.warehouse)} · ${esc(x.flags.join(', ')||'No flags')}${reviewed.has(x.record)?' · reviewed':''}</option>`).join('');
  $('inventory-queue').innerHTML=`<p>${rows.length} records in this queue. Showing the first 100 here; the selector and download include all.</p><div class="table-scroll"><table class="result-table"><thead><tr><th>Source</th><th>Date / series</th><th>Checks</th><th>Review</th></tr></thead><tbody>${rows.slice(0,100).map(x=>`<tr><td><button data-record="${x.record}">#${x.record}</button></td><td>${esc(x.date)}<br>${esc(x.product)} / ${esc(x.warehouse)}</td><td>${esc(x.flags.join(', ')||'No flags')}<br>Quantity difference: ${x.delta}</td><td>${reviewed.has(x.record)?'History saved':'No decision'}</td></tr>`).join('')}</tbody></table></div>`;
  const chart=$('inventory-timeline');
  if(!r.timeline)chart.innerHTML='<p>This older saved run has no stored timeline. Its original results remain available. Create a new investigation to include a timeline.</p>';
  else if(!series)chart.innerHTML='<p>Select one product / location to explore its daily sales timeline.</p>';
  else {
    const flagged=new Set(r.results.filter(x=>x.flags.length).map(x=>x.record));
    chart.innerHTML=inventoryTimeline(r.timeline.filter(same).map(x=>({...x,flagged:flagged.has(x.record)})),r.cutoffs,r.profiles.find(same))+'<p class="small">Scroll the chart horizontally on small screens. Gaps are missing days, never zero-filled. The dashed line is the calibrated upper sales threshold; orange points have one or more checks flagged. Only the training period fits the profile. Select an investigation point to inspect its source.</p>';
  }
  const inspect=record=>{if(!rows.some(x=>String(x.record)===record)){$('inventory-filter').value='all';renderInventoryQueue();}$('inventory-record').value=record;renderInventoryRecord();};
  for(const el of document.querySelectorAll('#inventory-queue [data-record], #inventory-timeline [data-record]')){
    el.onclick=()=>inspect(el.dataset.record);
    if(el.tagName.toLowerCase()==='circle')el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect(el.dataset.record);}};
  }
}
document.addEventListener('datasets-updated',e=>{const el=$('inventory-source'),old=el.value;el.innerHTML='<option value="">Choose…</option>'+e.detail.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');el.value=old;});
