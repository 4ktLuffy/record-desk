/* Pure SVG renderer: dates are UTC; missing days break the line. No imputation. */
function inventoryTimeline(points, cutoffs, profile) {
  const day=86400000, start=Date.parse(cutoffs.train_end)-29*day, end=Date.parse(cutoffs.test_end);
  const data=points.filter(p=>p.period!=='earlier history').sort((a,b)=>a.date.localeCompare(b.date));
  if(!data.length)return '<p>No valid daily points in this series and window.</p>';
  const limit=profile?profile.median+profile.scale*profile.threshold:null;
  const max=Math.max(1,...data.map(p=>p.sold),limit??0)*1.1;
  const x=t=>70+800*(t-start)/Math.max(day,end-start), y=v=>245-190*v/max;
  const safe=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cuts=[start,Date.parse(cutoffs.train_end)+day/2,Date.parse(cutoffs.calibration_end)+day/2,end];
  let paths='', last=null;
  for(const p of data){const t=Date.parse(p.date);paths+=(last!==null&&t-last===day?' L ':' M ')+x(t).toFixed(2)+' '+y(p.sold).toFixed(2);last=t;}
  return `<svg class="inventory-chart" viewBox="0 0 920 305" role="group" aria-label="Daily sales in whole units with training, calibration and investigation periods"><title>Daily sales: gaps stay missing; the dashed line is the calibrated upper threshold.</title>${['training','calibration','investigation'].map((name,i)=>`<rect class="period-${name}" x="${x(cuts[i])}" y="35" width="${Math.max(0,x(cuts[i+1])-x(cuts[i]))}" height="210"/><text x="${(x(cuts[i])+x(cuts[i+1]))/2}" y="23" text-anchor="middle">${name}</text>`).join('')}${[0,.5,1].map(f=>`<line class="chart-grid" x1="70" x2="870" y1="${y(max*f)}" y2="${y(max*f)}"/><text x="60" y="${y(max*f)+4}" text-anchor="end">${Math.round(max*f)}</text>`).join('')}<text x="70" y="290">Units sold</text><path class="sales-line" d="${paths}"/>${limit===null?'':`<line class="chart-threshold" x1="70" x2="870" y1="${y(limit)}" y2="${y(limit)}"><title>Upper threshold: ${limit.toFixed(2)} units; flagged only when exceeded</title></line>`}${data.map(p=>`<circle class="chart-point ${p.flagged?'flagged':''}" cx="${x(Date.parse(p.date))}" cy="${y(p.sold)}" r="${p.flagged?5:3}" ${p.period==='investigation'?`tabindex="0" role="button" data-record="${p.record}" aria-label="Inspect record ${p.record}, ${safe(p.date)}, ${p.sold} units"`:''}><title>${safe(p.date)} · ${p.sold} units · source record ${p.record}${p.flagged?' · flagged':''}</title></circle>`).join('')}${[start,Date.parse(cutoffs.train_end),Date.parse(cutoffs.calibration_end),end].map((t,i)=>`<text x="${x(t)}" y="268" text-anchor="${i===0?'start':i===3?'end':'middle'}">${new Date(t).toISOString().slice(0,10)}</text>`).join('')}</svg>`;
}
if(typeof module!=='undefined')module.exports={inventoryTimeline};
