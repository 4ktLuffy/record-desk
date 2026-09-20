const assert=require('node:assert/strict');
const {inventoryTimeline}=require('../dist/timeline.js');
const cuts={train_end:'2026-01-30',calibration_end:'2026-02-13',test_end:'2026-03-06'};
const points=[{record:2,date:'2026-01-01',sold:0,period:'training'},
  {record:3,date:'2026-01-02',sold:5,period:'training'},
  {record:4,date:'2026-01-04',sold:7,period:'training'},
  {record:5,date:'2026-02-14',sold:30,period:'investigation',flagged:true}];
const html=inventoryTimeline(points,cuts,{median:5,scale:1,threshold:3.5});
const path=html.match(/<path class="sales-line" d="([^"]*)"/)[1];
assert.equal((path.match(/ M /g)||[]).length,3,'A missing day must break the sales line');
assert.equal((path.match(/ L /g)||[]).length,1,'Only consecutive observations may connect');
assert.ok(html.includes('data-record="5"'));
assert.ok(!html.includes('data-record="2"'),'Historical observations are not investigation review targets');
assert.ok(html.includes('Upper threshold: 8.50'));
assert.ok(!/NaN|Infinity/.test(html));
const zeros=inventoryTimeline([{record:2,date:'2026-01-01',sold:0,period:'training'}],cuts,null);
assert.ok(!/NaN|Infinity/.test(zeros),'An all-zero series must still plot');
assert.ok(!zeros.includes('class="chart-threshold"'),'No fitted profile must mean no threshold line');
assert.ok(inventoryTimeline([],cuts,null).includes('No valid daily points'));
assert.deepEqual(points.map(p=>p.date),['2026-01-01','2026-01-02','2026-01-04','2026-02-14']);
console.log('Timeline: missing-day gaps, provenance, zero values and unavailable profiles verified.');
