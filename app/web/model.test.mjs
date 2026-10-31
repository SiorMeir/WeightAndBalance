import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseRequest, signature, isStale, interpolate, splitAtCap, samplePath, resultHeading, centrograms} from './model.mjs';

const example = JSON.parse(readFileSync(new URL('../../examples/light_twin.json', import.meta.url)));

test('request round-trip preserves all fields and defaults without changing the source', () => {
  const original = JSON.stringify(example);
  const request = parseRequest(example);
  assert.equal(signature(JSON.parse(JSON.stringify(request))), signature(example));
  assert.equal(JSON.stringify(example), original);
  assert.equal(request.items[0].optional, false);
  assert.equal(request.items[0].cg_delta, 0);
  assert.deepEqual(parseRequest({...example, items: []}).items, []);
});

test('invalid imports and row edits fail with field paths', () => {
  const cases = [
    [{...example, extra: true}, 'extra'],
    [{...example, items: null}, 'items'],
    [{...example, options: null}, 'options'],
    [{...example, options: {max_steps: 0}}, 'options.max_steps'],
    [{...example, options: {all_centrograms: null}}, 'options.all_centrograms'],
    [{...example, tails: [example.tails[0], example.tails[0]]}, 'tails.1.id'],
    [{...example, items: [{...example.items[0], weight_delta: 1000}]}, 'items.0.weight_delta'],
    [{...example, items: [{...example.items[0], cg_delta: null}]}, 'items.0.cg_delta'],
    [{...example, fuel_flow: [{weight: 1, moment: 1}, ...example.fuel_flow.slice(1)]}, 'fuel_flow.0.weight'],
    [{...example, fuel_flow: [...example.fuel_flow].reverse()}, 'fuel_flow.1.weight'],
    [{...example, tails: [{...example.tails[0], weight: Infinity}]}, 'tails.0.weight'],
  ];
  for (const [request, field] of cases) assert.throws(() => parseRequest(request), error => error.details.some(d => d.field === field), field);
  assert.throws(() => parseRequest('{bad json'));
});

test('interpolation is linear in weight and moment, not CG', () => {
  const a = {weight: 100, cg: 10, fuel: 0}, b = {weight: 300, cg: 30, fuel: 200};
  assert.deepEqual(interpolate(a, b, .5), {weight: 200, cg: 25, fuel: 100});
  assert.notEqual(interpolate(a, b, .5).cg, (a.cg + b.cg) / 2);
});

test('cap splitting preserves exact API cap points, original waypoints, and zero/full caps', () => {
  const points = [{weight: 100, cg: 10, fuel: 0}, {weight: 300, cg: 30, fuel: 200}];
  const capPoint = {weight: 200, cg: 25};
  const [safe, beyond] = splitAtCap({points, cap: 100, cap_point: capPoint});
  assert.deepEqual(safe.at(-1), {...capPoint, fuel: 100});
  assert.deepEqual(safe.at(-1), beyond[0]);
  assert.equal(safe[0], points[0]); assert.equal(beyond.at(-1), points[1]);
  assert.equal(splitAtCap({points, cap: 0})[0].length, 1);
  assert.equal(splitAtCap({points, cap: 200})[1].length, 1);
});

test('adaptive curves stay within half a pixel throughout every segment and after zoom', () => {
  const original = [{weight: 100, cg: 10, fuel: 0}, {weight: 2000, cg: 80, fuel: 1900}];
  for (const scale of [10, 100, 1000]) {
    const points = samplePath(original, scale);
    assert.equal(points[0], original[0]); assert.equal(points.at(-1), original[1]);
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      for (let j = 0; j <= 100; j++) {
        const t = j / 100;
        assert.ok(Math.abs(interpolate(a, b, t).cg - (a.cg + (b.cg - a.cg) * t)) * scale <= .500001);
      }
    }
  }
});

test('only changes to the submitted request make results stale', () => {
  const saved = parseRequest(example), changed = structuredClone(saved);
  assert.equal(isStale(saved, null), false);
  assert.equal(isStale(saved, example), false);
  changed.items[0].weight += 1; assert.equal(isStale(changed, saved), true);
  changed.items[0].weight -= 1; assert.equal(isStale(changed, saved), false);
});

test('straight centrograms retain hover targets between distant waypoints', () => {
  const path = [{weight: 100, cg: 10, fuel: 0}, {weight: 1100, cg: 10, fuel: 1000}];
  const sampled = samplePath(path, 1, .5, .5);
  assert.ok(sampled.length > 2);
  for (let i = 1; i < sampled.length; i++) assert.ok((sampled[i].weight - sampled[i - 1].weight) * .5 <= 16);
});

test('incomplete results never receive a robust-cap heading', () => {
  assert.equal(resultHeading({status: 'incomplete'}), 'Unproven upper bound');
  assert.equal(resultHeading({status: 'ok'}), 'Robust fuel cap');
});

test('limiting curves are deduplicated and all-mode includes every returned loading', () => {
  const limiting = {tail: 'A', items: [], cap: 1, binding: true};
  const other = {tail: 'B', items: [], cap: 2, binding: false};
  const result = {critical_centrograms: [{...limiting, binding: false}], limiting, all_centrograms: [other, limiting]};
  assert.deepEqual(centrograms(result), [limiting]);
  assert.deepEqual(centrograms(result, true), [limiting, other]);
});
