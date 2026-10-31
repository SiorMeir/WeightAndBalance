export const fields = {
  envelope: [['cg', 'CG', 'number'], ['weight', 'Weight', 'number']],
  tails: [['id', 'Tail number', 'text'], ['weight', 'Weight', 'number'], ['cg', 'CG', 'number']],
  items: [['name', 'Item', 'text'], ['weight', 'Weight', 'number'], ['cg', 'CG', 'number'],
    ['weight_delta', 'Weight ±', 'number'], ['cg_delta', 'CG ±', 'number'], ['optional', 'Optional', 'checkbox']],
  fuel_flow: [['weight', 'Cumulative weight', 'number'], ['moment', 'Cumulative moment', 'number']],
};
export const titles = {envelope: 'Envelope', tails: 'Tail numbers', items: 'Items', fuel_flow: 'Fuel flow', options: 'Simulation options'};

export function parseRequest(input) {
  const value = typeof input === 'string' ? JSON.parse(input) : structuredClone(input);
  const errors = [];
  const fail = (field, problem) => errors.push({field, problem});
  const object = (v, keys, path) => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) { fail(path, 'Expected an object.'); return false; }
    for (const k of Object.keys(v)) if (!keys.includes(k)) fail(path ? `${path}.${k}` : k, 'Unknown field.');
    return true;
  };
  if (!object(value, [...Object.keys(fields), 'options'], '')) throw Object.assign(new Error('Expected a request object.'), {details: errors});
  const request = {};
  const limits = {envelope: [3, 200], tails: [1, 50], items: [0, 40], fuel_flow: [2, 500]};
  for (const [section, columns] of Object.entries(fields)) {
    const rows = value[section] === undefined && section === 'items' ? [] : value[section];
    request[section] = [];
    if (!Array.isArray(rows)) { fail(section, 'Expected an array.'); continue; }
    const [min, max] = limits[section];
    if (rows.length < min || rows.length > max) fail(section, `Use ${min}–${max} rows.`);
    rows.forEach((row, i) => {
      const path = `${section}.${i}`;
      if (!object(row, columns.map(c => c[0]), path)) return;
      const result = {};
      for (const [key, label, type] of columns) {
        const v = row[key] === undefined ? (key.endsWith('_delta') ? 0 : key === 'optional' ? false : undefined) : row[key];
        result[key] = v;
        if (type === 'number' && (typeof v !== 'number' || !Number.isFinite(v))) fail(`${path}.${key}`, `${label} must be a finite number.`);
        if (type === 'text' && (typeof v !== 'string' || !v.length)) fail(`${path}.${key}`, `${label} is required.`);
        if (type === 'checkbox' && typeof v !== 'boolean') fail(`${path}.${key}`, `${label} must be true or false.`);
      }
      if (section !== 'envelope' && (result.weight < 0 || (section === 'tails' && result.weight === 0))) fail(`${path}.weight`, 'Weight must be nonnegative (positive for a tail).');
      if (section === 'items') {
        if (result.weight_delta < 0 || result.weight_delta > result.weight) fail(`${path}.weight_delta`, 'Weight delta must be between zero and weight.');
        if (result.cg_delta < 0) fail(`${path}.cg_delta`, 'CG delta must be nonnegative.');
      }
      request[section].push(result);
    });
  }
  for (const [section, key] of [['tails', 'id'], ['items', 'name']]) {
    const seen = new Set();
    request[section].forEach((row, i) => {
      if (seen.has(row[key])) fail(`${section}.${i}.${key}`, 'Must be unique.');
      seen.add(row[key]);
    });
  }
  request.fuel_flow.forEach((p, i, rows) => {
    if (i === 0 && (p.weight !== 0 || p.moment !== 0)) fail('fuel_flow.0.weight', 'First fuel point must be (0, 0).');
    if (i > 0 && p.weight <= rows[i - 1].weight) fail(`fuel_flow.${i}.weight`, 'Fuel weights must strictly increase.');
  });
  const options = value.options === undefined ? {} : value.options;
  if (object(options, ['all_centrograms', 'max_steps'], 'options')) {
    request.options = {all_centrograms: options.all_centrograms === undefined ? false : options.all_centrograms,
      max_steps: options.max_steps === undefined ? 5000 : options.max_steps};
    if (typeof request.options.all_centrograms !== 'boolean') fail('options.all_centrograms', 'Must be true or false.');
    if (!Number.isInteger(request.options.max_steps) || request.options.max_steps < 1 || request.options.max_steps > 100000) fail('options.max_steps', 'Use an integer from 1 to 100,000.');
  }
  if (errors.length) throw Object.assign(new Error('Check the highlighted fields.'), {details: errors});
  return request;
}

export const signature = request => JSON.stringify(parseRequest(request));
export const isStale = (request, submitted) => submitted !== null && signature(request) !== signature(submitted);
export const resultHeading = result => result.status === 'incomplete' ? 'Unproven upper bound' : 'Robust fuel cap';

export function interpolate(a, b, t) {
  const weight = a.weight + (b.weight - a.weight) * t;
  const moment = a.weight * a.cg + (b.weight * b.cg - a.weight * a.cg) * t;
  return {weight, cg: moment / weight, fuel: a.fuel + (b.fuel - a.fuel) * t};
}

export function splitAtCap(curve) {
  const points = [...curve.points];
  let cap = points.find(p => p.fuel === curve.cap);
  if (!cap) {
    const i = points.findIndex(p => p.fuel > curve.cap);
    if (i <= 0) throw new Error('Cap is outside the fuel trajectory.');
    cap = {...interpolate(points[i - 1], points[i], (curve.cap - points[i - 1].fuel) / (points[i].fuel - points[i - 1].fuel)), ...curve.cap_point, fuel: curve.cap};
    points.splice(i, 0, cap);
  }
  const i = points.indexOf(cap);
  return [points.slice(0, i + 1), points.slice(i)];
}

export function samplePath(points, pixelsPerCG, tolerance = 0.5, pixelsPerWeight = 0) {
  if (!points.length) return [];
  const result = [points[0]];
  function segment(a, b) {
    // The maximum horizontal chord error occurs at W = sqrt(Wa * Wb).
    const t = Math.sqrt(a.weight) / (Math.sqrt(a.weight) + Math.sqrt(b.weight));
    const worst = interpolate(a, b, t);
    const error = Math.abs(worst.cg - (a.cg + (b.cg - a.cg) * t)) * pixelsPerCG;
    const length = Math.hypot((b.cg - a.cg) * pixelsPerCG, (b.weight - a.weight) * pixelsPerWeight);
    // Dense hit targets make long, nearly straight paths inspectable between fuel waypoints.
    if (error > tolerance || length > 16) {
      const mid = interpolate(a, b, 0.5);
      if (mid.fuel !== a.fuel && mid.fuel !== b.fuel) { segment(a, mid); segment(mid, b); return; }
    }
    result.push(b);
  }
  for (let i = 1; i < points.length; i++) segment(points[i - 1], points[i]);
  return result;
}

export const curveKey = curve => JSON.stringify([curve.tail, curve.items]);
export function centrograms(result, all = false) {
  const unique = new Map((all ? result.all_centrograms ?? [] : result.critical_centrograms ?? []).map(c => [curveKey(c), c]));
  if (result.limiting) unique.set(curveKey(result.limiting), result.limiting);
  return [...unique.values()].sort((a, b) => Number(b.binding) - Number(a.binding) || a.cap - b.cap);
}
