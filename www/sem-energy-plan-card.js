/**
 * SEM Energy Plan Card – simulerar batterinivå, export, sol-takeover och
 * billig nätladdning för resten av kvällen och natten fram till 09:00.
 *
 *   type: custom:sem-energy-plan-card
 *   # Valfria overrides:
 *   battery_cap_kwh: 33
 *   battery_min_pct: 10
 *   export_percentile: 80
 *   charge_percentile: 25
 *   grid_charge_kw: 4
 *   night_house_load_w: 700
 */

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
      battery_soc:          "sensor.sonnenbatterie_271100_state_battery_percentage_real",
      battery_wh:           "sensor.sonnenbatterie_271100_battery_remaining_capacity_usable",
      solar_w:              "sensor.sonnenbatterie_271100_state_production",
      house_w:              "sensor.sonnenbatterie_271100_state_consumption_current",
      solcast_tomorrow_kwh: "sensor.solcast_pv_forecast_forecast_tomorrow",
      solcast_peak_w:       "sensor.solcast_pv_forecast_peak_forecast_tomorrow",
      solcast_peak_time:    "sensor.solcast_pv_forecast_peak_time_tomorrow",
      battery_cap_kwh:      33,
      battery_usable_pct:   93,
      battery_min_pct:      10,
      export_percentile:    80,
      operating_mode:       "select.smart_energy_manager_operating_mode",
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

  getCardSize() { return 8; }

  // ── DOM ──────────────────────────────────────────────────────────────

  _build() {
    this.shadowRoot.innerHTML = `
<style>
:host { display: block; }
ha-card { padding: 16px 16px 12px; }
h3 { margin: 0 0 12px; font-size: 13px; font-weight: 500; color: var(--primary-text-color); }
.stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin-bottom: 12px; }
.st { background: var(--secondary-background-color); border-radius: 8px; padding: 8px 10px; }
.stl { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 3px; }
.stv { font-size: 15px; font-weight: 500; color: var(--primary-text-color); }
canvas { display: block; width: 100%; }
.leg { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: var(--secondary-text-color); margin: 6px 0 10px; }
.leg span { display: flex; align-items: center; gap: 5px; }
.lsq { width: 10px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.lhatch { width: 10px; height: 8px; border-radius: 2px; flex-shrink: 0;
          background: rgba(240,120,20,0.75); }
.lhatch-g { width: 10px; height: 8px; border-radius: 2px; flex-shrink: 0;
            background: repeating-linear-gradient(45deg,rgba(27,175,122,0.4) 0,rgba(27,175,122,0.4) 2px,transparent 2px,transparent 5px); }
.phases { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; }
.ph { border-left: 3px solid; border-radius: 0 6px 6px 0; padding: 7px 9px;
      background: var(--secondary-background-color); }
.pht { font-size: 11px; font-weight: 500; margin-bottom: 2px; }
.phd { font-size: 11px; color: var(--secondary-text-color); line-height: 1.35; }
</style>
<ha-card>
  <h3>Energiplan — kväll → 09:00 imorgon</h3>
  <div class="stats">
    <div class="st"><div class="stl">Batteri nu</div><div class="stv" id="v-soc">–</div></div>
    <div class="st"><div class="stl">Exporterbart</div><div class="stv" id="v-exp">–</div></div>
    <div class="st"><div class="stl">Toppris kväll</div><div class="stv" id="v-peak">–</div></div>
    <div class="st"><div class="stl">Sol imorgon</div><div class="stv" id="v-sol">–</div></div>
  </div>
  <canvas id="c" height="270"></canvas>
  <div class="leg">
    <span><span class="lsq" style="background:rgba(42,120,214,0.7)"></span>Nordpool (mörk = högt)</span>
    <span><span class="lhatch"></span>Exportfönster</span>
    <span id="leg-charge" style="opacity:0.35"><span class="lhatch-g"></span>Billig laddning (vinterläge)</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2.5px solid #1baf7a"></span>&nbsp;Batteri kWh</span>
    <span><span style="display:inline-block;width:16px;height:0;border-top:2px dashed #eda100"></span>&nbsp;Sol kW</span>
  </div>
  <div class="phases">
    <div class="ph" style="border-color:#f07814">
      <div class="pht" style="color:#f07814" id="ph1t">Export</div>
      <div class="phd" id="ph1d">–</div>
    </div>
    <div class="ph" style="border-color:#888">
      <div class="pht" style="color:#888">Nattvila</div>
      <div class="phd" id="ph2d">–</div>
    </div>
    <div class="ph" style="border-color:#1baf7a">
      <div class="pht" style="color:#1baf7a" id="ph3t">Sol-takeover</div>
      <div class="phd" id="ph3d">–</div>
    </div>
  </div>
</ha-card>`;

    // Trigger re-render as soon as the card gets a real layout width
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

  // ── Helpers ──────────────────────────────────────────────────────────

  _getFloat(eid, def = 0) {
    const s = this._hass?.states[eid];
    return s ? (parseFloat(s.state) || def) : def;
  }

  _slotVal(slot) {
    if (slot == null) return 0;
    if (typeof slot === "object") return slot.value ?? slot.price ?? 0;
    return slot || 0;
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
    const gridChargeEnabled = opMode === "winter" || opMode === "force_charge_battery";

    const capKwh = cfg.battery_cap_kwh;
    const minKwh = capKwh * (cfg.battery_min_pct / 100);
    const battKwh = battWh > 100
      ? battWh / 1000
      : (battPct / 100) * capKwh * (cfg.battery_usable_pct / 100);

    const now = new Date();
    const timeline = this._buildTimeline(now, rawToday, rawTomorrow);

    const nowMs = now.getTime();

    // ── Solar profile (Gaussian, sigma=2.0) – definieras tidigt för att
    // kunna avgöra vilka slots som är "höga" (sol < 2 kW = kval. för export)
    const peakTimeTomorrow = peakStr ? new Date(peakStr) : null;
    const SIGMA = 2.0;
    const getSolarKw = (t) => {
      const h = t.getHours() + t.getMinutes() / 60;
      const isToday = t.toDateString() === now.toDateString();
      if (isToday) {
        if (h < 4 || h > 21.5) return 0;
        const dtH = (t.getTime() - nowMs) / 3600000;
        return Math.max(0, (solarW / 1000) * Math.exp(-dtH * 0.7));
      }
      if (!peakTimeTomorrow || peakW <= 0) return 0;
      const ph = peakTimeTomorrow.getHours() + peakTimeTomorrow.getMinutes() / 60;
      return Math.max(0, (peakW / 1000) * Math.exp(-0.5 * ((h - ph) / SIGMA) ** 2));
    };

    // ── High-price slots (export window) ─────────────────────────────
    // Inkludera imorgon bittis slots om sol < 2 kW – speglar energy_controller.
    // Utan detta exporteras allt kväll och inget finns kvar till morgonens prispeak.
    const todayVals = rawToday.map(s => this._slotVal(s)).filter(v => v > 0).sort((a, b) => a - b);
    const expIdx = Math.min(Math.floor((cfg.export_percentile / 100) * todayVals.length), todayVals.length - 1);
    const expThreshold = todayVals[expIdx] || 100;
    const COMPETING_SOLAR_KW = 2.0;
    const windowEndMs = nowMs + 20 * 3600000;
    timeline.forEach(s => {
      s.high = s.price >= expThreshold
        && s.time.getTime() >= nowMs - 1800000
        && s.time.getTime() < windowEndMs
        && getSolarKw(s.time) < COMPETING_SOLAR_KW;
    });

    // ── Cheap overnight slots (charging window) ───────────────────────
    // Visas bara i winter/force_charge_battery – i auto-läge laddas från sol, inte nät.
    if (gridChargeEnabled) {
      const overnightPrices = timeline
        .filter(s => { const h = s.time.getHours(); return (h >= 21 || h < 7); })
        .map(s => s.price)
        .filter(v => v > 0)
        .sort((a, b) => a - b);
      const chgIdx = Math.min(Math.floor((cfg.charge_percentile / 100) * overnightPrices.length), overnightPrices.length - 1);
      const chgThreshold = overnightPrices[chgIdx] ?? 10;
      timeline.forEach(s => {
        const h = s.time.getHours();
        const overnight = (h >= 21 || h < 7);
        s.cheap = overnight && s.price > 0 && s.price <= chgThreshold && s.time.getTime() > nowMs;
      });
    } else {
      timeline.forEach(s => { s.cheap = false; });
    }

    // ── Solar takeover time (analytical, used for export floor) ──────
    const nightKw = cfg.night_house_load_w / 1000;
    const houseKw = houseW / 1000;
    // Takeover = when solar exceeds house load (not night load)
    const takeoverThresh = Math.max(nightKw, houseKw * 0.7);
    let takeoverH = 8.0;
    if (peakTimeTomorrow && peakW / 1000 > takeoverThresh) {
      const ph = peakTimeTomorrow.getHours() + peakTimeTomorrow.getMinutes() / 60;
      const inner = Math.log(peakW / 1000 / takeoverThresh);
      if (inner > 0) takeoverH = Math.max(5.0, ph - SIGMA * Math.sqrt(2 * inner));
    }

    // Export floor anchored to first today slot (not "now") — matches energy_controller fix
    const firstSlotTime = timeline.find(s => s.isToday)?.time ?? now;
    const windowStart = firstSlotTime < now ? now : firstSlotTime;
    const takeoverTomorrow = new Date(now);
    takeoverTomorrow.setDate(takeoverTomorrow.getDate() + 1);
    takeoverTomorrow.setHours(Math.floor(takeoverH), Math.round((takeoverH % 1) * 60), 0, 0);
    const hoursDark = Math.max(1, (takeoverTomorrow.getTime() - windowStart.getTime()) / 3600000);
    const exportFloor = hoursDark * nightKw + 2.0;
    const exportableKwh = Math.max(0, battKwh - minKwh - exportFloor);

    // ── Simulation ────────────────────────────────────────────────────
    const sim = this._simulate(
      timeline, battKwh, minKwh, exportableKwh, capKwh,
      getSolarKw, houseKw, nightKw, cfg.grid_charge_kw, nowMs
    );

    // ── Stat tiles ────────────────────────────────────────────────────
    const highPrices = timeline.filter(s => s.high).map(s => s.price);
    const peakPrice  = highPrices.length ? Math.max(...highPrices) : 0;
    this._el("v-soc").textContent  = `${battPct.toFixed(0)}% · ${battKwh.toFixed(1)} kWh`;
    this._el("v-exp").textContent  = `${exportableKwh.toFixed(1)} kWh`;
    this._el("v-peak").textContent = `${peakPrice.toFixed(0)} öre`;
    this._el("v-sol").textContent  = `${solTomKwh.toFixed(1)} kWh`;
    const legCharge = this._el("leg-charge");
    if (legCharge) legCharge.style.opacity = gridChargeEnabled ? "1" : "0.35";

    // ── Phase cards ───────────────────────────────────────────────────

    // Phase 1: Export
    if (exportableKwh < 0.1) {
      this._el("ph1t").textContent = "Ingen export";
      this._el("ph1d").textContent = `Golv ${(exportFloor + minKwh).toFixed(1)} kWh · batteri ${battKwh.toFixed(1)} kWh`;
    } else {
      const hs = timeline.filter(s => s.high);
      const expRange = hs.length ? `${hs[0].label}–${hs[hs.length - 1].label}` : "–";
      this._el("ph1t").textContent = `${expRange} export`;
      this._el("ph1d").textContent =
        `${exportableKwh.toFixed(1)} kWh prisväktat · ${battKwh.toFixed(1)} → ${sim.battAfterExport.toFixed(1)} kWh`;
    }

    // Phase 2: Nattvila + grid charging
    const cs = timeline.filter(s => s.cheap);
    const gridChgKwh = sim.gridChargedKwh;
    let ph2text = `${sim.battAfterExport.toFixed(1)} → ${sim.battAtTakeover.toFixed(1)} kWh · ${nightKw.toFixed(1)} kW hus`;
    if (gridChgKwh > 0.1 && cs.length) {
      ph2text += ` · +${gridChgKwh.toFixed(1)} kWh nätladdning ${cs[0].label}–${cs[cs.length-1].label}`;
    }
    this._el("ph2d").textContent = ph2text;

    // Phase 3: Sol-takeover — use simulation's detected slot for label
    const tkSlot = timeline.find(s => s.solarTakeover);
    const tkHH = String(Math.floor(takeoverH)).padStart(2, "0");
    const tkMM = String(Math.round((takeoverH % 1) * 60)).padStart(2, "0");
    const tkLabel = tkSlot ? tkSlot.label : `${tkHH}:${tkMM}`;
    this._el("ph3t").textContent = `Sol-takeover ~${tkLabel}`;

    // Beräkna när batteriet är fullt (92% av kapacitet) efter sol-takeover.
    // Letar först inom chart-fönstret, sedan fortsätter 30h framåt.
    const fullThresh  = capKwh * (cfg.battery_usable_pct / 100) * 0.92;
    const maxKwhFull  = capKwh * 0.99;
    let battFullLabel = null;
    const tkIdx = timeline.findIndex(s => s.solarTakeover);
    for (let i = Math.max(0, tkIdx); i < timeline.length; i++) {
      if ((sim.battLevels[i] ?? 0) >= fullThresh) {
        battFullLabel = timeline[i].label;
        break;
      }
    }
    if (!battFullLabel && tkIdx >= 0) {
      let extBatt = sim.battLevels[sim.battLevels.length - 1] ?? battKwh;
      let extTime  = new Date(timeline[timeline.length - 1].time.getTime() + 30 * 60000);
      const extEnd = new Date(nowMs + 30 * 3600000);
      while (extTime <= extEnd) {
        const solar   = getSolarKw(extTime);
        const h       = extTime.getHours();
        const effLoad = (h >= 22 || h < 6) ? nightKw : houseKw;
        const surplus = solar - effLoad;
        extBatt = surplus > 0
          ? Math.min(maxKwhFull, extBatt + surplus * 0.5 * 0.95)
          : Math.max(minKwh, extBatt + surplus * 0.5);
        if (extBatt >= fullThresh) {
          const hh = String(extTime.getHours()).padStart(2, "0");
          const mm = String(extTime.getMinutes()).padStart(2, "0");
          battFullLabel = `${hh}:${mm}`;
          break;
        }
        extTime = new Date(extTime.getTime() + 30 * 60000);
      }
    }

    let ph3text = peakW > 0
      ? `Sol > last · ${(peakW / 1000).toFixed(1)} kW peak imorgon`
      : "Sol > hushållslast";
    if (battFullLabel) ph3text += ` · fullt ~${battFullLabel}`;
    this._el("ph3d").textContent = ph3text;

    this._drawCanvas(timeline, sim, exportableKwh, capKwh);
  }

  // ── Timeline ─────────────────────────────────────────────────────────

  _buildTimeline(now, rawToday, rawTomorrow) {
    const slots = [];
    const cur = new Date(now);
    cur.setMinutes(cur.getMinutes() < 30 ? 0 : 30, 0, 0);
    const end = new Date(now);
    end.setDate(end.getDate() + 1);
    end.setHours(9, 0, 0, 0);

    while (cur <= end) {
      const isToday = cur.toDateString() === now.toDateString();
      const raw = isToday ? rawToday : rawTomorrow;
      const h = cur.getHours(), m = cur.getMinutes();
      const i = h * 4 + Math.floor(m / 15);
      const p1 = this._slotVal(raw[i]);
      const p2 = this._slotVal(raw[i + 1] ?? raw[i]);
      slots.push({
        time:          new Date(cur),
        label:         `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`,
        price:         (p1 + p2) / 2,
        isToday,
        isMidnight:    !isToday && h === 0 && m === 0,
        high:          false,
        cheap:         false,
        solarTakeover: false,
      });
      cur.setTime(cur.getTime() + 30 * 60000);
    }
    return slots;
  }

  // ── Simulation ────────────────────────────────────────────────────────

  _simulate(timeline, battKwh, minKwh, exportableKwh, capKwh,
            getSolarKw, houseKw, nightKw, gridChargeKw, nowMs) {
    const highSlots    = timeline.filter(s => s.high);
    const priceSum     = highSlots.reduce((a, s) => a + s.price, 0);
    const maxKwh       = capKwh * 0.99;
    // Index för den sista höga slotten — battAfterExport fångas EFTER denna.
    const lastHighIdx  = timeline.reduce((acc, s, i) => s.high ? i : acc, -1);

    let batt           = battKwh;
    let exported       = 0;
    let gridChargedKwh = 0;
    let battAfterExport = null;
    let battAtTakeover  = battKwh;
    let takeoverFound   = false;
    const battLevels    = [];
    const solarKws      = [];
    const gridKws       = [];

    for (let si = 0; si < timeline.length; si++) {
      const slot = timeline[si];
      battLevels.push(parseFloat(Math.max(minKwh, batt).toFixed(2)));

      // Fånga battAfterExport på första slotten EFTER sista exportslotten
      if (battAfterExport === null && lastHighIdx >= 0 && si === lastHighIdx + 1) {
        battAfterExport = Math.max(minKwh, batt);
      }

      const solar = getSolarKw(slot.time);
      solarKws.push(solar);

      const h       = slot.time.getHours();
      const effLoad = (h >= 22 || h < 6) ? nightKw : houseKw;
      const surplus = solar - effLoad;
      const dt      = 0.5;

      // Sol-takeover: first tomorrow slot where solar > house load
      if (!takeoverFound && !slot.isToday && solar > effLoad) {
        slot.solarTakeover = true;
        takeoverFound      = true;
        battAtTakeover     = Math.max(minKwh, batt);
      }

      const inExport = slot.high && slot.time.getTime() >= nowMs - 1800000 && exported < exportableKwh - 0.01;

      if (inExport) {
        const weight  = priceSum > 0 ? slot.price / priceSum : 1 / Math.max(1, highSlots.length);
        const slotExp = Math.min(weight * exportableKwh, exportableKwh - exported);
        const hNeeded = Math.max(0, -surplus) * dt;
        batt     = Math.max(minKwh, batt - slotExp - hNeeded);
        exported += slotExp;
        gridKws.push(0);
      } else {
        // Grid charging during cheap overnight slots
        if (slot.cheap && batt < maxKwh * 0.95) {
          const canCharge = Math.min(gridChargeKw * dt, maxKwh - batt);
          batt           += canCharge;
          gridChargedKwh += canCharge;
          gridKws.push(gridChargeKw);
        } else {
          gridKws.push(0);
          if (surplus < 0) {
            batt = Math.max(minKwh, batt + surplus * dt);
          } else {
            batt = Math.min(maxKwh, batt + surplus * dt * 0.95);
          }
        }
      }
    }

    if (battAfterExport === null) battAfterExport = battKwh;

    return { battLevels, solarKws, gridKws, battAfterExport, battAtTakeover, gridChargedKwh };
  }

  // ── Canvas ───────────────────────────────────────────────────────────

  _drawCanvas(timeline, sim, exportableKwh, capKwh) {
    const canvas = this._el("c");
    if (!canvas) return;

    const dpr   = window.devicePixelRatio || 1;
    let dispW   = canvas.offsetWidth || canvas.parentElement?.offsetWidth || 0;

    // If not laid out yet, use a safe fallback and retry once layout settles
    if (dispW === 0) {
      dispW = 420;
      setTimeout(() => {
        if (this._hass) {
          this._lastUpdate = 0;
          this._rafId = requestAnimationFrame(() => this._update());
        }
      }, 250);
    }

    const dispH    = 270;
    canvas.width   = dispW * dpr;
    canvas.height  = dispH * dpr;
    canvas.style.height = dispH + "px";

    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const W  = dispW, H = dispH;
    const pL = 44, pR = 64, pT = 22, pB = 42;
    const pw = W - pL - pR, ph = H - pT - pB;
    const n  = timeline.length;

    // Dynamic battery scale
    const battPeak = Math.max(...sim.battLevels, 1);
    const battMax  = Math.min(capKwh + 2, Math.max(12, Math.ceil(battPeak * 1.35 / 4) * 4));

    const xOf  = (i) => pL + (i / Math.max(n - 1, 1)) * pw;
    const yP   = (v) => pT + ph - Math.max(0, Math.min(1, v / 160)) * ph;
    const yB   = (v) => pT + ph - Math.max(0, Math.min(1, v / battMax)) * ph;
    const ySOL = (v) => pT + ph - Math.max(0, Math.min(1, v / 12)) * ph;

    const fz   = Math.max(10, Math.min(12, Math.round(dispW / 55)));
    const font = `${fz}px sans-serif`;

    const dark  = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const muted = dark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.45)";
    const grid  = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)";

    ctx.clearRect(0, 0, W, H);

    // Price grid lines
    ctx.strokeStyle = grid; ctx.lineWidth = 0.5;
    [40, 80, 120, 160].forEach(v => {
      ctx.beginPath(); ctx.moveTo(pL, yP(v)); ctx.lineTo(pL + pw, yP(v)); ctx.stroke();
    });

    // Cheap overnight zone (green tint)
    const cheapIdxs = timeline.map((s, i) => s.cheap ? i : -1).filter(i => i >= 0);
    if (cheapIdxs.length) {
      const bw = pw / (n - 1);
      let gi = 0;
      while (gi < cheapIdxs.length) {
        let gj = gi;
        while (gj + 1 < cheapIdxs.length && cheapIdxs[gj + 1] === cheapIdxs[gj] + 1) gj++;
        const x1 = xOf(cheapIdxs[gi]);
        const x2 = xOf(cheapIdxs[gj]) + bw;
        ctx.fillStyle = "rgba(27,175,122,0.07)";
        ctx.fillRect(x1, pT, x2 - x1, ph);
        ctx.strokeStyle = "rgba(27,175,122,0.22)"; ctx.lineWidth = 1; ctx.setLineDash([]);
        [x1, x2].forEach(x => { ctx.beginPath(); ctx.moveTo(x, pT); ctx.lineTo(x, pT + ph); ctx.stroke(); });
        gi = gj + 1;
      }
    }

    // Export zone (orange) — only when exportable > 0
    if (exportableKwh >= 0.1) {
      const hiIdxs = timeline.map((s, i) => s.high ? i : -1).filter(i => i >= 0);
      if (hiIdxs.length) {
        const bw = pw / (n - 1);
        // Strong per-slot background tint
        hiIdxs.forEach(i => {
          ctx.fillStyle = dark ? "rgba(240,120,20,0.18)" : "rgba(240,120,20,0.12)";
          ctx.fillRect(xOf(i) - bw / 2, pT, bw, ph);
        });
        // Solid top border line across the export zone
        const x1 = xOf(hiIdxs[0]) - bw / 2, x2 = xOf(hiIdxs[hiIdxs.length - 1]) + bw / 2;
        ctx.strokeStyle = "#f07814"; ctx.lineWidth = 2; ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(x1, pT + 1); ctx.lineTo(x2, pT + 1); ctx.stroke();
        // Vertical boundary lines
        ctx.lineWidth = 1.5;
        [x1, x2].forEach(x => { ctx.beginPath(); ctx.moveTo(x, pT); ctx.lineTo(x, pT + ph); ctx.stroke(); });
        // "EXPORT" label inside the zone
        ctx.fillStyle = "#f07814";
        ctx.font = `bold ${fz}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText("↑ EXPORT", (x1 + x2) / 2, pT + fz + 3);
        ctx.font = font;
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

    // Sol-takeover marker
    const tkIdx = timeline.findIndex(s => s.solarTakeover);
    if (tkIdx >= 0) {
      ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(xOf(tkIdx), pT); ctx.lineTo(xOf(tkIdx), pT + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#1baf7a"; ctx.font = font; ctx.textAlign = "left";
      ctx.fillText(`☀ ${timeline[tkIdx].label}`, xOf(tkIdx) + 3, pT + fz + 1);
    }

    // Price bars
    const barW = (pw / n) * 0.76;
    timeline.forEach((slot, i) => {
      const x = xOf(i) - barW / 2;
      const yTop = yP(slot.price), yBot = yP(0);
      const r = 2;
      ctx.fillStyle = slot.high ? "#f07814" : (dark ? "rgba(42,120,214,0.18)" : "rgba(42,120,214,0.15)");
      ctx.beginPath();
      ctx.moveTo(x + r, yTop);
      ctx.lineTo(x + barW - r, yTop);
      ctx.quadraticCurveTo(x + barW, yTop, x + barW, yTop + r);
      ctx.lineTo(x + barW, yBot);
      ctx.lineTo(x, yBot);
      ctx.lineTo(x, yTop + r);
      ctx.quadraticCurveTo(x, yTop, x + r, yTop);
      ctx.fill();
    });

    // Solar line (dashed amber)
    ctx.strokeStyle = "#eda100"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]);
    ctx.beginPath();
    let solFirst = true;
    timeline.forEach((_, i) => {
      const sv = sim.solarKws[i];
      if (sv < 0.05) { if (!solFirst) { ctx.stroke(); ctx.beginPath(); solFirst = true; } return; }
      if (solFirst) { ctx.moveTo(xOf(i), ySOL(sv)); solFirst = false; }
      else ctx.lineTo(xOf(i), ySOL(sv));
    });
    ctx.stroke();
    ctx.setLineDash([]);

    // Battery fill (gradient: green for solar, teal for grid charge)
    const battGrad = ctx.createLinearGradient(0, yB(battMax), 0, yB(0));
    battGrad.addColorStop(0, "rgba(27,175,122,0.15)");
    battGrad.addColorStop(1, "rgba(27,175,122,0.03)");
    ctx.fillStyle = battGrad;
    ctx.beginPath();
    sim.battLevels.forEach((v, i) => {
      if (i === 0) ctx.moveTo(xOf(i), yB(v)); else ctx.lineTo(xOf(i), yB(v));
    });
    ctx.lineTo(xOf(n - 1), pT + ph); ctx.lineTo(xOf(0), pT + ph); ctx.closePath(); ctx.fill();

    // Grid charge segments highlighted on battery line
    let gcFirst = true, gcOpen = false;
    ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 3.5;
    sim.gridKws.forEach((gkw, i) => {
      if (gkw > 0) {
        if (gcFirst) { ctx.beginPath(); ctx.moveTo(xOf(i), yB(sim.battLevels[i])); gcFirst = false; gcOpen = true; }
        else ctx.lineTo(xOf(i), yB(sim.battLevels[i]));
      } else if (gcOpen) { ctx.stroke(); gcOpen = false; gcFirst = true; }
    });
    if (gcOpen) ctx.stroke();

    // Battery line
    ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 2; ctx.lineJoin = "round";
    ctx.beginPath();
    sim.battLevels.forEach((v, i) => {
      if (i === 0) ctx.moveTo(xOf(i), yB(v)); else ctx.lineTo(xOf(i), yB(v));
    });
    ctx.stroke();

    // Battery endpoint dot
    ctx.fillStyle = "#1baf7a";
    ctx.beginPath(); ctx.arc(xOf(n - 1), yB(sim.battLevels[n - 1]), 4, 0, Math.PI * 2); ctx.fill();

    // "Nu" marker
    ctx.strokeStyle = dark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.2)";
    ctx.lineWidth = 1; ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(xOf(0), pT); ctx.lineTo(xOf(0), pT + ph); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = muted; ctx.font = font; ctx.textAlign = "center";
    ctx.fillText("nu", xOf(0), pT + ph + fz + 2);

    // Left axis – price (öre)
    ctx.fillStyle = muted; ctx.font = font; ctx.textAlign = "right";
    [0, 40, 80, 120, 160].forEach(v => ctx.fillText(v + "¢", pL - 4, yP(v) + fz * 0.4));

    // Right axis – battery kWh (dynamic ticks)
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
