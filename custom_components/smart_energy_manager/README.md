# Smart Energy Manager – HACS Integration

En HACS-integration för Home Assistant som optimerar egenförbrukning av solenergi med batteri, billaddare och elpanna/varmvattenberedare.

## Systemöversikt

```
Solceller (3-fas, lika per fas)
    │
    ▼
Sol-inverter ──────────────────────────────────────────┐
                                                        │
Batteri-inverter (3-fas, styrbar laddning/urladdning)  │   Elnät (3-fas, max 20A/fas)
    │                                                   │       │
    └───────────────────────────────────────────────────┴───────┤
                                                                │
                                              ┌─────────────────┤
                                              │                 │
                                         Billaddare        Elpanna
                                      (3-fas HW, bil         (1-fas)
                                       laddar 1-fas)
```

## Funktioner

### Autoläge (Självkonsumtion)
1. **Täck hushållslast** – prioritet 1
2. **Ladda bil från sol** – när solöverskott ≥ 1400W startas laddning, ström justeras dynamiskt
3. **Ladda batteri från solöverskott**
4. **Extra varmvatten** – när batteriet är fullt och sol finns kvar
5. **Ladda ur batteriet** – när priser är tillräckligt höga och ingen sol finns
6. **Negativa elpriser** – när säljpriset är negativt absorberas all möjlig solel i batteri, bil och varmvatten

### Vinterläge
- Ladda batteri nattetid när priset underskrider konfigurerbar gräns
- Ladda ur batteri under dyra timmar (kvällspeak)
- Ladda alltid från sol när möjligt

### Forcelägen
- **Force EV Charge** – ladda bil från elnät oavsett sol (max ström, men fasbegränsad)
- **Force Battery Charge** – ladda batteri från elnät

### Fasskydd
Beräknar fasbelastning per fas och reducerar i prioritetsordning:
1. Minskar billaddarens ström
2. Minskar batteriladdning
3. Stänger av billaddaren

## Prissättning

| | Formel |
|---|---|
| **Köppris** | `(spotpris + nätavgifter + energiskatt) × (1 + moms)` |
| **Säljpris** | `spotpris + extraintäkt (elcertifikat etc.)` |

## Enheter som exponeras

### Sensorer
| Enhet | Beskrivning |
|---|---|
| `sensor.sem_buy_price` | Aktuellt köppris SEK/kWh |
| `sensor.sem_sell_price` | Aktuellt säljpris SEK/kWh |
| `sensor.sem_spot_price` | Nordpool spotpris |
| `sensor.sem_battery_charge_power` | Batteri laddnings-setpoint (W) |
| `sensor.sem_battery_discharge_power` | Batteri urladdnings-setpoint (W) |
| `sensor.sem_ev_current_setpoint` | Billaddare strömsetpoint (A) |
| `sensor.sem_phase_l1_load` | Beräknad fasbelastning L1 (W) |
| `sensor.sem_phase_l2_load` | Beräknad fasbelastning L2 (W) |
| `sensor.sem_phase_l3_load` | Beräknad fasbelastning L3 (W) |
| `sensor.sem_solar_surplus` | Solöverskott (W) |
| `sensor.sem_decision_reason` | Text förklaring senaste beslut |
| `sensor.sem_operating_mode` | Aktivt driftläge |

### Switches
| Enhet | Funktion |
|---|---|
| `switch.sem_force_ev_charge_from_grid` | Forcera billaddning från nät |
| `switch.sem_winter_mode` | Aktivera vinterläge |
| `switch.sem_force_charge_battery_from_grid` | Forcera batteriladdning |

### Select
| Enhet | Funktion |
|---|---|
| `select.sem_operating_mode` | Välj driftläge: auto / winter / force_charge_ev / force_charge_battery / manual |

### Number (justerbart i realtid)
| Enhet | Funktion |
|---|---|
| `number.sem_battery_min_soc` | Batteri min SOC % |
| `number.sem_battery_max_soc` | Batteri max SOC % |
| `number.sem_ev_soc_target` | Bil laddningsmål % |
| `number.sem_winter_cheap_threshold` | Prisgräns billigt (SEK/kWh) |
| `number.sem_winter_expensive_threshold` | Prisgräns dyrt (SEK/kWh) |
| `number.sem_winter_min_soc` | Vinter min SOC % |
| `number.sem_winter_max_soc` | Vinter max SOC % |

## Installation via HACS

1. Gå till HACS → Integrationer → ⋮ → Custom repositories
2. Lägg till din GitHub-URL, kategori: Integration
3. Installera "Smart Energy Manager"
4. Starta om Home Assistant
5. Inställningar → Integrationer → Lägg till → Smart Energy Manager

## Konfiguration

### Beroenden
Dessa HACS-integrationer måste vara installerade och konfigurerade:
- **nordpool** – elprissensor
- **solcast_solar** – solprognos

### Steg 1 – Entiteter
Mappa alla enheter från din anläggning till integration-entiteter.

**Batteri-inverter:** Integrationen skriver till `number`-entiteter för att styra laddnings- och urladdningseffekt. Använd den entitet som din inverter exponerar (t.ex. Sungrow, Goodwe, Victron, Fronius etc.)

**Billaddare:** Exponera en `switch` (aktivera/avaktivera) och en `number` (strömnivå i A). Stöds av t.ex. Easee, Zaptec, Wallbox via deras HACS-integrationer.

### Steg 2 – Nät & Priser
Ange nätavgifter, energiskatt och momsats för korrekt prisberäkning.

## Loggning

För debug-loggning, lägg till i `configuration.yaml`:
```yaml
logger:
  logs:
    custom_components.smart_energy_manager: debug
```

## Exempel: Automation för vinterläge

```yaml
automation:
  - alias: "Winter mode October–March"
    trigger:
      - platform: time
        at: "00:01:00"
    condition:
      - condition: template
        value_template: "{{ now().month in [10,11,12,1,2,3] }}"
    action:
      - service: select.select_option
        target:
          entity_id: select.sem_operating_mode
        data:
          option: winter
```

## Exempel: Dashboard (Lovelace)

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: select.sem_operating_mode
    name: Driftläge
  - type: glance
    entities:
      - entity: sensor.sem_buy_price
        name: Köp SEK/kWh
      - entity: sensor.sem_sell_price
        name: Sälj SEK/kWh
      - entity: sensor.sem_solar_surplus
        name: Solöverskott W
  - type: gauge
    entity: sensor.sem_battery_charge_power
    name: Batteriladdning W
    max: 5000
  - type: entity
    entity: sensor.sem_decision_reason
    name: Senaste beslut
```
