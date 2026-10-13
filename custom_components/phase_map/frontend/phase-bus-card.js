/*
 * Phase Bus Card for HA Phase Map
 * https://github.com/JanJoh/ha-phase-map-card
 *
 * A single-line diagram: the main fuse, one bus per phase, every load branching off the
 * phase it is on, with live or average power. Needs the HA Phase Map integration
 * (https://github.com/JanJoh/ha-phase-map).
 *
 * MIT License
 */

const CARD_VERSION = "0.8.0";

const DEFAULT_COLORS = { L1: "#b8622a", L2: "#3a7fc1", L3: "#4f8f36" };
const ESTIMATED_COLOR = "#8a5cc2";

// Layout constants (same as the prototype generator)
const BUS_GAP = 180;           // vertical distance between buses
const BUS_TOP = 64;
const BW = 92, BH = 58, GAP = 6, X0 = 168;
const TP_W = 112, TP_H = 104, TP_GAP = 4, TP_SP = 13, HOP_R = 5.5;
// Meters behind other meters get a panel of their own below, fed from their box
const PANEL_GAP = 46, FEED_STEP = 7;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtW(w, kwFrom = 10000) {
  if (w === null || w === undefined || Number.isNaN(w)) return "–";
  if (Math.abs(w) >= kwFrom) return `${+(w / 1000).toFixed(1)} kW`;
  return `${Math.round(w)} W`;
}

function splitName(name) {
  // Two lines of at most ~14 characters, breaking at spaces, then after "/" or "-"
  const s = String(name || "").trim();
  const MAX = 14;
  if (s.includes("|")) return s.split("|").slice(0, 2);
  if (s.length <= MAX) return [s];
  const tokens = s.split(/(?<=[\s/-])/);
  let a = "";
  let i = 0;
  while (i < tokens.length && (a + tokens[i]).trimEnd().length <= MAX) a += tokens[i++];
  if (!a) {
    a = s.slice(0, MAX - 1) + "-";
    return [a, ellipsize(s.slice(MAX - 1), MAX + 1)];
  }
  return [a.trimEnd(), ellipsize(tokens.slice(i).join("").trim(), MAX + 1)];
}

function ellipsize(t, n) {
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

class PhaseBusCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("phase-bus-card-editor");
  }

  static getStubConfig() {
    return { mode: "live", days: 10 };
  }

  setConfig(config) {
    if (config.mode && !["live", "average"].includes(config.mode)) {
      throw new Error("mode must be 'live' or 'average'");
    }
    this._config = { mode: "live", days: 10, ...config };
    this._resubscribe();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._resubscribe();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._resubscribe();
  }

  disconnectedCallback() {
    this._unsubscribe();
  }

  _unsubscribe() {
    if (this._unsub) {
      this._unsub.then((u) => u && u()).catch(() => {});
      this._unsub = null;
    }
    clearInterval(this._avgTimer);
    this._avgTimer = null;
  }

  async _resubscribe() {
    if (!this._hass || !this._config || !this.isConnected) return;
    const key = JSON.stringify([this._config.entry_id, this._config.mode, this._config.days]);
    if (key === this._subKey && this._unsub) return;
    this._subKey = key;
    this._unsubscribe();
    this._error = null;
    try {
      let entryId = this._config.entry_id;
      if (!entryId) {
        const entries = await this._hass.callWS({ type: "phase_map/entries" });
        if (!entries.length) throw new Error("no_entry");
        entryId = entries[0].entry_id;
      }
      this._entryId = entryId;
      this._unsub = this._hass.connection.subscribeMessage(
        (payload) => { this._live = payload; this._render(); },
        { type: "phase_map/subscribe", entry_id: entryId },
      );
      await this._unsub;
      if (this._config.mode === "average") {
        await this._loadAverage();
        this._avgTimer = setInterval(() => this._loadAverage(), 15 * 60 * 1000);
      }
    } catch (err) {
      this._error = err && (err.message || err.code) ? (err.message || err.code) : String(err);
      this._render();
    }
  }

  async _loadAverage() {
    try {
      this._avg = await this._hass.callWS({ type: "phase_map/average", entry_id: this._entryId, days: Number(this._config.days) || 10 });
    } catch (err) {
      this._avg = null;
      this._avgError = err && err.message ? err.message : String(err);
    }
    this._render();
  }

  _t(key) {
    const sv = (this._hass?.locale?.language || this._hass?.language || "en").startsWith("sv");
    const T = {
      main_fuse: sv ? "Huvudsäkring" : "Main fuse",
      unmetered: sv ? "Omätt|rest" : "Unmetered|rest",
      average: sv ? "medel" : "average",
      of: sv ? "av" : "of",
      est: sv ? "uppsk." : "est.",
      not_running: sv ? "går inte" : "not running",
      running: sv ? "går" : "running",
      detected: sv ? "upptäckt" : "detected",
      detected_tip: sv ? "Ingen mätare: uppskattad av detektorn från omätt ström över det normala" : "No meter: estimated by the detector from unmetered current above normal",
      no_entry: sv ? "Ingen HA Phase Map hittades. Lägg till integrationen först." : "No HA Phase Map found. Add the integration first.",
      loading: sv ? "Laddar…" : "Loading…",
      days: sv ? "dagar" : "days",
      n_phase: sv ? "{n}-fas" : "{n}-phase",
      all_on: sv ? "allt på {p}" : "all on {p}",
      live: sv ? "Live" : "Live",
      average_days: sv ? "Medel · {d} dagar" : "Average · {d} days",
      other: sv ? "Övrigt" : "Other",
      split_equal: sv ? "1 mätare, jämnt fördelad" : "1 meter, split equally",
      one_meter_on: sv ? "1 mätare, allt på {p}" : "1 meter, all on {p}",
      unverified: sv ? "{p}: ingen ström sedd här ännu (ej verifierad)" : "{p}: no current seen here yet (unverified)",
      moved: sv ? "Verkar sitta på {p}, inte {c}" : "Seems to be on {p}, not {c}",
    };
    return T[key] ?? key;
  }

  _color(phase, i) {
    const cfg = this._config.colors || {};
    return cfg[phase] || DEFAULT_COLORS[phase] || ["#b8622a", "#3a7fc1", "#4f8f36"][i % 3];
  }

  _model() {
    const live = this._live;
    if (!live) return null;
    const avgMode = this._config.mode === "average";
    const avg = this._avg;
    const order = live.phases_order;
    const v = live.voltage;
    const avgLoad = {};
    if (avgMode && avg) for (const l of avg.loads) avgLoad[l.id] = l;
    const sum = (o) => Object.values(o || {}).reduce((s, x) => s + (x || 0), 0);
    const items = {};
    for (const load of live.loads) {
      const w = avgMode ? (avgLoad[load.id]?.w || {}) : load.w;
      const perPhase = {};
      for (const p of load.phases) perPhase[p] = w[p] ?? null;
      const total = load.estimated ? null : sum(perPhase);
      const restW = (avgMode ? avgLoad[load.id]?.rest_w : load.rest_w) || {};
      items[load.id] = { ...load, perPhase, total, restW, children: [] };
    }
    // Detectors are drawn as loads too, live only: what they see is current above normal that no
    // meter accounts for, so it is taken out of "Unmetered" below
    const detected = avgMode ? [] : (live.detectors || []).filter((d) => Array.isArray(d.phases) && d.phases.length);
    for (const d of detected) {
      const perPhase = {};
      for (const p of d.phases) perPhase[p] = d.w?.[p] ?? null;
      items[`det_${d.id}`] = { id: `det_${d.id}`, name: d.name, phases: d.phases, detected: true, on: !!d.on,
        perPhase, total: d.power_w ?? null, restW: {}, children: [] };
    }
    const roots = [];
    for (const load of live.loads) {
      const parent = load.parent && items[load.parent];
      if (parent) parent.children.push(items[load.id]);
      else roots.push(items[load.id]);
    }
    roots.push(...detected.map((d) => items[`det_${d.id}`]));
    // One panel per distribution point: the main fuse, then every meter with meters behind it
    const panel = (members, o) => {
      const rows = Object.fromEntries(o.phases.map((p) => [p, []]));
      const multi = [];
      for (const it of members) {
        if (it.phases.length > 1) multi.push(it);
        else if (it.phases.length === 1 && rows[it.phases[0]]) rows[it.phases[0]].push(it);
      }
      for (const p of o.phases) {
        if (avgMode) rows[p].sort((a, b) => (b.total ?? -1) - (a.total ?? -1));
        const rest = o.rest[p];
        rows[p].push({ id: `rest_${o.key}_${p}`, name: o.restName, rest: true, phases: [p],
          total: rest == null ? null : Math.max(rest, 0) });
      }
      return { ...o, rows, multi };
    };
    const mainRest = {}, mainLabel = {}, warn = {};
    for (const p of order) {
      const ph = live.phases[p];
      mainRest[p] = avgMode ? avg?.phases?.[p]?.unmetered_w : (ph.unmetered_a == null ? null : ph.unmetered_a * v);
      for (const d of detected) if (mainRest[p] != null && d.w?.[p]) mainRest[p] -= d.w[p];
      if (avgMode) mainLabel[p] = `≈ ${fmtW(avg?.phases?.[p]?.total_w)} ${this._t("average")}`;
      else mainLabel[p] = ph.current_a == null ? "–" : `${ph.current_a.toFixed(1)} A ${this._t("of")} ${live.fuse_a} A`;
      warn[p] = !avgMode && ph.current_a != null && ph.current_a >= live.fuse_a - (live.margin_a ?? 1);
    }
    const panels = [panel(roots, { key: "main", main: true, phases: order, rest: mainRest,
      restName: this._t("unmetered"), title: [this._t("main_fuse")], subtitle: `${order.length} × ${live.fuse_a} A`,
      label: mainLabel, warn })];
    const addSubs = (pnl) => {
      for (const it of [...Object.values(pnl.rows).flat(), ...pnl.multi]) {
        if (!it.children || !it.children.length) continue;
        const label = {};
        for (const p of it.phases) label[p] = avgMode ? `≈ ${fmtW(it.perPhase[p])} ${this._t("average")}` : fmtW(it.perPhase[p]);
        const sub = panel(it.children, { key: it.id, feeder: it.id, phases: order.filter((p) => it.phases.includes(p)),
          rest: it.restW, restName: this._t("other"), title: splitName(it.name), subtitle: fmtW(it.total),
          label, warn: {} });
        panels.push(sub);
        addSubs(sub);
      }
    };
    addSubs(panels[0]);
    return { live, phases: order, panels, avgMode, avg };
  }

  // Positions inside one panel, relative to its top-left corner
  _layout(P) {
    const ph = P.phases;
    const busY = Object.fromEntries(ph.map((p, i) => [p, BUS_TOP + i * BUS_GAP]));
    const xs = {};
    let rowEnd = X0;
    for (const p of ph) {
      xs[p] = P.rows[p].map((_, j) => X0 + j * (BW + GAP));
      rowEnd = Math.max(rowEnd, X0 + P.rows[p].length * (BW + GAP) - GAP);
    }
    const lastBus = busY[ph[ph.length - 1]];
    const centres = P.multi.map((_, i) => rowEnd + 30 + TP_W / 2 + i * (TP_W + TP_GAP));
    const w = Math.round((P.multi.length ? centres[centres.length - 1] + TP_W / 2 : rowEnd) + 16);
    const boxTop3 = lastBus + 34 + BH + 34;
    const h = P.multi.length ? boxTop3 + TP_H + 12 : lastBus + 34 + BH + 20;
    return { busY, xs, centres, w, h, boxTop3, lastBus };
  }

  _movedMark(o, ld, xRight, yTop) {
    const pc = ld.phase_check;
    if (!pc || !pc.suspected) return;
    const tip = this._t("moved").replace("{p}", pc.suspected).replace("{c}", pc.phase);
    o.push(`<text class="moved" x="${xRight - 5}" y="${yTop + 13}" text-anchor="end"><title>${esc(tip)}</title>⚠ ${esc(pc.suspected)}?</text>`);
  }

  // Draw one panel at (ox, oy); returns where every box's bottom is, for feeder lines
  _panel(o, P, L, ox, oy) {
    const { busY, xs, centres, boxTop3, lastBus } = L;
    const phases = this._live.phases_order;
    const anchors = {};
    const col = (p) => this._color(p, phases.indexOf(p));
    o.push(`<g transform="translate(${ox},${oy})">`);
    const fuseTop = BUS_TOP - 40, fuseH = lastBus - BUS_TOP + 80;
    o.push(`<rect class="fuse${P.main ? "" : " feeder"}" x="18" y="${fuseTop}" width="104" height="${fuseH}" rx="8"/>`);
    P.title.forEach((t, k) => o.push(`<text class="fuse-t${P.main ? "" : " small"}" x="70" y="${BUS_TOP - 18 + k * 14}" text-anchor="middle">${esc(t)}</text>`));
    o.push(`<text class="fuse-s" x="70" y="${BUS_TOP - 2 + (P.title.length - 1) * 14}" text-anchor="middle">${esc(P.subtitle)}</text>`);
    const busEnd = L.w - 18;
    P.phases.forEach((p) => {
      const y = busY[p], c = col(p);
      o.push(`<line class="bus${P.main ? "" : " sub"}" x1="122" y1="${y}" x2="${busEnd}" y2="${y}" style="stroke:${c}"/>`);
      o.push(`<text class="ph" x="134" y="${y - 12}" style="fill:${c}">${esc(p)}</text>`);
      o.push(`<text class="ph-avg${P.warn[p] ? " warn" : ""}" x="162" y="${y - 12}">${esc(P.label[p] ?? "")}</text>`);
      P.rows[p].forEach((ld, j) => {
        const x = xs[p][j], cx = x + BW / 2, top = y + 34;
        const est = ld.estimated, det = ld.detected;
        o.push(`<line class="tap" x1="${cx}" y1="${y}" x2="${cx}" y2="${top}" style="stroke:${est || det ? ESTIMATED_COLOR : c}"/>`);
        o.push(`<circle cx="${cx}" cy="${y}" r="4" style="fill:${c}"/>`);
        o.push(`<rect class="box${ld.rest ? " rest" : ""}${est || det ? " est" : ""}${ld.children?.length ? " feeds" : ""}" x="${x}" y="${top}" width="${BW}" height="${BH}" rx="6" style="stroke:${est || det ? ESTIMATED_COLOR : c}">${det ? `<title>${esc(this._t("detected_tip"))}</title>` : ""}</rect>`);
        const lines = splitName(ld.name);
        const ty = lines.length === 2 ? top + 17 : top + 24;
        lines.forEach((l, k) => o.push(`<text class="nm" x="${cx}" y="${ty + k * 13}" text-anchor="middle">${esc(l)}</text>`));
        const val = est ? `${this._t("est")} ${fmtW(ld.estimated_w, 1000)}` : fmtW(ld.total);
        o.push(`<text class="w${!est && !ld.total ? " zero" : ""}" x="${cx}" y="${top + BH - 8}" text-anchor="middle">${esc(val)}</text>`);
        this._movedMark(o, ld, x + BW, top);
        anchors[ld.id] = { x: ox + cx, y: oy + top + BH };
      });
    });
    P.multi.forEach((ld, n) => {
      const xc = centres[n];
      const est = ld.estimated, det = ld.detected;
      ld.phases.forEach((p) => {
        const k = P.phases.indexOf(p);
        if (k < 0) return;
        const x = xc + (k - (P.phases.length - 1) / 2) * TP_SP;
        let d = `M${x},${busY[p]}`;
        for (const q of P.phases.slice(k + 1)) {
          const yb = busY[q];
          d += ` V${yb - HOP_R} A${HOP_R},${HOP_R} 0 0 1 ${x},${yb + HOP_R}`;
        }
        d += ` V${boxTop3}`;
        // A phase the load has never been seen drawing from is drawn dashed, with an open dot
        const unv = !est && !det && Array.isArray(ld.verified) && !ld.verified.includes(p);
        const tip = unv ? `<title>${esc(this._t("unverified").replace("{p}", p))}</title>` : "";
        o.push(`<path class="cond${est || det ? " est" : ""}${unv ? " unv" : ""}" d="${d}" style="stroke:${col(p)}">${tip}</path>`);
        o.push(unv
          ? `<circle class="unv-dot" cx="${x}" cy="${busY[p]}" r="4" style="stroke:${col(p)}">${tip}</circle>`
          : `<circle cx="${x}" cy="${busY[p]}" r="5" style="fill:${col(p)}"/>`);
        // What this phase draws, at the junction in the phase colour (measured channels only:
        // a one-meter load's split is a rule, not a reading)
        const pw = ld.perPhase[p];
        if (!est && !ld.meter && pw != null && Math.round(pw) >= 1) {   // a detector's share is its own reading
          o.push(`<text class="jw" x="${x + 7}" y="${busY[p] - 6}" style="fill:${col(p)}">${esc(fmtW(pw))}</text>`);
        }
      });
      o.push(`<rect class="box tp${est || det ? " est" : ""}${ld.children?.length ? " feeds" : ""}" x="${xc - TP_W / 2}" y="${boxTop3}" width="${TP_W}" height="${TP_H}" rx="6"${est || det ? ` style="stroke:${ESTIMATED_COLOR}"` : ""}>${det ? `<title>${esc(this._t("detected_tip"))}</title>` : ""}</rect>`);
      const txt = [];
      splitName(ld.name).forEach((l) => txt.push(["nm t3" + (est || det ? " estc" : ""), l]));
      txt.push(["nm small", this._t("n_phase").replace("{n}", ld.phases.length)]);
      txt.push(["w" + (!est && !ld.total ? " zero" : ""), est ? `${this._t("est")} ${fmtW(ld.estimated_w, 1000)}` : fmtW(ld.total)]);
      if (det) {
        txt.push(["nm small", `${this._t("detected")} · ${this._t(ld.on ? "running" : "not_running")}`]);
      } else if (ld.meter) {
        // one meter for several phases: say how it is counted, not a measured split
        const draw = ld.meter.draw;
        txt.push(["nm small", draw && draw !== "equal" ? this._t("one_meter_on").replace("{p}", draw) : this._t("split_equal")]);
      } else if (!est && ld.total) {
        // where the power goes, e.g. "all on L1" for a car that charges on one phase
        const active = ld.phases.filter((p) => (ld.perPhase[p] || 0) >= 1);
        if (active.length === 1 && ld.phases.length > 1) txt.push(["nm small", this._t("all_on").replace("{p}", active[0])]);
        else if (active.length > 1 && !ld.children?.length) {
          // the per-phase split; a box that feeds its own panel shows it there instead
          const split = active.map((p) => `${p} ${Math.round(ld.perPhase[p])}`).join(" · ");
          if (split.length * 5.6 <= TP_W - 10) txt.push(["nm small", split, active]);
        }
      }
      txt.slice(0, 6).forEach(([cls, t, split], k) => {
        // the per-phase split, each reading in its phase colour
        const body = split
          ? split.map((p, i) => `${i ? '<tspan class="sep"> · </tspan>' : ""}<tspan style="fill:${col(p)}">${esc(`${p} ${Math.round(ld.perPhase[p])}`)}</tspan>`).join("")
          : esc(t);
        o.push(`<text class="${cls}${split ? " split" : ""}" x="${xc}" y="${boxTop3 + 19 + k * 16}" text-anchor="middle">${body}</text>`);
      });
      anchors[ld.id] = { x: ox + xc, y: oy + boxTop3 + TP_H };
    });
    o.push("</g>");
    return anchors;
  }

  _svg(m) {
    const layouts = m.panels.map((P) => this._layout(P));
    const feeds = m.panels.filter((P) => P.feeder).length;
    const margin = feeds ? 14 + feeds * FEED_STEP : 0;
    const W = Math.max(...layouts.map((L) => L.w)) + margin;
    const tops = [];
    let y = 0;
    layouts.forEach((L, i) => { tops.push(y); y += L.h + (i < layouts.length - 1 ? PANEL_GAP : 0); });
    const H = y;
    const o = [];
    o.push(`<svg viewBox="0 0 ${W} ${H}" style="max-width:${W}px" role="img" aria-label="${esc(this._t("main_fuse"))}" xmlns="http://www.w3.org/2000/svg">`);
    const anchors = {};
    const body = [];
    m.panels.forEach((P, i) => Object.assign(anchors, this._panel(body, P, layouts[i], 0, tops[i])));
    // Feeder lines: from the box that measures a branch to that branch's panel, routed down the
    // right-hand margin so they never run through another panel
    let k = 0;
    m.panels.forEach((P, i) => {
      if (!P.feeder || !anchors[P.feeder]) return;
      const a = anchors[P.feeder];
      const xm = W - 10 - k++ * FEED_STEP;
      const yIn = tops[i] + BUS_TOP - 40;
      const yTurn = tops[i] - 16;
      o.push(`<path class="feed" d="M${a.x},${a.y} V${a.y + 10} H${xm} V${yTurn} H70 V${yIn}"/>`);
      o.push(`<circle class="feed-dot" cx="${a.x}" cy="${a.y}" r="3.5"/>`);
    });
    o.push(...body);
    o.push("</svg>");
    return o.join("");
  }

  _render() {
    if (!this.shadowRoot) return;
    const title = this._config?.title;
    let body;
    if (this._error) {
      body = `<div class="msg">${esc(this._error === "no_entry" ? this._t("no_entry") : this._error)}</div>`;
    } else {
      const m = this._model();
      if (!m || (m.avgMode && !m.avg)) {
        body = `<div class="msg">${esc(this._avgError || this._t("loading"))}</div>`;
      } else {
        const dets = (m.avgMode ? (m.live.detectors || []) : []).map((d) =>
          `<span class="chip${d.on ? " on" : ""}">${esc(d.name)}: ${esc(d.on ? this._t("running") : this._t("not_running"))}</span>`).join("");
        const sub = m.avgMode ? this._t("average_days").replace("{d}", Number(this._config.days)) : this._t("live");
        body = `<div class="sub">${esc(sub)}</div><div class="diagram">${this._svg(m)}</div>${dets ? `<div class="chips">${dets}</div>` : ""}`;
      }
    }
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { padding: 12px 12px 14px; }
        .head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; padding: 0 4px 6px; }
        .title { font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
        .sub { font-size: 12px; color: var(--secondary-text-color); padding: 0 4px 6px; }
        .diagram { overflow-x: auto; }
        svg { display:block; width:100%; min-width: 640px; height:auto; margin: 0 auto; }
        .fuse { fill: none; stroke: var(--primary-text-color); stroke-width: 1.5; }
        .fuse-t { font: 600 14px var(--ha-font-family-body, sans-serif); fill: var(--primary-text-color); }
        .fuse-s { font: 500 12px var(--code-font-family, monospace); fill: var(--secondary-text-color); }
        .bus { stroke-width: 6; stroke-linecap: round; }
        .bus.sub { stroke-width: 4; }
        .fuse.feeder { stroke-dasharray: none; stroke-width: 1.2; }
        .fuse-t.small { font-size: 12.5px; }
        .box.feeds { stroke-width: 2.5; }
        .feed { fill: none; stroke: var(--secondary-text-color); stroke-width: 1.5; stroke-dasharray: 5 4; }
        .feed-dot { fill: var(--secondary-text-color); }
        .moved { font: 700 10.5px var(--ha-font-family-body, sans-serif); fill: var(--warning-color, #e0a000); }
        .ph { font: 600 15px var(--code-font-family, monospace); }
        .ph-avg { font: 500 12px var(--code-font-family, monospace); fill: var(--secondary-text-color); }
        .ph-avg.warn { fill: var(--error-color, #db4437); font-weight: 700; }
        .tap { stroke-width: 2; }
        .cond { fill: none; stroke-width: 2.5; stroke-linecap: round; }
        .cond.est { stroke-dasharray: 6 4; }
        .cond.unv { stroke-dasharray: 3 4; stroke-width: 2; }
        .unv-dot { fill: var(--card-background-color, var(--ha-card-background, #fff)); stroke-width: 2; }
        .box { fill: var(--card-background-color, var(--ha-card-background, #fff)); stroke-width: 1.5; }
        .box.rest { fill: var(--secondary-background-color, transparent); stroke-dasharray: 4 3; }
        .box.est { stroke-dasharray: 5 4; }
        .box.tp { stroke: var(--primary-text-color); }
        .nm { font: 500 11.5px var(--ha-font-family-body, sans-serif); fill: var(--primary-text-color); }
        .nm.t3 { font-weight: 600; font-size: 12.5px; }
        .nm.estc { fill: ${ESTIMATED_COLOR}; }
        .nm.small { font-size: 10.5px; fill: var(--secondary-text-color); }
        .nm.split { font-weight: 600; }
        .nm.split .sep { fill: var(--secondary-text-color); font-weight: 400; }
        .jw { font: 600 10px var(--code-font-family, monospace); paint-order: stroke; stroke: var(--card-background-color, var(--ha-card-background, #fff)); stroke-width: 3px; stroke-linejoin: round; }
        .w { font: 600 13px var(--code-font-family, monospace); fill: var(--primary-text-color); }
        .w.zero { fill: var(--secondary-text-color); }
        .chips { display:flex; flex-wrap:wrap; gap:6px; padding: 8px 4px 0; }
        .chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--secondary-background-color, rgba(127,127,127,.12)); color: var(--secondary-text-color); }
        .chip.on { background: ${ESTIMATED_COLOR}; color: #fff; }
        .msg { padding: 16px 4px; color: var(--secondary-text-color); }
      </style>
      <ha-card>
        ${title ? `<div class="head"><span class="title">${esc(title)}</span></div>` : ""}
        ${body}
      </ha-card>`;
  }
}

class PhaseBusCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._renderForm();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._renderForm();
  }

  _renderForm() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => ({
        entry_id: "Main fuse (HA Phase Map entry)", mode: "Values", days: "Average over (days)", title: "Title",
        colors: "Phase colours",
      }[s.name] || s.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = PhaseBusCardEditor.fromForm(this._config, ev.detail.value);
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = PhaseBusCardEditor.toForm(this._config);
    this._form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "entry_id", selector: { config_entry: { integration: "phase_map" } } },
      { name: "mode", selector: { select: { mode: "dropdown", options: [
        { value: "live", label: "Live" }, { value: "average", label: "Average" }] } } },
      { name: "days", selector: { number: { min: 1, max: 90, mode: "box" } } },
      { name: "colors", type: "expandable", title: "Phase colours", schema:
        ["L1", "L2", "L3"].map((p) => ({ name: p, selector: { color_rgb: {} } })) },
    ];
  }

  // The card stores colours as "#rrggbb"; the colour picker works in [r, g, b]
  static hexToRgb(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || "").trim());
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : undefined;
  }

  static rgbToHex(rgb) {
    if (!Array.isArray(rgb) || rgb.length !== 3) return undefined;
    return "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0))).toString(16).padStart(2, "0")).join("");
  }

  static toForm(config) {
    const colors = {};
    for (const p of ["L1", "L2", "L3"]) colors[p] = PhaseBusCardEditor.hexToRgb((config.colors || {})[p] || DEFAULT_COLORS[p]);
    return { ...config, colors };
  }

  static fromForm(config, value) {
    const out = { ...config, ...value };
    const colors = {};
    for (const [p, rgb] of Object.entries(value.colors || {})) {
      const hex = PhaseBusCardEditor.rgbToHex(rgb);
      // only keep colours that differ from the defaults, so the YAML stays short
      if (hex && hex.toLowerCase() !== (DEFAULT_COLORS[p] || "").toLowerCase()) colors[p] = hex;
    }
    if (Object.keys(colors).length) out.colors = colors;
    else delete out.colors;
    return out;
  }
}

// The integration loads this card itself; a dashboard resource for the same card may load it a
// second time. The first copy wins instead of throwing "already been defined".
if (customElements.get("phase-bus-card")) {
  console.info(`PHASE-BUS-CARD ${CARD_VERSION}: already loaded, keeping the first copy`);
} else {
  customElements.define("phase-bus-card", PhaseBusCard);
  customElements.define("phase-bus-card-editor", PhaseBusCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "phase-bus-card",
    name: "Phase Bus Card",
    description: "Main fuse, one bus per phase, and every load on the phase it is connected to (HA Phase Map).",
    preview: false,
    documentationURL: "https://github.com/JanJoh/ha-phase-map-card",
  });
  console.info(`%c PHASE-BUS-CARD %c ${CARD_VERSION} `, "color:#fff;background:#3a7fc1;font-weight:700", "color:#3a7fc1");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { PhaseBusCard, PhaseBusCardEditor, splitName, fmtW };   // for the tests
}
