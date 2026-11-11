/*
 * Tüke Busz card for Home Assistant (bundled with the tukebusz integration).
 *
 *   type: custom:tukebusz-card
 *   mode: departures            # next A→B journeys from a tukebusz sensor
 *   entity: sensor.tuke_busz_fopalyaudvar_kertvaros
 *
 *   type: custom:tukebusz-card
 *   mode: timetable             # interactive timetable
 *   view: stop | line           # initial view (default: stop)
 *   stop: Főpályaudvar          # optional presets
 *   line: "15"
 *   start: "06:00"
 *   end: "09:00"                # omit start/end for the whole day
 */
const VERSION = "0.1.0";

const I18N = {
  hu: {
    now: "most", min: (m) => `${m} perc múlva`, hm: (h, m) => (m ? `${h} óra ${m} perc múlva` : `${h} óra múlva`),
    arrive: "érkezés", none: "Nincs több indulás a következő órákban.", noData: "Nincs adat.",
    stop: "Megálló", line: "Járat", date: "Dátum", from: "Ettől", to: "Eddig", allDay: "Egész nap",
    direction: "Irány", allDirections: "Minden irány", query: "Lekérdezés", loading: "Betöltés…",
    time: "Idő", headsign: "Irány", platform: "Kocsiállás", departures: "indulás", missingEntity: "Hiányzó entitás",
    minUnit: "perc", timetable: "Menetrend", pickStop: "Válassz megállót", pickLine: "Válassz járatot",
  },
  en: {
    now: "now", min: (m) => `in ${m} min`, hm: (h, m) => (m ? `in ${h} h ${m} min` : `in ${h} h`),
    arrive: "arrives", none: "No more departures in the coming hours.", noData: "No data.",
    stop: "Stop", line: "Line", date: "Date", from: "From", to: "To", allDay: "Whole day",
    direction: "Direction", allDirections: "All directions", query: "Show", loading: "Loading…",
    time: "Time", headsign: "Towards", platform: "Platform", departures: "departures", missingEntity: "Entity not found",
    minUnit: "min", timetable: "Timetable", pickStop: "Choose a stop", pickLine: "Choose a line",
  },
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fmtTime = (d, sec = false) => `${pad(d.getHours())}:${pad(d.getMinutes())}${sec ? ":" + pad(d.getSeconds()) : ""}`;

function relative(t, minutes) {
  if (minutes <= 0) return t.now;
  if (minutes < 60) return t.min(minutes);
  return t.hm(Math.floor(minutes / 60), minutes % 60);
}

let metadataPromise = null;
async function callService(hass, service, data) {
  const res = await hass.callWS({
    type: "call_service", domain: "tukebusz", service, service_data: data, return_response: true,
  });
  return res.response;
}
function getMetadata(hass) {
  if (!metadataPromise) {
    metadataPromise = callService(hass, "get_metadata", {}).catch((e) => { metadataPromise = null; throw e; });
  }
  return metadataPromise;
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 12px 16px 16px; }
  .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  .title { font-size: 1.15em; font-weight: 500; }
  .clock { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .row { display: grid; grid-template-columns: auto 1fr auto; gap: 4px 12px; align-items: center; padding: 8px 0; border-top: 1px solid var(--divider-color); }
  .row:first-of-type { border-top: none; }
  .badge { display: inline-block; min-width: 2.4em; text-align: center; font-weight: 700; border-radius: 6px; padding: 2px 6px; }
  .main { min-width: 0; }
  .when { font-weight: 600; font-variant-numeric: tabular-nums; }
  .sub { color: var(--secondary-text-color); font-size: 0.9em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rel { font-weight: 600; color: var(--primary-color); white-space: nowrap; text-align: right; }
  .rel.soon { color: var(--error-color, #db4437); }
  .muted { color: var(--secondary-text-color); padding: 8px 0; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: end; margin-bottom: 12px; }
  .controls label { display: flex; flex-direction: column; font-size: 0.8em; color: var(--secondary-text-color); gap: 2px; }
  .controls input, .controls select, .controls button {
    font: inherit; font-size: 0.95rem; padding: 6px 8px; border-radius: 6px; color: var(--primary-text-color);
    border: 1px solid var(--divider-color); background: var(--card-background-color); }
  .controls input[type=checkbox] { width: 18px; height: 18px; }
  .controls .chk { flex-direction: row; align-items: center; gap: 6px; font-size: 0.9em; }
  .controls button { background: var(--primary-color); color: var(--text-primary-color, #fff); border: none; cursor: pointer; }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 6px; overflow: hidden; }
  .seg button { background: transparent; color: var(--primary-text-color); border-radius: 0; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  th { font-weight: 500; color: var(--secondary-text-color); position: sticky; top: 0; background: var(--card-background-color); }
  td.stopname { position: sticky; left: 0; background: var(--card-background-color); max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
  td.empty { color: var(--disabled-text-color, #aaa); text-align: center; }
  h3 { margin: 12px 0 6px; font-size: 1em; font-weight: 500; }
  .err { color: var(--error-color, #db4437); }
`;

class TukeBuszCard extends HTMLElement {
  setConfig(config) {
    const mode = config.mode || (config.entity ? "departures" : "timetable");
    if (mode === "departures" && !config.entity) throw new Error("tukebusz-card: 'entity' is required in departures mode");
    this._config = { ...config, mode };
    this._built = false;
    this._controlsBuilt = false;
    this._metaLoaded = false;
    this.__state = null;
    this._lastState = null;
    if (this.shadowRoot) this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._t = I18N[(hass.locale?.language || hass.language || "hu").startsWith("hu") ? "hu" : "en"];
    if (!this._built) this._build();
    if (this._config.mode === "departures") this._renderDepartures();
    else if (first || (!this._metaLoaded && Date.now() - (this._metaFailedAt || 0) > 30000)) this._initTimetable();
  }

  connectedCallback() {
    this._tick = setInterval(() => {
      if (!this._hass) return;
      if (this._clock) this._clock.textContent = `${fmtDate(new Date())} ${fmtTime(new Date(), true)}`;
      if (this._config.mode === "departures" && new Date().getSeconds() % 15 === 0) this._renderDepartures(true);
    }, 1000);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  getCardSize() {
    return this._config?.mode === "departures" ? 2 + (this._config.max || 5) : 8;
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((e) => e.startsWith("sensor.") && Array.isArray(hass.states[e].attributes.departures));
    return entity ? { mode: "departures", entity } : { mode: "timetable" };
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>
      <div class="head"><span class="title"></span><span class="clock"></span></div>
      <div class="body"></div></ha-card>`;
    this._title = this.shadowRoot.querySelector(".title");
    this._clock = this.shadowRoot.querySelector(".clock");
    this._body = this.shadowRoot.querySelector(".body");
    this._clock.textContent = `${fmtDate(new Date())} ${fmtTime(new Date(), true)}`;
    this._built = true;
  }

  // ---------------------------------------------------------------- departures

  _renderDepartures(force = false) {
    const t = this._t;
    const st = this._hass.states[this._config.entity];
    if (!st) {
      this._title.textContent = this._config.title || "Tüke Busz";
      this._body.innerHTML = `<div class="err">${t.missingEntity}: ${esc(this._config.entity)}</div>`;
      return;
    }
    if (!force && st === this._lastState) return;
    this._lastState = st;
    const a = st.attributes;
    this._title.textContent = this._config.title || (a.destination ? `${a.origin} → ${a.destination}` : a.origin || st.entity_id);

    const now = Date.now();
    const deps = (a.departures || [])
      .filter((d) => new Date(d.departure).getTime() > now - 60000)
      .slice(0, this._config.max || 99);
    if (!deps.length) {
      this._body.innerHTML = `<div class="muted">${t.none}</div>`;
      return;
    }
    const showArrival = this._config.show_arrival !== false;
    this._body.innerHTML = deps.map((d) => {
      const dep = new Date(d.departure);
      const minutes = Math.max(0, Math.floor((dep.getTime() - now) / 60000));
      const date = fmtDate(dep);
      const arr = d.arrival ? new Date(d.arrival) : null;
      const arrTxt = arr ? `${fmtDate(arr) === date ? "" : fmtDate(arr) + " "}${fmtTime(arr)}` : "";
      const platform = d.origin_platform ? ` · ${esc(d.origin_platform)}` : "";
      return `<div class="row">
        <span class="badge" style="background:${esc(d.color)};color:${esc(d.text_color)}">${esc(d.line)}</span>
        <div class="main">
          <div class="when">${date} ${fmtTime(dep)}${
            showArrival && arr ? ` <span class="sub">→ ${arrTxt} ${t.arrive}</span>` : ""}</div>
          <div class="sub">${esc(d.headsign)}${platform}${d.duration_min != null ? ` · ${d.duration_min} ${t.minUnit}` : ""}</div>
        </div>
        <span class="rel${minutes <= 3 ? " soon" : ""}">${relative(t, minutes)}</span>
      </div>`;
    }).join("");
  }

  // ---------------------------------------------------------------- timetable

  async _initTimetable() {
    const t = this._t;
    this._title.textContent = this._config.title || `Tüke Busz – ${t.timetable}`;
    if (!this._controlsBuilt) {
      this._body.innerHTML = `<div class="muted">${t.loading}</div>`;
      this._controlsBuilt = true;
    }
    if (this._metaLoading) return;
    this._metaLoading = true;
    try {
      this._meta = await getMetadata(this._hass);
      this._metaLoaded = true;
      this._buildControls();
      if (this._state.view === "stop" ? this._state.stop : this._state.line) this._query();
    } catch (e) {
      this._metaFailedAt = Date.now();
      this._body.innerHTML = `<div class="err">${esc(e.message || e)}</div>`;
    } finally {
      this._metaLoading = false;
    }
  }

  get _state() {
    if (!this.__state) {
      const c = this._config;
      this.__state = {
        view: c.view || (c.line && !c.stop ? "line" : "stop"),
        stop: c.stop || "", line: c.line != null ? String(c.line) : "",
        date: fmtDate(new Date()), start: c.start || "", end: c.end || "",
        allDay: !c.start && !c.end, direction: c.direction || "",
      };
    }
    return this.__state;
  }

  _buildControls() {
    const t = this._t;
    const s = this._state;
    const stopOpts = this._meta.stops.flatMap((g) => [
      `<option value="${esc(g.name)}"></option>`,
      ...(g.platforms.length > 1 ? g.platforms.map((p) => `<option value="${esc(p.stop_id)}">${esc(g.name)} – ${esc(p.platform)}</option>`) : []),
    ]).join("");
    const lineOpts = this._meta.routes.map((r) =>
      `<option value="${esc(r.line)}" ${r.line === s.line ? "selected" : ""}>${esc(r.line)} – ${esc(r.name)}</option>`).join("");

    this._body.innerHTML = `
      <div class="controls">
        <div class="seg"><button data-view="stop">${t.stop}</button><button data-view="line">${t.line}</button></div>
        <label class="f-stop">${t.stop}<input list="tb-stops" class="stop" placeholder="${t.pickStop}" value="${esc(s.stop)}"></label>
        <datalist id="tb-stops">${stopOpts}</datalist>
        <label class="f-line">${t.line}<select class="line"><option value="">${t.pickLine}</option>${lineOpts}</select></label>
        <label class="f-dir">${t.direction}<select class="dir"></select></label>
        <label>${t.date}<input type="date" class="date" value="${s.date}"></label>
        <label class="chk"><input type="checkbox" class="allday" ${s.allDay ? "checked" : ""}>${t.allDay}</label>
        <label class="f-time">${t.from}<input type="time" class="start" value="${esc(s.start)}"></label>
        <label class="f-time">${t.to}<input type="time" class="end" value="${esc(s.end)}"></label>
        <button class="go">${t.query}</button>
      </div>
      <div class="result"></div>`;
    const $ = (sel) => this._body.querySelector(sel);
    this._result = $(".result");

    const sync = () => {
      this._body.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.view === s.view));
      $(".f-stop").style.display = s.view === "stop" ? "" : "none";
      $(".f-line").style.display = $(".f-dir").style.display = s.view === "line" ? "" : "none";
      this._body.querySelectorAll(".f-time").forEach((el) => (el.style.display = s.allDay ? "none" : ""));
      const route = this._meta.routes.find((r) => r.line === s.line);
      $(".dir").innerHTML = `<option value="">${t.allDirections}</option>` + (route?.directions || [])
        .map((d) => `<option value="${esc(d)}" ${d === s.direction ? "selected" : ""}>${esc(d)}</option>`).join("");
    };
    this._body.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
      s.view = b.dataset.view; sync(); this._query();
    }));
    $(".stop").addEventListener("change", (e) => { s.stop = e.target.value.trim(); this._query(); });
    $(".line").addEventListener("change", (e) => { s.line = e.target.value; s.direction = ""; sync(); this._query(); });
    $(".dir").addEventListener("change", (e) => { s.direction = e.target.value; this._query(); });
    $(".date").addEventListener("change", (e) => { s.date = e.target.value; this._query(); });
    $(".allday").addEventListener("change", (e) => { s.allDay = e.target.checked; sync(); this._query(); });
    $(".start").addEventListener("change", (e) => { s.start = e.target.value; this._query(); });
    $(".end").addEventListener("change", (e) => { s.end = e.target.value; this._query(); });
    $(".go").addEventListener("click", () => {
      s.stop = $(".stop").value.trim();
      this._query();
    });
    sync();
  }

  async _query() {
    const t = this._t;
    const s = this._state;
    if (!this._result) return;
    if (s.view === "stop" && !s.stop) { this._result.innerHTML = `<div class="muted">${t.pickStop}</div>`; return; }
    if (s.view === "line" && !s.line) { this._result.innerHTML = `<div class="muted">${t.pickLine}</div>`; return; }
    const data = { mode: s.view, date: s.date };
    if (s.view === "stop") data.stop = s.stop; else data.line = s.line;
    if (s.view === "line" && s.direction) data.direction = s.direction;
    if (!s.allDay) {
      if (s.start) data.start = s.start;
      if (s.end) data.end = s.end;
    }
    const seq = (this._seq = (this._seq || 0) + 1);
    this._result.innerHTML = `<div class="muted">${t.loading}</div>`;
    try {
      const res = await callService(this._hass, "get_timetable", data);
      if (seq !== this._seq) return;
      this._result.innerHTML = res.mode === "line" ? this._renderLine(res) : this._renderStop(res);
    } catch (e) {
      if (seq === this._seq) this._result.innerHTML = `<div class="err">${esc(e.message || e)}</div>`;
    }
  }

  _renderStop(res) {
    const t = this._t;
    if (!res.departures.length) return `<div class="muted">${t.noData}</div>`;
    const multi = new Set(res.departures.map((d) => d.stop_id)).size > 1;
    return `<h3>${esc(res.stop)} · ${esc(res.date)} · ${res.departures.length} ${t.departures}</h3>
      <div class="scroll"><table>
        <tr><th>${t.time}</th><th>${t.line}</th><th>${t.headsign}</th>${multi ? `<th>${t.platform}</th>` : ""}</tr>
        ${res.departures.map((d) => `<tr>
          <td>${esc(d.time)}</td>
          <td><span class="badge" style="background:${esc(d.color)};color:${esc(d.text_color)}">${esc(d.line)}</span></td>
          <td>${esc(d.headsign)}</td>${multi ? `<td>${esc(d.platform)}</td>` : ""}</tr>`).join("")}
      </table></div>`;
  }

  _renderLine(res) {
    const t = this._t;
    const dirs = res.directions.filter((d) => d.trips.length);
    if (!dirs.length) return `<div class="muted">${t.noData}</div>`;
    const badge = `<span class="badge" style="background:${esc(res.color)};color:${esc(res.text_color)}">${esc(res.line)}</span>`;
    return dirs.map((d) => `
      <h3>${badge} → ${esc(d.terminus)} · ${esc(res.date)}</h3>
      <div class="scroll"><table>
        ${d.stops.map((stop, i) => `<tr>
          <td class="stopname" title="${esc(stop.platform)}">${esc(stop.name)}</td>
          ${d.trips.map((trip) => trip.times[i] ? `<td>${esc(trip.times[i])}</td>` : `<td class="empty">–</td>`).join("")}
        </tr>`).join("")}
      </table></div>`).join("");
  }
}

if (!customElements.get("tukebusz-card")) {
  customElements.define("tukebusz-card", TukeBuszCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
  type: "tukebusz-card",
  name: "Tüke Busz",
  description: "Next departures (A→B) and interactive timetable for Pécs buses",
    preview: false,
  });
}
console.info(`%c TUKEBUSZ-CARD %c ${VERSION} `, "background:#2FB457;color:#fff", "");
