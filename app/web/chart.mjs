import {samplePath, splitAtCap} from './model.mjs';

export const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const blue = '#002FA7';

export class CentrogramChart {
  constructor(element, select, hover, error) {
    this.element = element;
    this.select = select;
    this.hover = hover;
    this.error = error;
    this.ranges = null;
    this.initialized = false;
    this.queue = Promise.resolve();
    this.layers = {envelope: true, curves: true};
    new ResizeObserver(() => this.draw()).observe(element);
  }

  setData(request, curves, selected, units, reset = false) {
    Object.assign(this, {request, curves, selected, units});
    if (reset) this.ranges = null;
    return this.draw();
  }

  draw() {
    if (!this.request || !window.Plotly) return Promise.resolve();
    this.queue = this.queue.then(() => this.render()).catch(this.error);
    return this.queue;
  }

  async render() {
    const {element, request, curves = [], selected, units, layers} = this;
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    const bound = p => { xmin = Math.min(xmin, p.cg); xmax = Math.max(xmax, p.cg); ymin = Math.min(ymin, p.weight); ymax = Math.max(ymax, p.weight); };
    request.envelope.forEach(bound);
    curves.forEach(c => c.points.forEach(bound));
    const padx = (xmax - xmin || 1) * .1, pady = (ymax - ymin || 1) * .08;
    const ranges = this.ranges ?? {x: [xmin - padx, xmax + padx], y: [ymin - pady, ymax + pady]};
    const scale = Math.max(1, element.clientWidth - 90) / Math.abs(ranges.x[1] - ranges.x[0]);
    const weightScale = Math.max(1, element.clientHeight - 67) / Math.abs(ranges.y[1] - ranges.y[0]);
    const traces = [];
    if (layers.envelope) {
      const points = [...request.envelope, request.envelope[0]];
      traces.push({x: points.map(p => p.cg), y: points.map(p => p.weight), type: 'scatter', mode: 'lines',
        name: 'Envelope', fill: 'toself', fillcolor: 'rgba(80,80,90,.045)', line: {color: '#777781', width: 1.5},
        hovertemplate: 'Envelope<br>CG %{x:,.2f}<br>Weight %{y:,.2f}<extra></extra>'});
    }
    const groups = new Map();
    const append = (key, path, index, text, color, width, dash) => {
      if (path.length < 2) return;
      if (!groups.has(key)) groups.set(key, {type: 'scattergl', mode: 'lines', x: [], y: [], text: [], customdata: [],
        line: {color, width, dash}, hovertemplate: '%{text}<br>Fuel %{customdata[1]:,.2f}<br>Gross weight %{y:,.2f}<br>CG %{x:,.3f}<extra></extra>'});
      const trace = groups.get(key);
      for (const p of samplePath(path, scale, .5, weightScale)) {
        trace.x.push(p.cg); trace.y.push(p.weight); trace.text.push(text); trace.customdata.push([index, p.fuel]);
      }
      trace.x.push(null); trace.y.push(null); trace.text.push(''); trace.customdata.push(null);
    };
    if (layers.curves) {
      curves.forEach((curve, i) => {
        const focus = i === selected, binding = curve.binding;
        const group = binding ? 'limiting' : focus ? 'selected' : 'background';
        const color = binding || focus ? blue : '#92929e';
        const text = `${escapeHTML(curve.tail)}${binding ? ' · limiting loading' : ''}<br>Individual cap ${curve.cap.toLocaleString(undefined, {maximumFractionDigits: 2})}<br>` +
          curve.items.map(item => `${escapeHTML(item.name)}: ${item.present ? `${item.weight.toLocaleString()} @ ${item.cg?.toLocaleString() ?? '—'}` : 'absent'}`).join('<br>');
        const [safe, beyond] = splitAtCap(curve);
        append(`${group}-safe`, safe, i, text, color, binding ? 2.8 : focus ? 2.2 : 1, 'solid');
        append(`${group}-beyond`, beyond, i, text, focus || binding ? blue : '#b0b0ba', focus || binding ? 1.6 : 1, 'dash');
        if (focus || binding) {
          const cap = {...safe.at(-1), fuel: curve.cap};
          const markers = [...curve.points, cap];
          traces.push({type: 'scatter', mode: 'markers', x: markers.map(p => p.cg), y: markers.map(p => p.weight),
            marker: {size: markers.map((_, j) => j === markers.length - 1 ? 10 : 4), color: blue,
              symbol: markers.map((_, j) => j === markers.length - 1 ? 'diamond' : 'circle')},
            text: markers.map(() => text), customdata: markers.map(p => [i, p.fuel]),
            hovertemplate: '%{text}<br>Fuel %{customdata[1]:,.2f}<br>Gross weight %{y:,.2f}<br>CG %{x:,.3f}<extra></extra>'});
        }
      });
    }
    // Backgrounds first; keep the selected path and its exact markers visible above them.
    const order = ['background-safe', 'background-beyond', 'selected-safe', 'selected-beyond', 'limiting-safe', 'limiting-beyond'];
    traces.splice(layers.envelope ? 1 : 0, 0, ...order.filter(k => groups.has(k)).map(k => groups.get(k)));
    const suffix = key => units?.[key] ? ` (${units[key]})` : '';
    const layout = {autosize: true, margin: {l: 70, r: 20, t: 15, b: 52}, showlegend: false,
      paper_bgcolor: '#FFFFFF', plot_bgcolor: '#FFFFFF', font: {family: 'Helvetica Neue, Helvetica, Arial, sans-serif', size: 11, color: '#686872'},
      hovermode: 'closest', hoverlabel: {bgcolor: '#FFFFFF', bordercolor: '#dedee3', font: {color: '#202124', size: 11}},
      dragmode: 'zoom', xaxis: {title: {text: `CG station${suffix('cg')}`, standoff: 18}, range: ranges.x, gridcolor: '#eeeef1', zeroline: false, automargin: true},
      yaxis: {title: {text: `Gross weight${suffix('weight')}`, standoff: 12}, range: ranges.y, gridcolor: '#eeeef1', zeroline: false, automargin: true}};
    await Plotly.react(element, traces, layout, {displaylogo: false, scrollZoom: true,
      modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d'], responsive: false});
    if (!this.initialized) {
      element.on('plotly_click', event => {
        const data = event.points?.[0]?.customdata;
        if (data) this.select(data[0]);
      });
      element.on('plotly_hover', event => {
        const data = event.points?.[0]?.customdata;
        if (data) this.hover(data[0]);
      });
      element.on('plotly_unhover', () => this.hover(null));
      element.on('plotly_relayout', event => {
        if (event['xaxis.autorange'] || event['yaxis.autorange']) { this.ranges = null; this.draw(); return; }
        if ('xaxis.range[0]' in event || 'yaxis.range[0]' in event || 'xaxis.range' in event || 'yaxis.range' in event) {
          this.ranges = {x: [...element.layout.xaxis.range], y: [...element.layout.yaxis.range]};
          this.draw();
        }
      });
      this.initialized = true;
    }
  }
}
