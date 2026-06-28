# Smart Energy Manager – HACS Integration

En HACS-integration för Home Assistant som optimerar egenförbrukning av solenergi med batteri, billaddare och elpanna/varmvattenberedare.

## Systemöversikt

```
Elnät (3-fas, max 20A/fas)
    │
    │   Batteri-inverter (3-fas)      Sol-inverter (3-fas)
    │          │                             │
    └──────────┴─────────────────────────────┴──── Elmätare 4 (huslast) ────┬──── Övriga laster
                                                                             │
                                                                        Elmätare 5
                                                                             │
                                                                          Elpanna
                                                                        (1-fas kompressor
                                                                       +2-fas elpatron)
               │
           Billaddare
        (3-fas hårdvara,
         varje bil laddar
         1-fas eller 3-fas)
```

### Elmätarroller

| Mätare | Placering | Tecken | Enhet |
|---|---|---|---|
| Elmätare 1 | Nätanslutning | Negativ = export, Positiv = import | kW |
| Elmätare 2 | Batteri-inverter AC-sida | Negativ = förbrukning | W |
| Elmätare 3 | Sol-inverter | Positiv = produktion | W |
| Elmätare 4 | Fastighetslast (exkl. sol, batteri och billaddare) | Positiv = förbrukning | W |
| Elmätare 5 | Elpanna | Positiv = förbrukning | W |

> **OBS:** Elm1 rapporterar i **kW** – välj enheten `kW` i konfigurationen. Billaddare (intern mätare) rapporterar också i **kW**.

## Funktioner

### Autoläge (Självkonsumtion)
1. **Täck hushållslast** – prioritet 1
2. **Ladda bilar från sol** – när solöverskott ≥ 1 400 W (1-fas) eller 4 140 W (3-fas) startas laddning; ström justeras dynamiskt per bil
3. **Ladda batteri från solöverskott**
4. **Extra varmvatten via elpatron** – när batteriet är fullt och sol finns kvar
5. **Ladda ur batteriet** – när priserna är tillräckligt höga och ingen sol finns
6. **Negativa elpriser** – när säljpriset är negativt absorberas all möjlig solel i batteri, bilar och varmvatten

### Vinterläge
- Ladda batteri nattetid när priset underskrider konfigurerbar gräns
- Ladda ur batteri under dyra timmar (kvällspeak)
- Ladda alltid från sol när möjligt

### Forcelägen
- **Force EV Charge** – ladda alla bilar från elnät oavsett sol (max ström, men fasbegränsad)
- **Force Battery Charge** – ladda batteri från elnät

### Fasskydd
Beräknar fasbelastning per fas och reducerar i prioritetsordning:
1. Minskar billaddarens ström (bil med lägst prioritet först)
2. Minskar batteriladdning
3. Stänger av extra varmvatten (elpatron)

## Elpanna – fasmodell

Elpannan har två separata kretsar med olika fasbelastning:

| Krets | Drift | Faser | Typisk effekt |
|---|---|---|---|
| Värmepump (kompressor) | Normal husvärme | **1-fas** (konfigurerbar, standard L3) | 500–1 500 W |
| Elpatron | Extra varmvatten | **2-fas** (de två övriga faserna, standard L1+L2) | 3 000–6 000 W |

Faserna för elpatronen beräknas automatiskt som de två faser som *inte* används av kompressorn. Väljs kompressorn till L3 → patron på L1+L2.


## Legionella-desinficering

Elpatronen (2-fas) körs automatiskt ca 1 gång/vecka för att värma varmvattnet till ≥ 60°C och eliminera legionellabakterier.

### Prioritetsordning

| Prioritet | Villkor | Beskrivning |
|---|---|---|
| 1 | Solöverskott ≥ 3 000 W inom önskat tidsfönster | Gratis solel driver patronen |
| 2 | Elpris ≤ konfigurerat maxpris inom önskat tidsfönster | Körs på billig nätström |
| 3 | Intervallet överskridits med 50 % (nödkörning) | Kör oavsett pris, undviker natten 23–06 |

### Inställningar

| Inställning | Standard | Beskrivning |
|---|---|---|
| Aktiverad | Ja | Slå av/på funktionen |
| Intervall | 7 dagar | Hur ofta desinficering ska ske |
| Önskat tidsfönster | 10–15 | Timmar då sol normalt är tillgänglig |
| Max pris | 1,50 SEK/kWh | Kör ej på nätström om dyrare |
| Körtid | 60 min | Hur länge elpatronen ska vara aktiv |

### Sensorer

| Entitet | Beskrivning |
|---|---|
| `sensor.sem_legionella_active` | `on` när desinficering pågår |
| `sensor.sem_legionella_days_since` | Dagar sedan senaste körning |
| `sensor.sem_legionella_next_due` | Beräknat datum för nästa körning |

## Multipla bilar

Varje bil konfigureras oberoende med:
- Namn
- Strömbrytare och strömsättningsentitet (laddare)
- Effektsensor (valfritt)
- SOC-sensor och SOC-mål (valfritt)
- Antal faser: **1-fas** (ange vilken fas: L1/L2/L3) eller **3-fas**

Bilar laddas i prioritetsordning (konfigurerad ordning). Fasgränserna kontrolleras gemensamt för alla bilar.

## Prissättning

| | Formel |
|---|---|
| **Köppris** | `(spotpris + nätavgifter + energiskatt) × (1 + moms)` |
| **Säljpris** | `spotpris + extraintäkt (elcertifikat etc.)` |

## Enheter som exponeras

### Sensorer

| Entitet | Beskrivning |
|---|---|
| `sensor.sem_buy_price` | Aktuellt köppris SEK/kWh |
| `sensor.sem_sell_price` | Aktuellt säljpris SEK/kWh |
| `sensor.sem_spot_price` | Nordpool spotpris |
| `sensor.sem_battery_charge_power` | Batteri laddnings-setpoint (W) |
| `sensor.sem_battery_discharge_power` | Batteri urladdnings-setpoint (W) |
| `sensor.sem_ev_car_N_current` | Bil N laddströms-setpoint (A) |
| `sensor.sem_ev_car_N_enabled` | Bil N laddning aktiv (on/off) |
| `sensor.sem_phase_l1_load` | Beräknad fasbelastning L1 (W) |
| `sensor.sem_phase_l2_load` | Beräknad fasbelastning L2 (W) |
| `sensor.sem_phase_l3_load` | Beräknad fasbelastning L3 (W) |
| `sensor.sem_house_load` | Huslast W – Elm4 direkt eller beräknad |
| `sensor.sem_solar_surplus` | Solöverskott (W) |
| `sensor.sem_legionella_active` | `on` när desinficering pågår |
| `sensor.sem_legionella_days_since` | Dagar sedan senaste körning |
| `sensor.sem_legionella_next_due` | Datum för nästa planerad körning |
| `sensor.sem_decision_reason` | Textförklaring senaste beslut |
| `sensor.sem_operating_mode` | Aktivt driftläge |

> `sem_phase_lX_load` är en **prognos**, inte en mätning – den speglar beräknad fasbelastning *efter* att styrningsbesluten verkställts. Används internt för fasskydd.

### Switches

| Entitet | Funktion |
|---|---|
| `switch.sem_force_ev_charge_from_grid` | Forcera billaddning från nät |
| `switch.sem_winter_mode` | Aktivera vinterläge |
| `switch.sem_force_charge_battery_from_grid` | Forcera batteriladdning |

### Select

| Entitet | Funktion |
|---|---|
| `select.sem_operating_mode` | Välj driftläge: `auto` / `winter` / `force_charge_ev` / `force_charge_battery` / `manual` |

### Number (justerbart i realtid)

| Entitet | Funktion |
|---|---|
| `number.sem_battery_min_soc` | Batteri min SOC % |
| `number.sem_battery_max_soc` | Batteri max SOC % |
| `number.sem_ev_soc_target` | Bil laddningsmål % (global standard) |
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
- **solcast_solar** – solprognos (valfritt men rekommenderat)

### Konfigurationsflöde

Konfigurationen sker i fem steg:

**Steg 1 – Nät & Prissättning**
- Nordpool-sensor (obligatorisk)
- Nätmätare per fas (Elm1 L1/L2/L3)
- Strömgivare per fas (för fasskydd)
- Max ström per fas (standard 20 A)
- Nätspänning (standard 230 V)
- Nätavgifter, energiskatt, moms, försäljningsersättning
- **Huslaststyrare** – peka på Elm4 för direkt huslastmätning (rekommenderas)
- **Nätmätare enhet** – välj `kW` om Elm1 rapporterar i kilowatt
- **EV-laddare effektenhet** – välj `kW` om laddaren rapporterar i kilowatt

**Steg 2 – Solceller**
- Sol-inverter total och per fas (Elm3 / intern invertergivare)
- Solcast-prognoser idag/imorgon

**Steg 3 – Batteri**
- SOC-sensor, effektgivare, laddnings- och urladdningsentiteter
- Kapacitet (kWh) och max effekt (kW)
- Min/max SOC-gränser

**Steg 4 – Elpanna**
- Effektgivare (Elm5)
- Strömbrytare för extra varmvatten (elpatron)
- Kompressorns fas (1-fas, standard L3) – patronfaserna beräknas automatiskt
- Elpatronens märkeffekt (kW)

**Steg 5 – Legionella-desinficering**
- Aktivera/avaktivera funktionen
- Intervall i dagar (standard 7)
- Önskat tidsfönster för körning (standard 10–15, sol-timmar)
- Max elpris för körning på nätström (standard 1,50 SEK/kWh)
- Körtid i minuter (standard 60)

**Steg 6 – Elbilar**
- Lägg till en eller flera bilar
- Varje bil: namn, laddare-switch, strömsättningsentitet, SOC-sensor, SOC-mål, antal faser och fas (vid 1-fas)
- Repetera för varje bil, välj "Klar" när alla bilar är konfigurerade

### Enhetsanmärkning för din anläggning

| Sensor | Enhet i HA | Inställning |
|---|---|---|
| Elm1 (nätmätare) | kW | Nätmätare enhet → **kW** |
| Elm3 / SolInv_prod | W | (standard W) |
| BatInv_in_out | W, pos=laddning, neg=urladdning | (standard W) |
| Elm4 (huslast) | W | Huslaststyrare → Elm4 |
| Elm5 (elpanna) | W | Elpanna effektgivare → Elm5 |
| Bil_ladd (intern) | kW | EV-laddare effektenhet → **kW** |

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
      - entity: sensor.sem_house_load
        name: Huslast W
  - type: gauge
    entity: sensor.sem_battery_charge_power
    name: Batteriladdning W
    max: 5000
  - type: entities
    title: Elbilar
    entities:
      - entity: sensor.sem_ev_car_0_current
        name: Bil 1 – laddström A
      - entity: sensor.sem_ev_car_0_enabled
        name: Bil 1 – laddning aktiv
      - entity: sensor.sem_ev_car_1_current
        name: Bil 2 – laddström A
      - entity: sensor.sem_ev_car_1_enabled
        name: Bil 2 – laddning aktiv
  - type: entity
    entity: sensor.sem_decision_reason
    name: Senaste beslut
```
