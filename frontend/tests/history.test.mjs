import assert from 'node:assert/strict';
import {test} from 'node:test';
import {addToHistory, addBatchToHistory, listHistory, clearHistory} from '../src/lib/history.ts';
const memory = new Map();
globalThis.localStorage = {getItem: (k) => memory.get(k) ?? null, setItem: (k,v) => memory.set(k,v), removeItem: (k) => memory.delete(k)};
test('batch history survives a read and never stores document filenames', () => {
 clearHistory();
 addBatchToHistory({batch_id:'batch-1', name:'Учебный комплект', purpose:'internal', overall:null, created_at:'2026-11-11T08:00:00Z', documents_total:2, violations_red:0, violations_yellow:0, documents:[{filename:'never-store-this.pdf'}]});
 assert.equal(listHistory()[0].kind, 'batch');
 assert.equal(listHistory()[0].documents, 2);
 assert.equal(listHistory()[0].overall, null);
 assert.ok(![...memory.values()].join('').includes('never-store-this'));
});
test('malformed batch history is ignored', () => {
 memory.set('grif.history.v1', JSON.stringify([{report_id:'invalid',kind:'batch',name:'test',documents:-1}]));
 assert.deepEqual(listHistory(), []);
});

const report = {report_id:'report-1', purpose:'internal', overall:'green', created_at:'2026-11-12T08:00:00Z', violations:[]};
test('history retains every supported extension after storage round trip, without filenames', () => {
 for (const ext of ['pdf','docx','xlsx','xml','png','jpg','jpeg','tif','tiff']) {
  clearHistory();
  addToHistory({...report, filename:`synthetic-private-name.${ext.toUpperCase()}`});
  assert.equal(listHistory()[0].ext, `.${ext}`);
  assert.equal(listHistory()[0].kind, 'file');
  assert.ok(![...memory.values()].join('').includes('synthetic-private-name'));
 }
});
test('history accepts old extensionless entries and rejects unsupported stored extensions', () => {
 clearHistory();
 for (const filename of ['unknown.exe','file.pdf.exe','no-extension',null]) {
  addToHistory({...report, filename});
  assert.equal(listHistory()[0].ext,null);
 }
 assert.equal(listHistory()[0].kind,'text');
 const entry = {...listHistory()[0],kind:'file',ext:'.exe'};
 memory.set('grif.history.v1',JSON.stringify([entry]));
 assert.deepEqual(listHistory(),[]);
 memory.set('grif.history.v1',JSON.stringify([{...entry,ext:null}]));
 assert.equal(listHistory().length,1);
 memory.set('grif.history.v1',JSON.stringify([{...entry,kind:'text',ext:'.png'}]));
 assert.deepEqual(listHistory(),[]);
});
