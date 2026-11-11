import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ocrRanges} from '../src/components/ocrRanges.ts';
test('preserves uncertainty ranges and their overlap with findings', () => {
 assert.deepEqual(ocrRanges(20, [[1,5],[4,8]]), [[1,5],[4,8]]);
});
test('clamps out-of-range offsets and rejects invalid or empty spans', () => {
 assert.deepEqual(ocrRanges(10,[[-2,3],[8,20],[4,4],[6,2],[NaN,4],[1.5,3],[20,30]]), [[0,3],[8,10]]);
 assert.deepEqual(ocrRanges(0,[[0,5]]), []);
});
