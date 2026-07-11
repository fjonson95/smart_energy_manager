/**
 * SEM Charger Card – custom Lovelace card for Smart Energy Manager
 *
 * Configuration example:
 *
 *   type: custom:sem-charger-card
 *   name: Garage
 *   connected_sensor: sensor.sem_charger_garage_connected      # "connected" / other = disconnected
 *   active_car_sensor: select.sem_charger_garage_active_car    # "unknown" = no car selected
 *   power_sensor: sensor.sem_charger_garage_power              # W or kW
 *   power_unit: W        # W (default) or kW
 */

class SemChargerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._boundSelectCar = this._onSelectCar.bind(this);
    this._pickerOpen = false;
  }

  setConfig(config) {
    if (!config.connected_sensor || !config.active_car_sensor || !config.power_sensor) {
      throw new Error(
        "sem-charger-card kräver: connected_sensor, active_car_sensor, power_sensor"
      );
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          padding: 14px 16px;
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .side {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 5px;
          min-width: 52px;
        }
        .side-icon {
          --mdc-icon-size: 22px;
          color: var(--disabled-text-color);
          transition: color 0.3s;
        }
        .side-icon.accent  { color: var(--primary-color); }
        .side-icon.success { color: var(--success-color, #4caf50); }
        .dot {
          width: 10px; height: 10px;
          border-radius: 50%;
          background: var(--disabled-text-color);
          transition: background 0.3s;
        }
        .dot.accent  { background: var(--primary-color); }
        .dot.success { background: var(--success-color, #4caf50); }
        .side-label {
          font-size: 11px;
          color: var(--secondary-text-color);
          text-align: center;
          max-width: 56px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .side-label.accent { color: var(--primary-color); font-weight: 500; }
        .center {
          flex: 1;
          text-align: center;
          min-width: 0;
        }
        .card-name {
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-bottom: 4px;
        }
        .power-val {
          font-size: 30px;
          font-weight: 500;
          color: var(--primary-text-color);
          line-height: 1.1;
        }
        .power-val.charging { color: var(--success-color, #4caf50); }
        .power-unit {
          font-size: 13px;
          color: var(--secondary-text-color);
        }
        .power-idle {
          font-size: 20px;
          color: var(--disabled-text-color);
        }
        .car-select-wrap {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
        }
        .car-select-hint {
          font-size: 11px;
          color: var(--secondary-text-color);
        }
        select.car-picker {
          width: 100%;
          max-width: 160px;
          padding: 6px 8px;
          border-radius: 6px;
          border: 1px solid var(--divider-color, #e0e0e0);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          font-size: 13px;
          cursor: pointer;
          appearance: auto;
        }
        select.car-picker:focus {
          outline: 2px solid var(--primary-color);
          outline-offset: 1px;
        }
      </style>

      <ha-card>
        <div class="side">
          <ha-icon class="side-icon" id="plug-icon" icon="mdi:ev-plug-type2"></ha-icon>
          <div class="dot" id="conn-dot"></div>
          <span class="side-label" id="conn-label">–</span>
        </div>

        <div class="center">
          <div class="card-name" id="card-name"></div>
          <div id="power-area"><div class="power-idle">–</div></div>
        </div>

        <div class="side">
          <ha-icon class="side-icon" id="car-icon" icon="mdi:car-electric"></ha-icon>
          <span class="side-label" id="car-label">–</span>
        </div>
      </ha-card>
    `;
  }

  _update() {
    if (!this._hass || !this._config) return;

    const h   = this._hass;
    const cfg = this._config;

    const connState  = h.states[cfg.connected_sensor];
    const carState   = h.states[cfg.active_car_sensor];
    const powerState = h.states[cfg.power_sensor];

    const CONNECTED_STATES = new Set([
      "connected", "charging", "plugged_in", "pluggedin",
      "waiting", "ready", "preparing", "suspended_ev", "suspended_evse", "ev connected",
    ]);
    const isConnected = connState
      ? CONNECTED_STATES.has((connState.state || "").toLowerCase())
      : false;

    const activeCar = carState ? carState.state : "unknown";
    const carSelected = activeCar && activeCar.toLowerCase() !== "unknown";

    const carOptions = (carState && carState.attributes && carState.attributes.options)
      ? carState.attributes.options.filter(o => o.toLowerCase() !== "unknown")
      : [];

    let powerW = 0;
    if (powerState && powerState.state !== "unavailable") {
      const raw = parseFloat(powerState.state) || 0;
      powerW = (cfg.power_unit || "W").toUpperCase() === "KW" ? raw * 1000 : raw;
    }
    const isCharging = isConnected && carSelected && powerW > 100;

    const plugIcon  = this.shadowRoot.getElementById("plug-icon");
    const connDot   = this.shadowRoot.getElementById("conn-dot");
    const connLbl   = this.shadowRoot.getElementById("conn-label");
    const powerArea = this.shadowRoot.getElementById("power-area");
    const carIcon   = this.shadowRoot.getElementById("car-icon");
    const carLbl    = this.shadowRoot.getElementById("car-label");
    const cardName  = this.shadowRoot.getElementById("card-name");

    cardName.textContent = cfg.name || "";

    // ── Vänster: anslutningsindikator ────────────────────────────────
    if (isCharging) {
      plugIcon.className = "side-icon success";
      connDot.className  = "dot success";
      connLbl.textContent = "Laddar";
      connLbl.className   = "side-label";
    } else if (isConnected) {
      plugIcon.className = "side-icon accent";
      connDot.className  = "dot accent";
      connLbl.textContent = "Ansluten";
      connLbl.className   = "side-label accent";
    } else {
      plugIcon.className = "side-icon";
      connDot.className  = "dot";
      connLbl.textContent = "Frånkopplad";
      connLbl.className   = "side-label";
    }

    // ── Mitten: laddeffekt eller bilval ──────────────────────────────
    if (isCharging) {
      const kw = (powerW / 1000).toFixed(1);
      powerArea.innerHTML = `<div class="power-val charging">${kw}</div><div class="power-unit">kW</div>`;
    } else if (isConnected && !carSelected && carOptions.length > 0) {
      // Ansluten men ingen bil vald → visa dropdown
      // Om dropdown redan finns och är öppen: rör den inte (annars kollapsar den vid varje HA-uppdatering)
      const existingPicker = this.shadowRoot.getElementById("car-picker");
      if (!existingPicker) {
        const opts = carOptions.map(
          o => `<option value="${o}">${o}</option>`
        ).join("");
        powerArea.innerHTML = `
          <div class="car-select-wrap">
            <span class="car-select-hint">Välj bil</span>
            <select class="car-picker" id="car-picker">
              <option value="" disabled selected>– välj –</option>
              ${opts}
            </select>
          </div>`;
        const picker = this.shadowRoot.getElementById("car-picker");
        picker.addEventListener("change", this._boundSelectCar);
        picker.addEventListener("focus",  () => { this._pickerOpen = true; });
        picker.addEventListener("blur",   () => { this._pickerOpen = false; });
      }
    } else {
      powerArea.innerHTML = `<div class="power-idle">–</div>`;
    }

    // ── Höger: aktiv bil ─────────────────────────────────────────────
    if (carSelected) {
      carIcon.className = "side-icon accent";
      carLbl.className  = "side-label accent";
      carLbl.textContent = activeCar;
    } else {
      carIcon.className = "side-icon";
      carLbl.className  = "side-label";
      carLbl.textContent = "Ingen bil";
    }
  }

  _onSelectCar(e) {
    const car = e.target.value;
    if (!car || !this._hass || !this._config) return;
    this._hass.callService("select", "select_option", {
      entity_id: this._config.active_car_sensor,
      option: car,
    });
  }

  getCardSize() { return 1; }

  static getStubConfig() {
    return {
      name: "Laddare",
      connected_sensor: "",
      active_car_sensor: "",
      power_sensor: "",
      power_unit: "W",
    };
  }
}

customElements.define("sem-charger-card", SemChargerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "sem-charger-card",
  name: "SEM Charger Card",
  description: "Visar anslutning, laddeffekt och aktiv bil för Smart Energy Manager.",
  preview: true,
});
