/**
 * SEM Energy Plan Card v2 – ritar DayPlan från SEM plan-mode.
 *
 *   type: custom:sem-energy-plan-card
 *   day_plan_sensor: sensor.smart_energy_manager_<dag_plan_anledning_entity>
 *   # Valfria overrides:
 *   battery_cap_kwh: 33
 *   battery_min_pct: 10
 *   export_percentile: 80
 *   charge_percentile: 25
 *   grid_charge_kw: 4
 *   night_house_load_w: 700
 */

// Nordpool-priset ritas som blå stapel (se PRICE_COLOR nedan) ovanpå dessa
// kolumn-tonarna - live-feedback 2026-09-23: cover_load låg i lavendel
// (#7f77dd), för nära blått i nyans för att gå att skilja från prisstapeln
// på liten skärm, särskilt i mörkt tema där båda bara syns som låg-alpha
// överlägg. Bytt till rosa/magenta - maximalt avstånd i nyans från blått,
// samtidigt som orange/gult/grönt behåller sitt inbördes avstånd.
const ACTION_COLOR = {
  export:       { bg: "rgba(240,120,20,{a})",   border: "#f07814",  label: "Export"        },
  grid_charge:  { bg: "rgba(27,175,122,{a})",   border: "#1baf7a",  label: "Nätladdning"   },
  solar_charge: { bg: "rgba(255,200,0,{a})",    border: "#eda100",  label: "Solladdning"   },
  cover_load:   { bg: "rgba(224,60,120,{a})",   border: "#e03c78",  label: "Egenförbr."   },
  idle:         { bg: null,                      border: null,       label: "Idle"          },
};
// Nordpool-prisstapeln (låg tidigare på 0.15-0.18 alpha rgba(42,120,214,…),
// vilket smälte ihop med kolumn-tonarna bakom - särskilt cover_load, som
// hade lika låg alpha på nästan samma yta). Egen, klart mörkare/mättad blå
// med betydligt högre alpha - staplarna ska läsas som en tydlig, egen serie,
// inte som ett svagt filter över åtgärdsfärgen.
const PRICE_COLOR = { dark: "rgba(90,160,255,0.55)", light: "rgba(20,90,200,0.42)" };

function actionBg(action, alpha) {
  const c = ACTION_COLOR[action];
  if (!c || !c.bg) return null;
  return c.bg.replace("{a}", alpha);
}

class SemEnergyPlanCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._lastUpdate = 0;
    this._rafId = null;
    this._roConnected = false;
  }

  setConfig(config) {
    this._config = {
      nordpool:             "sensor.nordpool_kwh_se3_sek_3_10_0_2",
      buy_price_sensor:     "sensor.smart_energy_manager_nordpool_prisschema",
      battery_soc:          "sensor.sonnenbatterie_271100_state_battery_percentage_real",
      battery_wh:           "sensor.sonnenbatterie_271100_battery_remaining_capacity_usable",
      solar_w:              "sensor.sonnenbatterie_271100_state_production",
      house_w:              "sensor.sonnenbatterie_271100_state_consumption_current",
      solcast_tomorrow_kwh: "sensor.solcast_pv_forecast_forecast_tomorrow",
      solcast_peak_w:       "sensor.solcast_pv_forecast_peak_forecast_tomorrow",
      solcast_peak_time:    "sensor.solcast_pv_forecast_peak_time_tomorrow",
      day_plan_sensor:      "",
      operating_mode:       "select.smart_energy_manager_operating_mode",
      solcast_peak_w_today:       "sensor.solcast_pv_forecast_peak_forecast_today",
      solcast_peak_time_today:    "sensor.solcast_pv_forecast_peak_time_today",
      battery_cap_kwh:      33,
      battery_usable_pct:   93,
      battery_min_pct:      10,
      export_percentile:    80,
      charge_percentile:    25,
      grid_charge_kw:       4.0,
      night_house_load_w:   700,
      ...config,
    };
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    const now = Date.now();
    if (now - this._lastUpdate < 30000) return;
    this._lastUpdate = now;
    if (this._rafId) cancelAnimationFrame(this._rafId);
    this._rafId = requestAnimationFrame(() => this._update());
  }

  getCardSize() { return 9; }

  _build() {
    this.shadowRoot.innerHTML = `
<style>
:host { display: block; }
ha-card { padding: 16px 16px 12px; }
h3 { margin: 0 0 4px; font-size: 13px; font-weight: 500; color: var(--primary-text-color); }
.plan-badge { display:inline-block; font-size:10px; font-weight:600; padding:2px 7px; border-radius:10px;
              background:rgba(27,175,122,0.18); color:#1baf7a; margin-left:6px; vertical-align:middle; }
.plan-time { font-size:10px; color:var(--secondary-text-color); margin-bottom:10px; }
.stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin-bottom: 12px; }
.st { background: var(--secondary-background-color); border-radius: 8px; padding: 8px 10px; }
.stl { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 3px; }
.stv { font-size: 15px; font-weight: 500; color: var(--primary-text-color); }
canvas { display: block; width: 100%; }
.leg { display: flex; flex-wrap: wrap; gap: 8px 12px; font-size: 11px; color: var(--secondary-text-color); margin: 6px 0 10px; }
.leg span { display: flex; align-items: center; gap: 4px; }
.lsq { width: 10px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.phases { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; }
.ph { border-left: 3px solid; border-radius: 0 6px 6px 0; padding: 7px 9px;
      background: var(--secondary-background-color); }
.pht { font-size: 11px; font-weight: 500; margin-bottom: 2px; }
.phd { font-size: 11px; color: var(--secondary-text-color); line-height: 1.35; }
</style>
<ha-card>
  <h3>Energiplan<span class="plan-badge" id="plan-badge" style="display:none">PLAN-MODE</span></h3>
  <div class="plan-time" id="plan-time" style="display:none"></div>
  <div class="stats">
    <div class="st"><div class="stl">Batteri nu</div><div class="stv" id="v-soc">–</div></div>
    <div class="st"><div class="stl">Exporterbart</div><div class="stv" id="v-exp">–</div></div>
    <div class="st"><div class="stl">Kvällsmål</div><div class="stv" id="v-tgt">–</div></div>
    <div class="st"><div class="stl">Sol imorgon</div><div class="stv" id="v-sol">–</div></div>
  </div>
  <canvas id="c" height="270"></canvas>
  <div class="leg" id="legend">
    <span><span class="lsq" style="background:rgba(20,90,200,0.6)"></span>Spotpris</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2.5px solid var(--primary-text-color)"></span>&nbsp;Köppris (inkl. avgifter+moms)</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2px dashed #a855f7"></span>&nbsp;Sparvärde V</span>
    <span><span class="lsq" style="background:rgba(240,120,20,0.5)"></span>Export</span>
    <span><span class="lsq" style="background:rgba(27,175,122,0.5)"></span>Nätladdning</span>
    <span><span class="lsq" style="background:rgba(255,200,0,0.5)"></span>Solladdning</span>
    <span><span class="lsq" style="background:rgba(224,60,120,0.5)"></span>Egenförbrukning</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2.5px solid #1baf7a"></span>&nbsp;Batteri (plan)</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2px dashed #eda100"></span>&nbsp;Sol kW</span>
  </div>
  <div class="phases">
    <div class="ph" style="border-color:#f07814">
      <div class="pht" style="color:#f07814" id="ph1t">Export</div>
      <div class="phd" id="ph1d">–</div>
    </div>
    <div class="ph" style="border-color:#1baf7a">
      <div class="pht" style="color:#1baf7a" id="ph2t">Natladdning/Vila</div>
      <div class="phd" id="ph2d">–</div>
    </div>
    <div class="ph" style="border-color:#2a78d6">
      <div class="pht" style="color:#2a78d6" id="ph3t">Sol-takeover</div>
      <div class="phd" id="ph3d">–</div>
    </div>
  </div>
</ha-card>`;

    requestAnimationFrame(() => {
      const haCard = this.shadowRoot.querySelector("ha-card");
      if (!haCard || this._roConnected) return;
      const ro = new ResizeObserver(() => {
        const canvas = this._el("c");
        if (!canvas || canvas.offsetWidth === 0) return;
        ro.disconnect();
        if (this._hass) {
          this._lastUpdate = 0;
          if (this._rafId) cancelAnimationFrame(this._rafId);
          this._rafId = requestAnimationFrame(() => this._update());
        }
      });
      ro.observe(haCard);
      this._roConnected = true;
    });
  }

  _el(id) { return this.shadowRoot.getElementById(id); }

  // Live-feedback 2026-09-23: skalsiffrorna på höger/vänster axel var
  // osynliga i ljust tema. Orsak: canvas-ritningen avgjorde ljust/mörkt via
  // OS-inställningen (prefers-color-scheme), inte HA:s eget tema - byter
  // användaren HA-tema utan att matcha OS-läget (t.ex. ljust HA-tema på en
  // enhet med mörkt OS-läge) ritades texten i fel färg mot bakgrunden.
  // hass.themes.darkMode är HA:s egen, redan beräknade sanning för vilket
  // tema som faktiskt visas - föredra den, OS-inställningen bara som
  // reservläge om hass av någon anledning saknar den.
  _isDark() {
    const hassDark = this._hass?.themes?.darkMode;
    if (typeof hassDark === "boolean") return hassDark;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  _getFloat(eid, def = 0) {
    const s = this._hass?.states[eid];
    return s ? (parseFloat(s.state) || def) : def;
  }

  _slotVal(slot) {
    if (slot == null) return 0;
    if (typeof slot === "object") return slot.value ?? slot.price ?? 0;
    return slot || 0;
  }

  _fmt(dt) {
    const d = dt instanceof Date ? dt : new Date(dt);
    return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
  }

  // ── Main update ───────────────────────────────────────────────────────

  _update() {
    const cfg = this._config;
    const nordpool = this._hass?.states[cfg.nordpool];
    if (!nordpool?.attributes) return;

    const rawToday    = nordpool.attributes.raw_today    || [];
    const rawTomorrow = nordpool.attributes.raw_tomorrow || [];

    const battPct   = this._getFloat(cfg.battery_soc);
    const battWh    = this._getFloat(cfg.battery_wh);
    const solarW    = this._getFloat(cfg.solar_w);
    const houseW    = this._getFloat(cfg.house_w) || 900;
    const solTomKwh = this._getFloat(cfg.solcast_tomorrow_kwh);
    const peakW     = this._getFloat(cfg.solcast_peak_w);
    const peakStr   = this._hass?.states[cfg.solcast_peak_time]?.state;
    const opMode    = this._hass?.states[cfg.operating_mode]?.state ?? "auto";

    const capKwh = cfg.battery_cap_kwh;
    const minKwh = capKwh * (cfg.battery_min_pct / 100);
    const battKwh = battWh > 100
      ? battWh / 1000
      : (battPct / 100) * capKwh * (cfg.battery_usable_pct / 100);

    const now = new Date();
    const nowMs = now.getTime();

    // ── SEM plan data ─────────────────────────────────────────────────
    const planState = cfg.day_plan_sensor ? this._hass?.states[cfg.day_plan_sensor] : null;
    const planAttr  = planState?.attributes ?? {};
    const rawPlanSlots = Array.isArray(planAttr.slots) && planAttr.slots.length > 0
      ? planAttr.slots : null;
    const hasPlan = rawPlanSlots !== null;

    // Parse plan slots with Date objects
    const planSlots = hasPlan ? rawPlanSlots.map(s => ({
      ...s,
      startMs: new Date(s.start).getTime(),
      endMs:   new Date(s.end).getTime(),
    })) : null;

    const planEndMs = hasPlan ? Math.max(...planSlots.map(s => s.endMs)) : nowMs + 15 * 3600000;

    // Aktuell planslot (för "nu"-etikett i canvas och plan-texten)
    const _nowSlot = hasPlan ? planSlots.find(ps => nowMs >= ps.startMs && nowMs < ps.endMs) : null;
    const _currentPlanAction = _nowSlot?.action ?? null;

    const chartEndMs = hasPlan ? planEndMs : (() => {
      const e = new Date(now); e.setDate(e.getDate() + 1); e.setHours(9, 0, 0, 0); return e.getTime();
    })();

    // ── Timeline ─────────────────────────────────────────────────────
    const timeline = this._buildTimeline(now, rawToday, rawTomorrow, chartEndMs);

    // Verkligt köppris (spot + nätavgift + skatt + moms) per kvart ur SEM:s
    // egen prissensor - Nordpool-sensorn har bara råspot i öre.
    const buyAttr = this._hass?.states[cfg.buy_price_sensor]?.attributes ?? {};
    const buyByMs = new Map();
    [...(buyAttr.prices_today ?? []), ...(buyAttr.prices_tomorrow ?? [])]
      .forEach(p => buyByMs.set(new Date(p.start).getTime(), p.buy));
    timeline.forEach(s => { s.buy = buyByMs.get(s.time.getTime()) ?? null; });

    // ── Solar profile – Gaussisk Solcast-modell för båda dagarna ─────
    const _parseDate = (s) => {
      if (!s) return null;
      let d = new Date(s);
      if (!isNaN(d.getTime())) return d;
      // Svenskt HA-format: "29 augusti 2026 kl. 11:00"
      const SV = {januari:0,februari:1,mars:2,april:3,maj:4,juni:5,juli:6,augusti:7,september:8,oktober:9,november:10,december:11};
      const m = s.match(/(\d{1,2})\s+(\w+)\s+(\d{4})\s+kl\.\s+(\d{1,2}):(\d{2})/);
      if (m) { const mo = SV[m[2].toLowerCase()]; if (mo !== undefined) { d = new Date(+m[3], mo, +m[1], +m[4], +m[5]); if (!isNaN(d.getTime())) return d; } }
      return null;
    };
    const peakTimeTomorrow = _parseDate(peakStr);
    const peakWToday   = this._getFloat(cfg.solcast_peak_w_today);
    const peakStrToday = this._hass?.states[cfg.solcast_peak_time_today]?.state;
    const peakTimeToday = _parseDate(peakStrToday);
    const SIGMA = 2.0;
    const gaussSolar = (t, peakTime, peakKw) => {
      if (!peakTime || peakKw <= 0) return 0;
      const h  = t.getHours() + t.getMinutes() / 60;
      const ph = peakTime.getHours() + peakTime.getMinutes() / 60;
      return Math.max(0, peakKw * Math.exp(-0.5 * ((h - ph) / SIGMA) ** 2));
    };
    // Fallback-topptid för idag: imorgondagens tid eller soltoppmitt 12:30
    const _peakTimeToday = peakTimeToday
      ?? peakTimeTomorrow
      ?? { getHours: () => 12, getMinutes: () => 30 };
    const getSolarKw = (t) => {
      const isToday = t.toDateString() === now.toDateString();
      if (isToday) {
        if (peakWToday > 0)
          return gaussSolar(t, _peakTimeToday, peakWToday / 1000);
        // Sista fallback: exponentiellt avklingande från aktuell produktion
        const h  = t.getHours() + t.getMinutes() / 60;
        if (h < 4 || h > 21.5) return 0;
        const dtH = (t.getTime() - nowMs) / 3600000;
        return Math.max(0, (solarW / 1000) * Math.exp(-dtH * 0.7));
      }
      return gaussSolar(t, peakTimeTomorrow, peakW / 1000);
    };

    // ── Mark plan actions on timeline ─────────────────────────────────
    timeline.forEach(s => {
      s.planAction = null;
      if (!hasPlan) return;
      for (const ps of planSlots) {
        if (s.time.getTime() >= ps.startMs && s.time.getTime() < ps.endMs) {
          s.planAction = ps.action;
          break;
        }
      }
    });

    // ── Export / cheap zones (fallback when no plan) ───────────────────
    if (!hasPlan) {
      const todayVals = rawToday.map(s => this._slotVal(s)).filter(v => v > 0).sort((a, b) => a - b);
      const expIdx = Math.min(Math.floor((cfg.export_percentile / 100) * todayVals.length), todayVals.length - 1);
      const expThreshold = todayVals[expIdx] || 100;
      const windowEndMs = nowMs + 20 * 3600000;
      timeline.forEach(s => {
        s.high = s.price >= expThreshold
          && s.time.getTime() >= nowMs - 1800000
          && s.time.getTime() < windowEndMs
          && getSolarKw(s.time) < 2.0;
      });
      const winterMode = opMode === "winter" || opMode === "force_charge_battery";
      if (winterMode) {
        const overnightPrices = timeline.filter(s => { const h = s.time.getHours(); return h >= 21 || h < 7; })
          .map(s => s.price).filter(v => v > 0).sort((a, b) => a - b);
        const chgIdx = Math.min(Math.floor((cfg.charge_percentile / 100) * overnightPrices.length), overnightPrices.length - 1);
        const chgThreshold = overnightPrices[chgIdx] ?? 10;
        timeline.forEach(s => {
          const h = s.time.getHours();
          s.cheap = (h >= 21 || h < 7) && s.price > 0 && s.price <= chgThreshold && s.time.getTime() > nowMs;
        });
      }
    }

    // ── Plan battery SOC curve ────────────────────────────────────────
    // _socOffset: förskjut plan-kurvan till faktisk batterinivå vid "nu"
    const _planNowSlot = hasPlan ? planSlots.find(ps => nowMs >= ps.startMs && nowMs < ps.endMs) : null;
    const _planKwhNow  = _planNowSlot ? (_planNowSlot.battery_soc_est_pct / 100) * capKwh : null;
    const _socOffset   = _planKwhNow !== null ? battKwh - _planKwhNow : 0;
    const _adjKwh = (soc_pct) => Math.min(capKwh, Math.max(minKwh, (soc_pct / 100) * capKwh + _socOffset));

    let planBattKwhs = null;
    if (hasPlan) {
      planBattKwhs = timeline.map(slot => {
        const t = slot.time.getTime();
        for (const ps of planSlots) {
          if (t >= ps.startMs && t < ps.endMs)
            return _adjKwh(ps.battery_soc_est_pct);
        }
        return null;
      });
    }

    // ── Solar takeover ────────────────────────────────────────────────
    const planTakeover = hasPlan && planAttr.solar_takeover ? new Date(planAttr.solar_takeover) : null;
    if (planTakeover) {
      timeline.forEach(s => {
        s.solarTakeover = Math.abs(s.time.getTime() - planTakeover.getTime()) < 30 * 60000
          && !s.isToday;
      });
    } else {
      const nightKwFb = cfg.night_house_load_w / 1000;
      const houseKwFb = houseW / 1000;
      const takeoverThresh = Math.max(nightKwFb, houseKwFb * 0.7);
      let takeoverH = 8.0;
      if (peakTimeTomorrow && peakW / 1000 > takeoverThresh) {
        const ph = peakTimeTomorrow.getHours() + peakTimeTomorrow.getMinutes() / 60;
        const inner = Math.log(peakW / 1000 / takeoverThresh);
        if (inner > 0) takeoverH = Math.max(5.0, ph - SIGMA * Math.sqrt(2 * inner));
      }
      let tkFound = false;
      timeline.forEach(s => {
        if (!tkFound && !s.isToday && getSolarKw(s.time) > takeoverThresh) {
          s.solarTakeover = true; tkFound = true;
        }
      });
    }

    // ── Simulation (fallback or for comparison) ───────────────────────
    const nightKw = cfg.night_house_load_w / 1000;
    const houseKw = houseW / 1000;
    const exportableKwh = hasPlan
      ? (planAttr.total_exportable_kwh ?? 0)
      : (() => {
          const firstSlot = timeline.find(s => s.isToday)?.time ?? now;
          const ws = firstSlot < now ? now : firstSlot;
          const tkSlot = timeline.find(s => s.solarTakeover);
          const tkMs = tkSlot ? tkSlot.time.getTime() : nowMs + 9 * 3600000;
          const hd = Math.max(1, (tkMs - ws.getTime()) / 3600000);
          const floor = hd * nightKw + 2.0;
          return Math.max(0, battKwh - minKwh - floor);
        })();

    // Solprofil beräknas alltid – används för kurvan oavsett plan/sim-läge
    const solarKwsData = timeline.map(s => getSolarKw(s.time));

    const sim = hasPlan ? null : this._simulate(
      timeline, battKwh, minKwh, exportableKwh, capKwh,
      getSolarKw, houseKw, nightKw, cfg.grid_charge_kw, nowMs
    );

    // ── Stat tiles ────────────────────────────────────────────────────
    // planAttr.total_exportable_kwh är en ÖGONBLICKSBILD (batt_kwh vid
    // planeringstillfället minus reserv) - inte vad planen faktiskt tänker
    // exportera senare i dygnet. Live-incident 2026-09-22: batteriet stod
    // på 16% (nyss urladdat) när planen genererades kl 11:15, så talet blev
    // 1,4 kWh, trots att planen redan då la ~6 kWh export kl 17:45-19:00 -
    // efter att solladdningen hunnit fylla batteriet till 99%. Summera
    // istället vad export-slotsen FAKTISKT innehåller.
    const plannedExportKwh = hasPlan
      ? planSlots.filter(s => s.action === "export").reduce(
          (sum, s) => sum + Math.abs(s.target_power_w) / 1000 * (s.endMs - s.startMs) / 3600000, 0)
      : exportableKwh;
    const eveningTgt = hasPlan
      ? (planAttr.evening_target_soc_pct ?? 0)
      : (minKwh / capKwh * 100 + 10);
    this._el("v-soc").textContent  = `${battPct.toFixed(0)}% · ${battKwh.toFixed(1)} kWh`;
    this._el("v-exp").textContent  = `${plannedExportKwh.toFixed(1)} kWh`;
    this._el("v-tgt").textContent  = `${eveningTgt.toFixed(0)}% SOC`;
    this._el("v-sol").textContent  = `${solTomKwh.toFixed(1)} kWh`;

    // Plan mode badge
    const badge = this._el("plan-badge");
    const planTime = this._el("plan-time");
    if (hasPlan && badge) {
      badge.style.display = "inline-block";
      if (planTime && planAttr.plan_generated_at) {
        const genAt = new Date(planAttr.plan_generated_at);
        planTime.style.display = "block";
        const _acLabel = _currentPlanAction
          ? (ACTION_COLOR[_currentPlanAction]?.label ?? _currentPlanAction)
          : null;
        planTime.textContent = `Plan skapad ${this._fmt(genAt)} · Nu: ${_acLabel ?? "–"} · slots: ${planSlots.length}`;
      }
    }

    // ── Phase cards ───────────────────────────────────────────────────
    if (hasPlan) {
      // Export phase – kronologisk sortering, offset-justerade batterivärden
      const expSlots = planSlots.filter(s => s.action === "export")
        .sort((a, b) => a.startMs - b.startMs);
      if (expSlots.length) {
        const expPow    = Math.abs(expSlots[0].target_power_w);
        const _expStart = this._fmt(expSlots[0].start);
        const _expEnd   = this._fmt(expSlots[expSlots.length - 1].end);
        const expRange  = _expStart === _expEnd ? _expStart : `${_expStart}–${_expEnd}`;
        this._el("ph1t").textContent = `${expRange} export`;
        const battStart = _adjKwh(expSlots[0].battery_soc_est_pct);
        const battEnd   = _adjKwh(expSlots[expSlots.length-1].battery_soc_est_pct);
        this._el("ph1d").textContent =
          `${plannedExportKwh.toFixed(1)} kWh · ~${expPow.toFixed(0)} W · ${battStart.toFixed(1)}→${battEnd.toFixed(1)} kWh`;
      } else {
        this._el("ph1t").textContent = "Ingen export";
        this._el("ph1d").textContent = `Golv ${(planAttr.export_floor_kwh??0).toFixed(1)} kWh`;
      }

      // Grid charge / night phase
      const gcSlots = planSlots.filter(s => s.action === "grid_charge");
      if (gcSlots.length) {
        const gcRange = `${this._fmt(gcSlots[0].start)}–${this._fmt(gcSlots[gcSlots.length-1].end)}`;
        const gcPow = gcSlots[0].target_power_w.toFixed(0);
        this._el("ph2t").textContent = "Nätladdning (plan)";
        this._el("ph2t").style.color = "#1baf7a";
        this._el("ph2d").textContent = `${gcRange} · ${gcPow} W · batteri → ${(planAttr.evening_target_soc_pct??0).toFixed(0)}%`;
      } else {
        this._el("ph2t").textContent = "Nattvila";
        this._el("ph2t").style.color = "var(--secondary-text-color)";
        const idleSlots = planSlots.filter(s => s.action === "idle" || s.action === "cover_load");
        const clSlots = planSlots.filter(s => s.action === "cover_load");
        this._el("ph2d").textContent = `${idleSlots.length} slots · ${clSlots.length} egenförb.`;
      }

      // Solar takeover phase
      const tkSlot = timeline.find(s => s.solarTakeover);
      this._el("ph3t").textContent = `Sol-takeover ~${tkSlot ? this._fmt(tkSlot.time) : "–"}`;
      const scSlots = planSlots.filter(s => s.action === "solar_charge");
      const endBatt = planSlots.length ? _adjKwh(planSlots[planSlots.length-1].battery_soc_est_pct) : 0;
      this._el("ph3d").textContent = scSlots.length
        ? `${scSlots.length} sol-slots · planslut ~${endBatt.toFixed(1)} kWh`
        : `${peakW > 0 ? (peakW/1000).toFixed(1) + " kW peak" : "Sol > last"}`;
    } else {
      // Fallback simulation phase cards
      if (exportableKwh < 0.1) {
        this._el("ph1t").textContent = "Ingen export";
        this._el("ph1d").textContent = `Batteri ${battKwh.toFixed(1)} kWh`;
      } else {
        const hs = timeline.filter(s => s.high);
        this._el("ph1t").textContent = hs.length ? `${hs[0].label}–${hs[hs.length-1].label} export` : "Export";
        this._el("ph1d").textContent = `${exportableKwh.toFixed(1)} kWh → ${sim.battAfterExport.toFixed(1)} kWh`;
      }
      this._el("ph2t").textContent = "Nattvila";
      this._el("ph2t").style.color = "var(--secondary-text-color)";
      this._el("ph2d").textContent = `${sim.battAfterExport.toFixed(1)} → ${sim.battAtTakeover.toFixed(1)} kWh`;
      const tkSlot = timeline.find(s => s.solarTakeover);
      this._el("ph3t").textContent = `Sol-takeover ~${tkSlot ? tkSlot.label : "–"}`;
      this._el("ph3d").textContent = peakW > 0 ? `${(peakW/1000).toFixed(1)} kW peak imorgon` : "Sol > hushållslast";
    }

    this._drawCanvas(timeline, sim, solarKwsData, planBattKwhs, planSlots, hasPlan ? plannedExportKwh : exportableKwh, capKwh, minKwh, nowMs, _currentPlanAction,
      hasPlan ? (planAttr.marginal_value_sek_kwh ?? null) : null);
  }

  // ── Timeline ─────────────────────────────────────────────────────────

  _buildTimeline(now, rawToday, rawTomorrow, chartEndMs) {
    const slots = [];
    const cur = new Date(now);
    cur.setMinutes(Math.floor(cur.getMinutes() / 15) * 15, 0, 0);
    const end = new Date(chartEndMs + 15 * 60000);

    while (cur <= end) {
      const isToday = cur.toDateString() === now.toDateString();
      const raw = isToday ? rawToday : rawTomorrow;
      const h = cur.getHours(), m = cur.getMinutes();
      const i = h * 4 + Math.floor(m / 15);
      slots.push({
        time:          new Date(cur),
        label:         `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`,
        price:         this._slotVal(raw[i]),
        isToday,
        isMidnight:    !isToday && h === 0 && m === 0,
        high:          false,
        cheap:         false,
        planAction:    null,
        solarTakeover: false,
      });
      cur.setTime(cur.getTime() + 15 * 60000);
    }
    return slots;
  }

  // ── Simulation (fallback) ─────────────────────────────────────────────

  _simulate(timeline, battKwh, minKwh, exportableKwh, capKwh,
            getSolarKw, houseKw, nightKw, gridChargeKw, nowMs) {
    const highSlots    = timeline.filter(s => s.high);
    const priceSum     = highSlots.reduce((a, s) => a + s.price, 0);
    const maxKwh       = capKwh * 0.99;
    const lastHighIdx  = timeline.reduce((acc, s, i) => s.high ? i : acc, -1);

    let batt = battKwh, exported = 0, gridChargedKwh = 0;
    let battAfterExport = null, battAtTakeover = battKwh, takeoverFound = false;
    const battLevels = [], solarKws = [], gridKws = [];

    for (let si = 0; si < timeline.length; si++) {
      const slot = timeline[si];
      battLevels.push(parseFloat(Math.max(minKwh, batt).toFixed(2)));
      if (battAfterExport === null && lastHighIdx >= 0 && si === lastHighIdx + 1) {
        battAfterExport = Math.max(minKwh, batt);
      }
      const solar = getSolarKw(slot.time);
      solarKws.push(solar);
      const h = slot.time.getHours();
      const effLoad = (h >= 22 || h < 6) ? nightKw : houseKw;
      const surplus = solar - effLoad;
      const dt = 0.5;
      if (!takeoverFound && !slot.isToday && solar > effLoad) {
        slot.solarTakeover = true; takeoverFound = true;
        battAtTakeover = Math.max(minKwh, batt);
      }
      const inExport = slot.high && slot.time.getTime() >= nowMs - 1800000 && exported < exportableKwh - 0.01;
      if (inExport) {
        const w = priceSum > 0 ? slot.price / priceSum : 1 / Math.max(1, highSlots.length);
        const slotExp = Math.min(w * exportableKwh, exportableKwh - exported);
        batt = Math.max(minKwh, batt - slotExp - Math.max(0, -surplus) * dt);
        exported += slotExp; gridKws.push(0);
      } else if (slot.cheap && batt < maxKwh * 0.95) {
        const canCharge = Math.min(gridChargeKw * dt, maxKwh - batt);
        batt += canCharge; gridChargedKwh += canCharge;
        gridKws.push(gridChargeKw);
      } else {
        gridKws.push(0);
        batt = surplus < 0
          ? Math.max(minKwh, batt + surplus * dt)
          : Math.min(maxKwh, batt + surplus * dt * 0.95);
      }
    }
    if (battAfterExport === null) battAfterExport = battKwh;
    return { battLevels, solarKws, gridKws, battAfterExport, battAtTakeover, gridChargedKwh };
  }

  // ── Canvas ───────────────────────────────────────────────────────────

  _drawCanvas(timeline, sim, solarKwsData, planBattKwhs, planSlots, exportableKwh, capKwh, minKwh, nowMs, currentPlanAction = null, vValue = null) {
    const canvas = this._el("c");
    if (!canvas) return;

    const dpr   = window.devicePixelRatio || 1;
    let dispW   = canvas.offsetWidth || canvas.parentElement?.offsetWidth || 0;
    if (dispW === 0) {
      dispW = 420;
      setTimeout(() => {
        if (this._hass) { this._lastUpdate = 0; this._rafId = requestAnimationFrame(() => this._update()); }
      }, 250);
    }

    const dispH = 270;
    canvas.width  = dispW * dpr;
    canvas.height = dispH * dpr;
    canvas.style.height = dispH + "px";

    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const W = dispW, H = dispH;
    const pL = 44, pR = 64, pT = 22, pB = 42;
    const pw = W - pL - pR, ph = H - pT - pB;
    const n  = timeline.length;

    // Battery level – plan or simulation; subtrahera min-SOC-golvet → 0 = vid min
    const battSrcRaw = planBattKwhs ?? sim?.battLevels;
    const battSrc = battSrcRaw?.map(v => v !== null ? Math.max(0, v - minKwh) : null);

    // Scale – baserat på användbara kWh (exkl. min-SOC-golvet)
    const usableCap = capKwh - minKwh;
    const allBattVals = battSrc ? battSrc.filter(v => v !== null) : [];
    const battPeak = Math.max(...allBattVals, 1);
    const battMax  = Math.min(usableCap + 2, Math.max(8, Math.ceil(battPeak * 1.35 / 4) * 4));

    const xOf  = (i) => pL + (i / Math.max(n - 1, 1)) * pw;
    // Gemensam prisaxel i kr/kWh för spotstaplar (öre/100) och köpprislinjen.
    // Tidigare fast 0-160 öre, vilket kapade allt över 1,6 kr (dagens toppar
    // ligger på 2,5+ kr spot).
    const priceMaxRaw = Math.max(2, vValue || 0, ...timeline.map(s => Math.max((s.price || 0) / 100, s.buy || 0)));
    const priceStep = priceMaxRaw <= 5 ? 1 : 2;
    const priceMax = Math.ceil(priceMaxRaw / priceStep) * priceStep;
    const priceTicks = [];
    for (let v = 0; v <= priceMax; v += priceStep) priceTicks.push(v);
    const yP   = (v) => pT + ph - Math.max(0, Math.min(1, v / priceMax)) * ph;
    const yB   = (v) => pT + ph - Math.max(0, Math.min(1, v / battMax)) * ph;
    const ySOL = (v) => pT + ph - Math.max(0, Math.min(1, v / 12)) * ph;

    const fz   = Math.max(10, Math.min(12, Math.round(dispW / 55)));
    const font = `${fz}px sans-serif`;
    const dark  = this._isDark();
    // Höjd alpha mot tidigare (0.5/0.45 - för svagt för skalsiffror på
    // liten skärm, samma live-feedback 2026-09-23).
    const muted = dark ? "rgba(255,255,255,0.62)" : "rgba(0,0,0,0.58)";
    const grid  = dark ? "rgba(255,255,255,0.09)" : "rgba(0,0,0,0.09)";

    ctx.clearRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = grid; ctx.lineWidth = 0.5;
    priceTicks.filter(v => v > 0).forEach(v => {
      ctx.beginPath(); ctx.moveTo(pL, yP(v)); ctx.lineTo(pL + pw, yP(v)); ctx.stroke();
    });

    // Plan slot backgrounds
    const hasPlan = planBattKwhs !== null;
    if (hasPlan && planSlots) {
      const slotW = pw / Math.max(n - 1, 1);
      timeline.forEach((slot, i) => {
        const act = slot.planAction;
        if (!act || act === "idle") return;
        const bg = actionBg(act, dark ? "0.26" : "0.18");
        if (!bg) return;
        const x = xOf(i) - slotW / 2;
        ctx.fillStyle = bg;
        ctx.fillRect(Math.max(pL, x), pT, Math.min(slotW, pL + pw - Math.max(pL, x)), ph);
      });

      // Export zone label
      const hiSlots = timeline.filter(s => s.planAction === "export");
      if (hiSlots.length && exportableKwh >= 0.1) {
        const slotW2 = pw / (n - 1);
        const x1 = xOf(timeline.indexOf(hiSlots[0])) - slotW2 / 2;
        const x2 = xOf(timeline.indexOf(hiSlots[hiSlots.length-1])) + slotW2 / 2;
        ctx.strokeStyle = "#f07814"; ctx.lineWidth = 2; ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(x1, pT + 1); ctx.lineTo(x2, pT + 1); ctx.stroke();
        ctx.fillStyle = "#f07814"; ctx.font = `bold ${fz}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText("↑ EXPORT", (x1 + x2) / 2, pT + fz + 3);
        ctx.font = font;
      }
    } else {
      // Fallback: export zone highlight
      if (exportableKwh >= 0.1) {
        const hiIdxs = timeline.map((s, i) => s.high ? i : -1).filter(i => i >= 0);
        if (hiIdxs.length) {
          const bw = pw / (n - 1);
          hiIdxs.forEach(i => {
            ctx.fillStyle = dark ? "rgba(240,120,20,0.18)" : "rgba(240,120,20,0.12)";
            ctx.fillRect(xOf(i) - bw / 2, pT, bw, ph);
          });
          const x1 = xOf(hiIdxs[0]) - bw / 2, x2 = xOf(hiIdxs[hiIdxs.length-1]) + bw / 2;
          ctx.strokeStyle = "#f07814"; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(x1, pT + 1); ctx.lineTo(x2, pT + 1); ctx.stroke();
          ctx.fillStyle = "#f07814"; ctx.font = `bold ${fz}px sans-serif`;
          ctx.textAlign = "center";
          ctx.fillText("↑ EXPORT", (x1 + x2) / 2, pT + fz + 3);
          ctx.font = font;
        }
      }
      // Cheap grid charge zone (fallback sim)
      if (sim) {
        const cheapIdxs = timeline.map((s, i) => s.cheap ? i : -1).filter(i => i >= 0);
        if (cheapIdxs.length) {
          const bw = pw / (n - 1);
          let gi = 0;
          while (gi < cheapIdxs.length) {
            let gj = gi;
            while (gj + 1 < cheapIdxs.length && cheapIdxs[gj+1] === cheapIdxs[gj]+1) gj++;
            const x1 = xOf(cheapIdxs[gi]), x2 = xOf(cheapIdxs[gj]) + bw;
            ctx.fillStyle = "rgba(27,175,122,0.07)";
            ctx.fillRect(x1, pT, x2 - x1, ph);
            gi = gj + 1;
          }
        }
      }
    }

    // Midnight marker
    const midIdx = timeline.findIndex(s => s.isMidnight);
    if (midIdx >= 0) {
      ctx.strokeStyle = dark ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.18)";
      ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(xOf(midIdx), pT); ctx.lineTo(xOf(midIdx), pT + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = muted; ctx.font = font; ctx.textAlign = "center";
      ctx.fillText("midnatt", xOf(midIdx), pT - 5);
    }

    // Solar takeover marker
    const tkIdx = timeline.findIndex(s => s.solarTakeover);
    if (tkIdx >= 0) {
      ctx.strokeStyle = "#2a78d6"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(xOf(tkIdx), pT); ctx.lineTo(xOf(tkIdx), pT + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#2a78d6"; ctx.font = font; ctx.textAlign = "left";
      ctx.fillText(`☀ ${timeline[tkIdx].label}`, xOf(tkIdx) + 3, pT + fz + 1);
    }

    // Price bars
    const barW = (pw / n) * 0.76;
    timeline.forEach((slot, i) => {
      const x = xOf(i) - barW / 2;
      const yTop = yP((slot.price || 0) / 100), yBot = yP(0);
      const r = 2;
      const isExport = hasPlan ? (slot.planAction === "export") : slot.high;
      ctx.fillStyle = isExport ? "#f07814" : (dark ? PRICE_COLOR.dark : PRICE_COLOR.light);
      ctx.beginPath();
      ctx.moveTo(x + r, yTop);
      ctx.lineTo(x + barW - r, yTop);
      ctx.quadraticCurveTo(x + barW, yTop, x + barW, yTop + r);
      ctx.lineTo(x + barW, yBot); ctx.lineTo(x, yBot); ctx.lineTo(x, yTop + r);
      ctx.quadraticCurveTo(x, yTop, x + r, yTop);
      ctx.fill();
    });

    // Köppris (inkl. avgifter+moms) som stegad linje ovanpå spotstaplarna
    ctx.strokeStyle = dark ? "rgba(255,255,255,0.92)" : "rgba(0,0,0,0.8)";
    ctx.lineWidth = 2; ctx.lineJoin = "miter";
    const stepHalf = (pw / Math.max(n - 1, 1)) / 2;
    let buyOpen = false;
    timeline.forEach((slot, i) => {
      if (slot.buy == null) {
        if (buyOpen) { ctx.stroke(); buyOpen = false; }
        return;
      }
      const y = yP(slot.buy);
      if (!buyOpen) { ctx.beginPath(); ctx.moveTo(xOf(i) - stepHalf, y); buyOpen = true; }
      else ctx.lineTo(xOf(i) - stepHalf, y);
      ctx.lineTo(xOf(i) + stepHalf, y);
    });
    if (buyOpen) ctx.stroke();

    // Sparvärdet V (planens marginalvärde): köppris över linjen → urladdning,
    // under → köp från nätet och spara batteriet.
    if (vValue != null && vValue > 0) {
      const vCol = dark ? "#c084fc" : "#7e22ce";
      const yV = yP(vValue);
      ctx.strokeStyle = vCol; ctx.lineWidth = 2; ctx.setLineDash([7, 4]);
      ctx.beginPath(); ctx.moveTo(pL, yV); ctx.lineTo(pL + pw, yV); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = vCol; ctx.font = `bold ${fz}px sans-serif`; ctx.textAlign = "right";
      ctx.fillText(`V ${vValue.toFixed(2)} kr`, pL + pw - 4, yV - 4);
      ctx.font = font;
    }

    // Solar line – alltid från solarKwsData (beräknat i _update oavsett plan/sim)
    ctx.strokeStyle = "#eda100"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]);
    ctx.beginPath();
    let solFirst = true;
    solarKwsData.forEach((sv, i) => {
      if (sv < 0.05) { if (!solFirst) { ctx.stroke(); ctx.beginPath(); solFirst = true; } return; }
      if (solFirst) { ctx.moveTo(xOf(i), ySOL(sv)); solFirst = false; }
      else ctx.lineTo(xOf(i), ySOL(sv));
    });
    ctx.stroke();
    ctx.setLineDash([]);

    if (battSrc) {
      // Fill
      const battGrad = ctx.createLinearGradient(0, yB(battMax), 0, yB(0));
      battGrad.addColorStop(0, "rgba(27,175,122,0.15)");
      battGrad.addColorStop(1, "rgba(27,175,122,0.03)");
      ctx.fillStyle = battGrad;
      ctx.beginPath();
      let firstSet = false;
      battSrc.forEach((v, i) => {
        if (v === null) return;
        if (!firstSet) { ctx.moveTo(xOf(i), yB(v)); firstSet = true; }
        else ctx.lineTo(xOf(i), yB(v));
      });
      const lastI = battSrc.reduce((acc, v, i) => v !== null ? i : acc, 0);
      ctx.lineTo(xOf(lastI), pT + ph); ctx.lineTo(xOf(0), pT + ph);
      ctx.closePath(); ctx.fill();

      // Line
      ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 2; ctx.lineJoin = "round";
      ctx.beginPath();
      firstSet = false;
      battSrc.forEach((v, i) => {
        if (v === null) return;
        if (!firstSet) { ctx.moveTo(xOf(i), yB(v)); firstSet = true; }
        else ctx.lineTo(xOf(i), yB(v));
      });
      ctx.stroke();

      // Endpoint dot
      const lastVal = battSrc[battSrc.reduce((acc, v, i) => v !== null ? i : acc, 0)];
      if (lastVal !== null) {
        ctx.fillStyle = "#1baf7a";
        ctx.beginPath(); ctx.arc(xOf(n-1), yB(lastVal), 4, 0, Math.PI*2); ctx.fill();
      }
    }

    // Sim grid charge segments highlighted
    if (sim) {
      let gcFirst = true, gcOpen = false;
      ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 3.5;
      sim.gridKws.forEach((gkw, i) => {
        if (gkw > 0) {
          if (gcFirst) { ctx.beginPath(); ctx.moveTo(xOf(i), yB(sim.battLevels[i])); gcFirst = false; gcOpen = true; }
          else ctx.lineTo(xOf(i), yB(sim.battLevels[i]));
        } else if (gcOpen) { ctx.stroke(); gcOpen = false; gcFirst = true; }
      });
      if (gcOpen) ctx.stroke();
    }

    // "Nu" marker
    ctx.strokeStyle = dark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.2)";
    ctx.lineWidth = 1; ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(xOf(0), pT); ctx.lineTo(xOf(0), pT + ph); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = muted; ctx.font = font; ctx.textAlign = "center";
    ctx.fillText("nu", xOf(0), pT + ph + fz + 2);

    // Aktuell plan-åtgärd bredvid "nu"-linjen
    if (currentPlanAction && currentPlanAction !== "idle") {
      const _ac = ACTION_COLOR[currentPlanAction];
      ctx.fillStyle = _ac?.border || "#888";
      ctx.font = `bold ${fz}px sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(_ac?.label ?? currentPlanAction, xOf(0) + 4, pT + ph - 6);
      ctx.font = font;
    }

    // Left axis – price
    ctx.fillStyle = muted; ctx.font = font; ctx.textAlign = "right";
    priceTicks.forEach(v => ctx.fillText(v + " kr", pL - 4, yP(v) + fz * 0.4));

    // Right axis – battery
    ctx.textAlign = "left";
    const battTickStep = battMax <= 8 ? 2 : battMax <= 16 ? 4 : battMax <= 25 ? 5 : 10;
    for (let v = 0; v <= battMax; v += battTickStep) {
      ctx.fillStyle = muted;
      ctx.fillRect(pL + pw, yB(v) - 0.5, 4, 1);
      ctx.fillText(v + " kWh", pL + pw + 6, yB(v) + fz * 0.4);
    }

    // X-axis labels every 2h
    ctx.textAlign = "center"; ctx.fillStyle = muted;
    timeline.forEach((s, i) => {
      if (s.time.getHours() % 2 === 0 && s.time.getMinutes() === 0) {
        ctx.fillText(s.label, xOf(i), pT + ph + fz + 2);
      }
    });
  }
}

if (!customElements.get("sem-energy-plan-card")) {
  customElements.define("sem-energy-plan-card", SemEnergyPlanCard);
}
