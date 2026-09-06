# Smart Energy Manager – HACS Integration

![Version](https://img.shields.io/badge/version-0.7.3-blue)

A HACS integration for Home Assistant that optimizes self-consumption of solar energy with battery, EV charger, and electric boiler/water heater.

Läs detta på svenska: [README.sv.md](https://github.com/fjonson95/smart_energy_manager/blob/main/README.sv.md)

## What's New in 0.7.3

Extends P7-1 with the grid-phase and battery-inout history P7-2's model-fidelity replay needs, since the original P7-1 extraction only pulled house-load/solar/outdoor-temp/battery-SOC/price.

- **Extracted `sensor.elmatare_active_power_l1/l2/l3` and `sensor.sonnenbatterie_271100_state_battery_inout`** into `testdata/history/grid_l1_hourly.csv`, `grid_l2_hourly.csv`, `grid_l3_hourly.csv`, and `battery_inout_hourly.csv`, same window and format as the rest of `testdata/history/` (see `Series info.txt`). `_HISTORY_DIR_SERIES` and `has_actual_power_data` in `testdata/backtest.py` pick these up automatically when present — no other loader changes needed since `build_state()` already had `SENSOR_MAP` entries for both.
- **The P7-2 model-fidelity replay actually runs now**: over the 10-day `testdata/history/` window it reproduces measured grid import within -17.7% and export within +17.6%, and the simulated end-of-period SOC within 2.3 percentage points of the real value — a real answer to the plan's acceptance criterion, not just an "unavailable" placeholder.
- **Fixed a leak in the forward-simulation pass discovered while validating this**: `sim_state.battery_power_w` was being copied from the real historical reading via `dataclasses.replace(state, ...)`, but `EnergyController._apply_phase_limits()` reads that field as "current battery power" to compute a delta against the new decision. Once the simulated SOC trajectory diverges from history, that real reading is the wrong baseline — it now carries the *previous simulated slot's own* battery power instead, initialized from the real reading only for the very first slot.

## What's New in 0.7.2

Stage 7 of the development roadmap, complete — both P7-1 (real historical extraction, done earlier but never given its own changelog entry) and P7-2 (the forward-simulating battery model).

- **P7-1 — real history instead of hand-curated samples**: `testdata/backtest.py` can now load `testdata/history/`, a directory of CSVs pulled from Home Assistant's statistics API (hourly solar/house-load/outdoor-temp/battery-SOC, quarter-hour price — matching production's real slot length, not an assumed hour). Covers the ~10-day window where all five quantities overlap (the solar sensor is new since 2026-08-06, and raw quarter-hour price history is only retained ~10 days by the recorder) — see `testdata/history/Series info.txt` for exact ranges and how to regenerate/extend it.
- **P7-2 — the simulator runs three passes per slot now, not one**: alongside the existing "shadow" pass (what SEM would decide, given the *real* historical SOC), the backtest now also runs (a) a **forward simulation** that feeds SEM's decisions back into its own running SOC via a simplified battery model (`_simulate_battery_soc`, round-trip efficiency split evenly across the charge/discharge legs) instead of reading next hour's SOC from the CSV, deriving grid import/export/cost from the energy balance since the real historical grid reading no longer applies once the SOC trajectory diverges from history; (b) a **no-battery/no-control reference** (grid = house load − solar only) to measure savings against, per the plan's acceptance criterion; and (c) a **model-fidelity replay** that feeds the *historically measured* battery power (not SEM's decisions) through the same energy-balance formula and compares the result against the real measured grid import/export — this is what actually tests whether the physics are trustworthy, since comparing SEM's new (different, hopefully better) decisions against history's old ones will always show a large gap by design.
- **Honest result on the bundled `testdata/timdata/*.csv` samples**: the model-fidelity replay shows large errors on them, but that's a data-quality finding, not a simulator bug — those files were already documented as "small, sparse, manually-curated" (mostly one-hour gaps punctuated by a single multi-week gap), which the forward/replay integration can't sensibly bridge. Against the continuous `testdata/history/` data, the forward simulation reproduces the real end-of-period battery SOC to within 1 percentage point over a 10-day run — a sound sanity check, even though that source still can't run the fidelity replay itself (no historical grid-phase data extracted for it yet, see P7-1 above).

## What's New in 0.7.1

Stage 7 of the development roadmap, partial (P7-2 only; P7-1 — extracting continuous history via HA's statistics API — is not implemented).

- **Extracted the plan-executor into a reusable `EnergyController.apply_plan_executor()` method** — it previously existed only inline inside `coordinator.py`'s async update loop, which meant nothing outside a live Home Assistant instance could exercise it. `coordinator.py` now just calls this method; behavior is unchanged, it's the same code moved to where both the live coordinator and offline tooling can call it.
- **Fixed `testdata/backtest.py`, which had been silently broken since the Stage 2 refactor** — it called `EnergyController(export_sell_percentile=..., export_min_solar_tomorrow_kwh=...)`, both parameters removed months ago when export logic moved to `EnergyPlanner`; it crashed immediately. It also predated every fix in Stages 1–6: stale sensor entity IDs (`sungrow_sg12rt_active_generation`, `solcast_pv_forecast_forecast_today`), the pre-P1-4 config values (33 kWh battery, 0.07 sell markup, 20A phase limit), and the literal `"unknown"` sentinel for no-car-selected. All corrected, and the backtest now builds a real `DayPlan` via `EnergyPlanner` and runs it through the same `apply_plan_executor()` the live system uses, instead of only exercising the (largely superseded) `EnergyController.compute()` self-consumption path.
- **Solar per price-slot is approximated from actual historical production** (no historical Solcast p10/p50 archive exists to replay) — a p50 proxy from the real reading, with a fixed haircut for p10, just enough to exercise the floor/export logic meaningfully.
- **Honest scope note**: this is a "shadow mode" evaluator — at each historical hour it asks "what would SEM have decided right now", using the *real* historical battery SOC, not a simulated one following SEM's own prior decisions. P7-2's full ambition (a simplified battery model integrating decisions forward into its own SOC trajectory) and P7-1 (pulling continuous history from HA's statistics API instead of the small hand-curated `testdata/timdata/*.csv` samples) are both still open.

## What's New in 0.7.0

Stage 6 of the development roadmap — reliability hygiene. No behavior change to decisions, just to how often and how safely they're written and stored.

- **Deadband on service calls** — the coordinator was calling `switch.turn_on`/`turn_off` and `number.set_value` unconditionally every 30s cycle even when nothing changed (roughly 2900 no-op switch calls/day against the EV charger and the hot-water switch). Writes are now skipped when the target is within 50 W of the entity's own live-read state (exact match for switches) — but never skipped for more than 5 minutes straight, preserving the self-healing heartbeat from the earlier crash-safety work. Comparisons read the entity's *live* HA state rather than a self-tracked cache, so an externally-changed value is still caught and corrected.
- **Recorder attribute size** — `sem_day_plan_reason`'s `slots` attribute carried the full 28h plan horizon, big enough to exceed the recorder's 16 KB attribute limit and silently stop being stored. Capped to the next 24h.
- **Blocking sensor update** — `sem_nordpool_price_schedule` rebuilt its entire `prices_today`/`prices_tomorrow` attribute lists on every single state read (measured at 2.57s), even though the underlying price schedule only changes when Nordpool/Solcast data actually updates. Now cached by schedule object identity and only rebuilt when it changes.
- **Sentinel collision** — the "no car selected" placeholder was the literal string `"unknown"`, which collides with Home Assistant's own reserved state for missing data (`STATE_UNKNOWN`), making the car-selection `select` entities look like they had no value at all rather than an explicit choice. Changed to `"none"`, with a translated display label ("No car selected" / "Ingen bil vald").
- **Timezone correctness** — every `datetime.now()` call (which uses the *container's* system timezone, not Home Assistant's configured one — a real source of subtle bugs when they differ) was replaced with `homeassistant.util.dt.now()`. `energy_controller.py` has no Home Assistant imports by design (it needs to stay usable standalone for the planned backtest simulator), so it now receives "now" from the coordinator via a new `EnergyState.now` field instead, falling back to plain `datetime.now()` only if unset.
- **Legionella never marks a run as started until it's confirmed** — previously, deciding "time to start" immediately set the manager's internal "running" flag, before the `switch.turn_on` call was even attempted. If that call failed (device offline, network hiccup), the manager would believe a run was in progress that never physically happened, and wouldn't retry. It now only ever considers a run "running" once the boiler's switch is *observed* on — a stronger guarantee than a successful API call, and it comes with automatic retry for free: as long as the switch reads off, the same start decision fires again next cycle.
- **Removed the old manual control helpers** the plan flagged (`Charge_uncharge`, `Max Charge`, `Ladda effekt batteri`, `In_Out_Charge`, `Stop urladdning pris negativt`, `VV På`, `VV_fran`, `vv_till`) from Home Assistant. All eight were confirmed inert first: seven were read-only template sensors nothing referenced, and the eighth (`VV På`, a template switch) had `turn_on`/`turn_off` actions containing only a `wait_template` with no actual service call, so toggling it never did anything. Deleted with the user's confirmation.

## What's New in 0.6.7

Stage 5 of the development roadmap, partial — legionella scheduling (the rest of Stage 5 needs a real winter day first; see below).

- **Legionella now targets the actual best slot in its preferred window, not the first one crossing a fixed threshold** – `PriceSchedule` gained `is_best_opportunity_now()`: is the current slot's forecast solar ≥ 6 kW, or is its buy price within the cheapest third of today's slots in the configured window? `LegionellaManager.should_run_now()` uses it when a price schedule is available, falling back to the old absolute thresholds otherwise. The solar threshold moved from 3 kW to 6 kW — the old value was lower than what the electric heating elements themselves draw, so "starting on solar surplus" could mean starting on a surplus smaller than the load about to switch on.

## What's New in 0.6.6

- **Fix: forcing EV charging stopped the battery from capturing solar, exporting it instead** – reported live: with `switch.sem_force_ev_charge` on, `_force_charge_ev()` never touched `decision.battery_charge_power_w`, which defaults to 0 — since the coordinator writes that value to the battery's force-charge number every cycle, this actively commanded the battery to stop charging, so any solar surplus beyond the EV's draw went straight to export. It now charges the battery from whatever surplus remains after the forced EV load.
- **Fix: the mirror-image bug in `_force_charge_battery()`** – forcing the battery to charge similarly left every `ChargerDecision` at its `enable=False` default, blocking EV charging entirely even with ample solar surplus left over after the battery's charge rate. It now allocates remaining surplus to a connected, selected car the same way normal auto mode does.

## What's New in 0.6.5

- **Fix: `sem_decision_reason` silently dropped EV/hot-water reasoning whenever a plan existed** – found while investigating why an EV charging decision wasn't visible in the reason text. The 0.6.1 plan-executor consolidation *replaced* `decision.reason` outright in all its branches instead of extending it, discarding `_auto_mode`'s own reason text (which names which charger got solar current, whether hot water started, etc.) even though the underlying EV/hot-water decisions were still being applied correctly — only the visible explanation was wrong. The executor now prepends its battery-specific reasoning to the original reason instead of overwriting it.

## What's New in 0.6.4

- **Fix: `idle`/`cover_load` plan slots never charged the battery, even with a large real solar surplus** – found live: the plan classified the current slot as `cover_load` (dark/self-consumption) based on its 15-minute-old forecast, but actual solar surplus was over 10 kW. The 0.6.1 plan-executor consolidation only charged the battery for `solar_charge`/`grid_charge` actions, so `cover_load`/`idle` slots sat at 0 W both ways instead of capturing the unplanned surplus — exporting it at a near-zero sell price instead. The executor now also charges from real surplus during `idle`/`cover_load` slots (respecting the same `prefer_sell` check as `solar_charge`), falling back to self-consumption discharge only when there's a real deficit instead of a surplus.

## What's New in 0.6.3

Stage 4 of the development roadmap — the negative-price absorption ladder (partial: steps 1, 2, 4).

- **Fix: negative price at night with no solar was never exploited** – the negative-price block required `solar_w > 0`, so a negative spot price overnight (no sun) did nothing at all, even though charging the battery from the grid at a negative price is pure profit. Removed the solar requirement.
- **Fix: the proactive-headroom block disabled everything for hours, not just battery charging** – when a negative price was expected within 2h, the old code returned early, silently blocking self-consumption discharge, EV charging, and hot water for the entire lead-in window. It now only caps the battery's charge ceiling (to hold headroom) and falls through into the normal decision logic instead of short-circuiting it.
- **New: an explicit, sequential absorption ladder for negative prices** — battery (full power) → hot water → EV, each step activating only once the previous one is saturated, instead of splitting solar surplus proportionally across all of them. `sem_decision_reason` now names the active step (e.g. "Negativt pris steg 1: batteri full effekt").
- **Not yet implemented**: heating setpoint uplift (belongs to Stage 5) and inverter curtailment (Stage 4's last rung). Curtailment specifically requires manually calibrating `number.sg_power_limitation_setting`'s response curve first — the plan is explicit that this can't be tuned blind. When every other absorption step is saturated and the battery still exports at a negative price, the reason string says so plainly instead of pretending it's handled.

## What's New in 0.6.2

Stage 3 of the development roadmap — forecast reserve and snow detector.

- **Plan against pessimistic (p10) solar, not the median** – `PriceSlot` now carries `solar_kwh_p10` alongside the existing median-based `solar_kwh` (`price_scheduler.py`). The export floor calculation and the "is tomorrow's solar strong enough to export tonight" gate (`energy_planner.py`) now read p10 instead of the median — a forecast that's optimistic by chance no longer lets the battery run emptier than it should. Revenue estimates and the general SOC simulation still use the median, since being conservative there would just make the plan pessimistic about earnings, not less safe.
- **New: production ratio sensor detects snow/soiling** (`sem_pv_production_ratio`) – a rolling 3-day ratio of actual solar production (new optional config: `actual_solar_daily_entity`) against that day's Solcast forecast, persisted across restarts. Normal days sit around 0.8–1.3; a ratio below ~0.6 (snow-covered or dirty panels the forecast doesn't know about) automatically scales the export floor up toward full night-autonomy and the export gate tightens, until production recovers. Exposed as its own sensor and as an attribute on `sem_day_plan_reason` so it's visible *why* the system got cautious.
- **New floor formula**: `behov_kwh` (p10-based need until solar takeover) → `reserv_kwh` (scaled up by an uncertainty markup driven by the production ratio) → `golv_kwh = min(battery_max, max(hard_floor, reserv_kwh))`. The hard floor is fixed at 10% of battery capacity and is now always enforced regardless of how small the computed reserve is on a sunny day.

## What's New in 0.6.1

- **Consolidated the three parallel copies of battery decision logic** (`_auto_mode`'s self-consumption blocks, the coordinator's plan-mode override, and a shadow-calculation in `PlanExecutorCharge/DischargeSensor`) into a single plan-executor in `coordinator.py`. It now sets the battery charge/discharge setpoint for **every** plan slot action (`solar_charge`, `grid_charge`, `export`, `idle`/`cover_load`), not just the two it used to override — using real-time solar surplus and house deficit, not the plan's forecast numbers. The executor re-runs phase/export-limit clamping (`_apply_phase_limits`) after setting the new values, closing a gap where a plan-driven override could bypass the phase protection added in 0.6.0.
- `sem_plan_executor_charge`/`sem_plan_executor_discharge` now simply report the coordinator's actual last decision instead of independently recomputing a shadow estimate — they're guaranteed to match `sem_battery_charge_power_setpoint`/`..._discharge_power_setpoint` by construction.
- The plan-vs-actual deviation log now compares against the *final* post-executor decision and no longer needs the "soft match" heuristics that previously masked known planner gaps — a logged deviation is now a real one, with `sem_decision_reason` explaining which real-time limit it hit.

## What's New in 0.6.0

Stage 2 of the development roadmap — a controller with a correct value model.

- **Fix: export never actually triggered** – the day-plan's export filter compared sell price against a "reference buy price" that included energy tax, so the 90% threshold could never be beaten by a sell price (`build_plan()` in `energy_planner.py`). Replaced with a marginal-value model: exportable energy (already isolated as "above the floor" by the existing calculation) is only dispatched to a slot if that slot's sell price exceeds the battery's actual average cost (`sem_battery_average_price`, previously computed but never read) plus a configurable cycle/wear cost. Also wired in `export_min_solar_tomorrow_kwh` as an actually-read gate — previously stored but never consulted, so proactive export could trigger even when tomorrow's forecast was too weak to refill the battery.
- **New config: `eta_roundtrip` (default 0.87) and `cycle_cost_sek_kwh` (default 0.05 SEK/kWh)** in the Battery section — the round-trip efficiency and per-kWh wear cost now feed the export profitability check above.
- **Fix: phase protection was import-only** – `_apply_phase_limits()` in `energy_controller.py` only ever checked `phase_current <= max_current`, so a phase running high on *export* (large battery discharge, e.g. during a plan-driven export slot) was never caught. Now checks both directions; an over-limit export reduces battery discharge power (the only controllable export source) instead of touching EV/heater loads, which can't cause export overcurrent. Added a 2 A margin (`max_current_effective`) to account for the control loop's 30 s cycle plus the inverter's own response lag.
- **New: separate export power cap** (`max_export_w`, default 14000 W, configurable in the Grid section) — the inverter's AC export rating is a different constraint than per-phase current; battery discharge is now also clamped so `solar + discharge` never exceeds it.
- **Removed winter mode entirely** – `_winter_mode()`, the `WinterModeSwitch`, the four `winter_*` number entities, and all associated config were deleted. The day-plan's percentile-based pricing (already season-agnostic, using each day's own price distribution) and the export/grid-charge dispatch already cover what winter mode was for; the `winter` operating mode option is gone from the select entity.

## What's New in 0.5.63

- **Fix: reloading the integration mid-legionella-run could trigger a spurious duplicate start** – `LegionellaManager`'s run-tracking (`running`, `run_started_at`, `temp_confirmed`) only lived in memory. An options-flow save (or any config entry reload) recreated the coordinator and reset this state; if the boiler's switch happened to read `unavailable` for even one cycle right after the reload (a routine ems-esp communication blip), the manager mistook that for "the boiler just turned itself off" and, on the next due-check, started a second run — even though the original was still physically running and never actually finished. Two fixes: (1) run-tracking now persists to the same storage as `last_run`, restored on `async_load()`, so a reload/restart no longer loses an in-progress run or its true start time; (2) the switch is now read as a tri-state (on/off/unavailable) — an `unavailable` reading holds the current run status instead of being treated as "off".

## What's New in 0.5.62

- **Fix: solar energy per price slot was roughly double** – `_match_solar_to_slots()` in `price_scheduler.py` always converted average solar kW to kWh using a hardcoded 0.5 h (assuming a 30-minute slot), but Nordpool price slots are 15 minutes. Now uses the price slot's actual duration.
- **Fix: house-load floor overrode legitimate real-meter readings** – the yesterday's-average floor was applied unconditionally whenever solar was active, even when `house_load_entity` was providing a real, valid reading — clamping away genuine midday dips. The floor now only applies when the entity is missing/unavailable and the code falls back to the grid-balance formula (the actual source of the transition-glitch bug it was meant to guard against). Added a rolling median over the last 3–5 samples of the real sensor to smooth transient meter glitches instead.
- **Removed the 1.5 kW cap on predicted hourly load** in both `energy_controller.py` (evening-fill target calculation) and `energy_planner.py` (day-plan load estimate) — the cap was silently under-predicting winter consumption for an all-electric house. The 0.5 kW floor remains.
- **Fixed two number entities that silently stopped working in v0.5.60**: `ExportSellPercentileNumber` and `ExportMinSellPriceNumber` wrote to `EnergyController` attributes that were removed when export logic moved to `EnergyPlanner`. Repointed to `coordinator._energy_planner`.
- **Removed `EvSocTargetNumber`** (global "EV SOC target" number entity) — it wrote to an `EnergyController.ev_soc_target` attribute that was never read anywhere; each car's own per-car SOC target (set in the charger configuration) is what actually governs charging.
- **All `number.py` entities now inherit `RestoreEntity` and persist changes to `entry.options`** — a value changed via the UI previously lived only in memory and reset to the config-flow default on restart. Now it's written back to the config entry (survives restart/reload) with `RestoreEntity` as a fast-path restore on entity add.

## What's New in 0.5.61

- **Fix: crash in opportunistic grid charging** – `_auto_mode()` referenced `_export_floor_kwh` and `_battery_energy_kwh` without ever defining them in scope, causing a `NameError` whenever opportunistic charging conditions were met (low solar forecast, not in economic peak, no active export). The floor is now derived from the day plan (`state.plan_export_floor_kwh`, set by the coordinator from `DayPlan.export_floor_kwh`) with a fallback to `battery_min_soc` when no plan is available; the battery energy is derived from SOC × capacity. Also removed a duplicate/dead assignment of `_charge_target_kwh`.
- **Safety: battery setpoints always zeroed on unload** – `async_unload_entry` now writes 0 to both the charge and discharge setpoint entities before unloading, so a config reload or HA restart never leaves the battery frozen mid-charge or mid-discharge.
- **Safety: guaranteed zero-first write ordering** – battery setpoint writes now always zero whichever direction (charge/discharge) is inactive *before* writing the active direction's power, using a blocking service call for the zero. This prevents a brief window where the inverter could see both charge and discharge setpoints non-zero during a direction switch.
- **New optional config: battery operating-mode watchdog** – added `battery_operating_mode_entity` (Battery section). When configured, SEM checks this `select` entity every cycle and raises a persistent notification + log warning if it isn't set to `manual`, since in that case all setpoints SEM writes have no effect on the inverter.

## What's New in 0.5.60

- **Refactor: removed duplicate export logic from EnergyController** – the controller previously contained ~230 lines of proactive-export and morning-export logic that duplicated what EnergyPlanner already decides. Since the coordinator's plan-executor already overrides the controller's battery setpoints for `export` and `grid_charge` slots, the controller's own export calculation was never actually applied. Removed it entirely. The controller now reads `state.plan_action` (set by the coordinator from the current plan slot) and uses it only as a guard flag — blocking self-consumption discharge and opportunistic grid charging during an active export slot. Export power targets continue to come exclusively from the planner via the coordinator's plan-executor.

## What's New in 0.5.59

- **Fix: day-plan incorrectly scheduled export at low sell prices** – the planner's "max night-buy" filter compared the sell price against `PriceSchedule.slot.buy_sek`, which is only the Nordpool spot component (~1.7 SEK/kWh) and lacks electricity tax and grid fees. The actual full buy price was ~3.18 SEK/kWh, so the 90 % threshold was 1.53 SEK/kWh — making the check almost always pass and allowing export slots to appear in the plan even when selling at 1.63 SEK/kWh is a losing trade vs future grid purchase at 3.18 SEK/kWh. Fix: the coordinator now passes the actual full buy price (`state.buy_price_sek_kwh`) into `build_plan()`. The filter now uses 3.18 × 0.90 = 2.86 SEK/kWh as the threshold, correctly blocking export slots when sell price is below this level.

## What's New in 0.5.58

- **Improvement: Energy plan card clarity** (`sem-energy-plan-card.js`) – three UI improvements to make the current state unambiguous: (1) The plan-time row now shows the current plan action, e.g. "Plan skapad 19:00 · Nu: Egenförb. · slots: 12", so you can instantly see what the system is doing without inspecting sensors. (2) A colored action label is drawn directly in the canvas at the "nu" marker, making it clear whether the system is exporting, doing self-consumption, charging, etc. (3) Fixed a display bug where the export phase card showed "19:00–19:00" (identical start and end) when only one export slot exists — it now shows just the start time in that edge case.

## What's New in 0.5.57

- **Fix: EV blocked when battery is nearly full and Sonnen throttles** – v0.5.56 pre-empted all solar surplus for the house battery regardless of whether Sonnen was actually absorbing it. When the battery is near full (~94 % SOC), Sonnen enters standby and accepts only a few watts, but the pre-empted power was still withheld from the EV — resulting in 2.7 kW going to the grid with no one charging. Fix: the pre-emption now only runs when the battery is actually absorbing more than 100 W (`state.battery_power_w > 100`). When Sonnen throttles (e.g. 7 W), no surplus is pre-empted and the EV receives the full solar surplus.

## What's New in 0.5.56

- **Fix: house battery priority over EV charging** – the EV surplus loop ran before the battery charging block, so with e.g. 2342 W solar surplus the EV consumed ~2300 W and left only 42 W for the battery (below the 100 W threshold). Result: house battery stayed uncharged while the car charged. Fix: before the EV loop, the battery's share is pre-empted from `remaining_surplus` (up to `battery_max_power_kw`). EV then only gets what remains after the house battery is served. After the EV loop the pre-empted amount is returned to `remaining_surplus` so the battery block picks it up. When solar surplus is large enough for both (e.g. 5 000 W), both charge simultaneously.

## What's New in 0.5.55

- **Fix: solar exported at low price instead of stored in battery** – `prefer_sell` triggered whenever `sell_price ≥ 0.80 SEK/kWh`, which caused the battery charging block to be skipped entirely — even when the sell price was mediocre (e.g. 0.86 SEK/kWh) and the cheapest future recharge price was much higher (e.g. 1.74 SEK/kWh). This is a losing trade: sell now for 0.86, buy back later for 1.74. Fix: `prefer_sell` now additionally requires that the sell price is at least 90 % of `best_charge_slot.buy_sek` — the cheapest upcoming grid-charge price. If selling is cheaper than what you'll pay to refill, store in the battery instead. Example: 0.86 < 1.74 × 0.90 = 1.57 → `prefer_sell = False` → battery charges ✓. High-price day: 3.50 ≥ 1.74 × 0.90 = 1.57 → `prefer_sell = True` → export ✓.

## What's New in 0.5.54

- **Fix: battery charged from grid due to feedback loop in surplus formula** – v0.5.53 introduced `_effective_surplus_w = _raw_export_w + _batt_charge_w`. When solar dropped and the grid switched from export to import, `_raw_export_w` fell to 0 but `_batt_charge_w` still reflected the previous setpoint (e.g. 767 W). Result: the formula kept returning 767 W, the battery continued charging, and the house drew 632 W from the grid to compensate. Fix: the correction is now applied **only when the grid is actually exporting** (`_raw_export_w > 0`). If the grid is importing, the formula is not used and `solar_surplus_w` falls back to the standard `solar − house_load` calculation, which correctly yields 0 W when solar no longer exceeds load.

## What's New in 0.5.53

- **Fix: solar surplus still exported when battery already charging** – v0.5.52 fixed the case where the battery was in standby (0 W) and real grid-export exceeded the calculated surplus. But if the battery was already charging (e.g. 559 W), the grid-export reading reflected only what remained *after* the battery had taken its share (e.g. 312 W), making the supplement helpless: `max(558, 312) = 558 W`. Root cause: the effective surplus formula must account for both: `real_surplus = grid_export_after_charging + battery_charge_power`. In the example: 312 + 559 = 871 W → setpoint raised to 871 W → export drops to near zero. Fix: `_effective_surplus_w = _raw_export_w + _batt_charge_w`.

## What's New in 0.5.52

- **Fix: solar surplus going to grid instead of battery** – when the house-load sensor includes L3 loads powered directly by solar (bypassing the Sonnen's metering), the calculated surplus `solar_w − house_load_w` underestimates what the battery can actually capture. Result: SEM sent a 0 W charge setpoint, Sonnen entered standby, and all real solar surplus (up to 480 W) flowed to the grid. Fix: the surplus calculation is supplemented with the actual net grid-export reading (`−(grid_L1 + grid_L2 + grid_L3) − battery_discharge`). If more electricity is actually leaving the house than the formula predicted, that real-export value is used as the effective surplus — ensuring the battery receives it instead of the grid.

## What's New in 0.5.51

- **Fix: opportunistic grid charging blocked on cloudy days** – the opportunistic charge block previously required `battery_charge_power_w == 0.0`, meaning any active solar-surplus charge (even 50 W trickle on an overcast day) prevented grid top-up entirely. On a day when the solar forecast is below 10 kWh, the battery could stay undercharged even during the cheapest price slots. Fix: the `== 0.0` guard is removed. The block now runs whenever today's **or** tomorrow's solar forecast is below `DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH` (10 kWh), and only overrides `battery_charge_power_w` if the calculated grid-charge rate is **higher** than the current solar-surplus rate (`max()` instead of assignment) — solar surplus always has priority; the grid only tops up to the target if solar alone is insufficient.

## What's New in 0.5.50

- **Fix: plan export pauses at export floor** – the plan executor's `export` case now checks actual battery kWh against `export_floor_kwh` before discharging. If the battery reaches or drops below the floor (+ 0.5 kWh safety margin), export is paused and the reason is logged as `Plan export PAUSAD`. This prevents the battery from being drained below the overnight reserve when the actual SOC differs from the plan's projected SOC at generation time.

## What's New in 0.5.49

- **Plan-mode activated**: DayPlan now drives actual Sonnen setpoints in `auto` mode (previously comparison/logging only). Coordinator overrides `battery_charge_power_w`/`battery_discharge_power_w` based on the current plan slot:
  - `solar_charge` – left to controller (actual solar surplus handled best in real-time)
  - `grid_charge` – charges battery at plan's target power; suppressed if `economic_peak` is active (buy price ≥ 1.2× cheapest future slot) and replaced by self-consumption discharge
  - `export` – discharges plan's price-weighted export volume + house deficit
  - `idle` / `cover_load` – covers house load from battery if SOC > evening target; otherwise 0 W
- **Fix plan executor sensors**: `PlanExecutorChargeSensor` (grid_charge) and `PlanExecutorDischargeSensor` (grid_charge during economic_peak) now return correct values. Sensors mirror exactly what the coordinator actually executes.
- `battery_max_power_kw` added to coordinator data dict so sensors read the configured max instead of the 5 kW default.

## What's New in 0.5.48

- **Fix: opportunistic charging blocked self-consumption discharge during economic peak** – each night when the battery fell below the export floor threshold (>0.5 kWh deficit) and the current hour happened to have a price below the percentile threshold, the opportunistic charging block triggered. It set `battery_charge_power_w > 0`, which caused the self-consumption discharge block to be skipped (the `decision.battery_charge_power_w == 0.0` condition failed). Result: discharge setpoint dropped to 0 for ~30 minutes (until the next hour with a higher price), grid covered the full house load, and the system also ran grid-to-battery charging in parallel. The cycle repeated every time a cheap price slot coincided with battery SOC below the floor. Fix: `_economic_peak` is now computed *before* the opportunistic charging block and added as a `not _economic_peak` guard. During economic peak (buy price ≥ cheapest future charge price × 1.2) it is more economical to discharge now and recharge cheaply later — opportunistic charging is counterproductive and is suppressed.

## What's New in 0.5.47

- **Fix: economic-peak self-consumption threshold lowered from 2.0× to 1.2×** – the v0.5.44 guard (`buy_price ≥ best_charge_price × 2.0`) required a very large spread before the battery would discharge during expensive peak hours. In practice prices like 3.17 SEK/kWh now vs. 2.37 SEK/kWh at 02:45 (ratio 1.34×) were still blocked by both the 2.0× and the intermediate 1.5× threshold. The threshold is set to 1.2× so the battery bypasses the evening-floor whenever buying now costs ≥ 20 % more than the cheapest upcoming charge slot — a spread that is always worth an arbitrage trade (0.80 SEK/kWh net gain at tonight's prices). The decision reason suffix is now `×1.2`.

## What's New in 0.5.45

- **Fix: proactive export blocked when upcoming night electricity is more expensive** – the export logic sold battery energy during the day (e.g. at 1.50–2.10 SEK/kWh) without checking whether upcoming dark hours would require buying electricity back at a higher price (e.g. 3.77 SEK/kWh). This was a losing trade: sell cheap, buy expensive. Fix: a new `_export_price_ok` guard is calculated before the export gate: `sell_price ≥ max(dark-slot buy prices in floor period) × 0.9`. If the current sell price is less than 90 % of the peak upcoming night buy price, export is blocked — the energy is more valuable held for evening self-consumption. The 90 % factor allows export when prices are close (e.g. sell 1.80 vs night 1.90 — slight gain worth taking). Same filter applied to morning export and mirrored in `EnergyPlanner` (high-slots are filtered to only those with sell ≥ 90 % of peak night buy). Tonight's case: sell 2.10 < 3.77 × 0.9 = 3.39 → export blocked ✓.

## What's New in 0.5.44

- **Fix: battery sat idle during expensive evening peak — grid covered house load at 3.77 SEK/kWh** – the `evening_target_soc` guard (SOC floor protecting the night reserve) correctly blocked self-consumption when the battery was below 71 % target. But it made no economic distinction between cheap grid electricity (0.30–0.50 SEK/kWh at night) and expensive peak electricity (3.77 SEK/kWh in the evening). Fix: a new economic-peak condition checks whether the current buy price is ≥ 2× the cheapest upcoming charge price (`ps.best_charge_slot.buy_sek`). When true, the effective SOC floor drops to `battery_min_soc` — the battery discharges to cover house load now, and the opportunity-charge logic buys back cheap electricity later that night. Tonight's case: 3.77 / 1.25 = 3.0× → economic peak active → battery discharges 1 041 W instead of importing. The decision reason shows `| Självkonsumtion XXXW (ekonomisk topp Y.YY>Z.ZZ×2)` when this path is active.

## What's New in 0.5.43

- **Fix: battery sat idle during morning price peak while waiting for solar** – when `wait_for_solar` is active (solar expected within 2 h) the `evening_target_soc` guard was still blocking self-consumption discharge. The battery would sit at ~39 % SOC while the house drew from the grid at morning peak prices (~1.26 SEK/kWh), even though solar would replenish the reserve by noon. Fix: when `wait_for_solar = True` and `solar_next_2h_kwh > 0`, the effective evening-target floor is reduced by 80 % of the expected solar SOC gain (`0.8 × solar_next_2h_kwh / capacity × 100 %`). With 5.7 kWh expected and 33 kWh capacity this shifts the threshold from 39.7 % down to ≈ 22 %, allowing the battery to cover morning load. The 80 % factor provides a safety margin in case clouds reduce actual production. The decision reason now appends `(sol X.X kWh/2h)` when this path is active. The same logic is mirrored in `EnergyPlanner`: dark slots now look ahead 2 h and reduce the floor by 80 % of expected solar, so the plan accurately reflects the controller's morning behaviour.

## What's New in 0.5.42

- **Fix: EnergyPlanner simulation did not decrement battery for self-consumption** – the forward simulation in `energy_planner.py` produced `cover_load` slots (solar < house, daytime) and dark `idle` slots (night) without decrementing `batt_kwh`. As a result the plan overestimated available battery energy throughout the day and night, leading to inflated export capacity predictions and an inaccurate SOC trace. Fix: both cases now simulate battery self-consumption discharge — battery discharges to cover the house deficit (or full house load at night) while `batt_kwh > batt_min_kwh + export_floor_kwh`. The export-floor guard mirrors the controller's `battery_soc > evening_target_soc` check and protects the night-coverage reserve. When the battery is at its floor, the slot is shown as grid-covered with a clear reason string.

## What's New in 0.5.41

- **Fix: battery never discharged for self-consumption — all house load above solar came from grid** – the self-consumption discharge block existed but was gated on `buy_price > 0.20 SEK/kWh`. In summer, when spot prices are low and total buy price can be near this threshold, the condition was never met. Result: the battery sat idle all day and every watt of house load that solar could not cover was imported from the grid. Fix: the price gate is removed. Stored solar is always cheaper than grid import regardless of current spot price — the battery now discharges to cover house deficit whenever `battery_soc > evening_target_soc` and `battery_soc > battery_min_soc`. The evening-target guard already protects the night energy reserve.

## What's New in 0.5.39

- **New: number entities for proactive export thresholds** – two new adjustable parameters are now exposed as `number` entities in Home Assistant: `Proactive Export Price Percentile` (50–100%, step 5, default 75 — export triggers when sell price ≥ this percentile of today's prices) and `Proactive Export Absolute Min Price` (0.00–3.00 SEK/kWh, step 0.05, default 0.70 — always-export floor regardless of percentile). Both update the controller immediately without a restart and survive as long as HA is running (reset to config-flow defaults on restart).

## What's New in 0.5.38

- **Fix: plan executor discharge sensor showed non-zero for idle/cover_load slots below evening floor** – v0.5.36 added the `evening_target` guard only for `export` slots. For `idle` and `cover_load` slots (battery covers house deficit at night), the same guard was missing — the sensor still returned `house_deficit_w` even when `battery_soc ≤ evening_target_soc_pct`. The guard is now applied to all discharge actions: any slot returns 0 W when `battery_soc ≤ evening_target`.

## What's New in 0.5.37

- **Fix: "Bästa urladdningstimmen" discharged below evening floor after sunset** – the house-load discharge path (line `if not export_active … battery_soc > battery_min_soc`) only guarded against absolute min SOC. After sunset, `solar_w` drops below 100 W so `evening_fill = False` (solar check fails) even when `battery_soc < evening_target_soc`. At ~19:35, with battery at ~50% and evening target 53.3%, "Bästa urladdningstimmen" triggered (peak buy-price window) and discharged 1 384 W to cover house load — draining below the night-coverage floor and causing min-SOC depletion overnight. Fix: added `battery_soc > evening_target_soc` to the guard so the battery is protected below the night floor even when `evening_fill` is temporarily False (no solar).

## What's New in 0.5.36

- **Fix: proactive export ignored `battery_min_soc` in energy check, discharging below floor** – the export guard `_battery_energy_kwh > _export_floor_kwh` compared the *total* battery energy (e.g. 51% × 33 kWh = 16.83 kWh) against the floor (10.98 kWh), concluding there was 5.85 kWh to export. But only the energy *above* min SOC is usable: `(51 − 20)% × 33 = 10.23 kWh < 10.98 kWh floor` → nothing to export. The controller discharged at 4.9 kW into the evening peak despite the battery already being below the night-coverage floor. Same bug was present in `EnergyPlanner.exportable_kwh`. Fix: both calculations now use `usable_kwh = (battery_soc − battery_min_soc)% × capacity`. The plan executor discharge sensor also gains an `evening_target` guard: for `export` slots it returns 0 W when `battery_soc ≤ evening_target_soc_pct`.

## What's New in 0.5.35

- **Fix: `evening_target_soc` excluded `battery_min_soc`, causing severe underestimation** – the formula `evening_target_soc = evening_needed_kwh / capacity × 100` treated the battery as if all capacity were usable. But `battery_min_soc = 20%` is an absolute floor — the bottom 6.6 kWh are never accessible. Result: an 11.5 kWh evening need gave a 34.9% target, but at 34.9% only `(34.9 − 20) / 100 × 33 = 4.9 kWh` is actually usable — less than half the required amount. Battery depleted to min SOC every night even from 49–51% starting SOC. Fix: `evening_target_soc = min(battery_max_soc, battery_min_soc + evening_needed_kwh / capacity × 100)`. With the same 11.5 kWh need: `20 + 34.9 = 54.9%`, giving `(54.9 − 20) / 100 × 33 = 11.5 kWh` usable — exactly what's needed. Same fix applied to `EnergyPlanner.build_plan()`.

## What's New in 0.5.34

- **Fix: `evening_target_soc` underestimated on sunny days — battery depleted overnight** – the dynamic evening target was computed as `(_eff_daily_kwh / 24) × hours_dark + 2 kWh`, where `_eff_daily_kwh = max(predicted_daily_kwh, yesterday_consumption_kwh)`. On sunny days `yesterday_consumption_kwh` is grid import only (~7 kWh), giving ~0.29 kW average — even though actual night load is ~0.9–1.2 kW. Result: `evening_target_soc ≈ 17%`, battery above that all day → `evening_fill = False` → controller exported solar instead of charging. Battery then depleted overnight from ~49–51% to min SOC. Fix: `hourly_load_kw` is now clamped to `min(max(daily_avg, house_load_w / 1000, 0.5), 1.5)`, matching the v0.5.27 fix applied to the export floor. With house load ~1.24 kW the evening target becomes ~53%, triggering `evening_fill = True` (49% < 53%) and forcing the controller to store solar before selling.

## What's New in 0.5.33

- **Fix: Plan executor charge sensor used plan's `evening_target_soc_pct` instead of controller's** – the `prefer_sell` check in the charge sensor compared battery SOC against the plan's `evening_target_soc_pct` (computed from `solar_takeover_dt`, which could be set to the current day's solar ~11:15 → giving only ~16.5% target). The controller computes its `evening_target_soc` differently — searching `ps.slots` for the first slot after sunset where solar covers load, falling back to sunrise + 3 h, giving a realistic dark-period floor (~39% at 33% battery). This caused the sensor to show 0 W (prefer_sell=True, evening_fill=False) while the controller was charging at full surplus (evening_fill=True). Fix: `evening_target_soc` is now added to `ControlDecision`, set in `_auto_mode()` after the dynamic calculation, and stored in `coordinator.data` as `evening_target_soc_pct` — replacing the plan value. The charge sensor now mirrors the controller's own `evening_fill` result.

## What's New in 0.5.32

- **Fix: Plan executor charge sensor ignored `prefer_sell` logic** – for `solar_charge` slots the sensor always showed the battery charge estimate, but the controller skips charging and exports solar whenever `sell_price ≥ sell_solar_min_price AND NOT evening_fill`. This caused the charge sensor to show e.g. 4 000 W while the controller was actually exporting at 0 W charge. Fix: `sell_solar_min_price` (from controller config) and `evening_target_soc_pct` (from day plan) are now stored in coordinator data. The sensor computes `prefer_sell = sell_price ≥ sell_solar_min_price AND battery_soc ≥ evening_target_soc` and returns 0 W when true — matching the controller's export decision. Attributes expose `prefer_sell`, `evening_fill`, `sell_price`, and `sell_solar_min_price` for full transparency.

## What's New in 0.5.31

- **Fix: Plan executor charge sensor ignored EV charging priority** – the `Plan executor: charge` sensor showed `min(solar_surplus, battery_max)` as if all solar surplus went to the battery, but in practice the controller allocates surplus to EV chargers first. When an EV was drawing e.g. 5.5 kW from a 7 kW surplus, only 1.5 kW would actually reach the battery. Fix: for `solar_charge` slots the sensor now subtracts `ev_total_power_w` from solar surplus before calculating battery charge (`max(0, surplus − ev_total) → battery`). `ev_total_power_w` (sum of all charger `power_w` readings) is added to coordinator data and exposed as an attribute on the charge sensor alongside `battery_surplus_w`.

## What's New in 0.5.30

- **Fix: Plan executor discharge sensor showed non-zero at min SOC** – when battery reached minimum SOC (20%), the controller correctly set discharge to 0 W, but the plan executor discharge sensor still reported the house deficit (e.g. 774 W) because it lacked a SOC guard. Fix: `battery_soc_pct` and `battery_min_soc` are now stored in coordinator data; the sensor returns 0 W whenever `battery_soc_pct ≤ battery_min_soc`, matching the controller's hard floor.

## What's New in 0.5.29

- **Fix: Plan executor discharge showed only net export, not total battery discharge** – for `export` slots the sensor showed only the price-weighted dispatch component (e.g. 830 W to grid) but not the house-coverage component (e.g. 524 W). The actual controller discharge was their sum (1 354 W). Fix: the sensor now returns `min(net_export_w + house_deficit_w, battery_max_w)` for export slots, where `house_deficit_w = max(0, house_load_w − solar_power_w)`. A new `solar_power_w` key is added to coordinator data (used by the sensor). Attributes now expose `net_export_w`, `house_deficit_w`, `house_load_w`, and `solar_power_w` separately for full transparency.

## What's New in 0.5.28

- **Fix: Plan executor discharge sensor showed 0 W for idle slots at night** – the `Plan executor: discharge` sensor only returned a value for `export` action slots. During `idle` slots at night (no planned export, but battery still covers house load), the sensor read 0 W even though the controller was discharging 1200–1500 W to power the house. Fix: for `idle` and `cover_load` slots, the sensor now returns `min(house_load_w − solar_surplus_w, battery_max_w)` — the same house-deficit logic the controller uses. `solar_surplus_w` and `house_load_w` are added as sensor attributes. `export` slot behavior is unchanged.

## What's New in 0.5.27

- **Fix: export floor underestimated on sunny days, causing overnight battery drain** – on summer days with high solar production, `yesterday_consumption_kwh` only captures grid import (solar covered the rest), giving an average of ~320 W instead of the actual ~900 W night load. The export floor was then calculated as 320 W × 9 h + 2 kWh = 4.88 kWh, covering only ~4.6 hours of night — not the full 10+ hour dark period. After exporting down to that floor the battery hit min SOC around 00:30, leaving the house on expensive grid power until sunrise. Fix: `_hourly_load_kw` is now clamped to `min(max(yesterday_avg, house_load_w, 500 W), 1500 W)`, using the higher of the daily average and the current instantaneous house load (floor 500 W, cap 1500 W to exclude EV charging spikes). This raises the export floor to ~9–12 kWh on typical summer evenings, correctly preventing export when the battery cannot cover the full night. The same fix is applied to `EnergyPlanner.build_plan()` (new `house_load_w` parameter) for consistency.

## What's New in 0.5.26

- **New: Plan executor shadow sensors** – two new sensors show what the plan executor *would have* commanded if running in plan mode, without affecting actual control. `Plan executor: charge` gives the charge setpoint (W) the executor would send — for `solar_charge` slots it is capped at actual solar surplus (not the Solcast forecast), for `grid_charge` slots it uses the plan's target directly. `Plan executor: discharge` gives the discharge setpoint for `export` slots. Both sensors include attributes (`plan_power_w`, `actual_surplus_w`, `capped`) to compare plan estimates against reality. Use these to gain confidence in plan-mode accuracy before switching the controller to plan-driven operation.

## What's New in 0.5.25

- **Fix: EnergyPlanner AVVIKELSE spam after 0.5.24** – the EnergyPlanner was still using the old single-number export conditions (`solar_forecast_tomorrow_kwh >= 20 kWh` and `hours × average_load` floor) while the controller had already been updated to slot-based logic in 0.5.24. This caused continuous divergence warnings (plan=export, actual=idle) whenever the controller correctly blocked export because net solar tomorrow was below the floor. Both calculations in the planner now mirror the controller exactly: (1) export floor is Σ max(0, load_kwh − solar_kwh) per slot until solar takeover + 2 kWh safety margin; (2) `can_export` requires `net_solar_tomorrow_kwh >= export_floor_kwh` where net solar tomorrow is Σ max(0, solar_kwh − load_kwh) per slot for tomorrow's date.

## What's New in 0.5.24

- **Fix: proactive export used crude raw-production check and overestimated export floor** – two related accuracy improvements: (1) The "can we refill tomorrow?" gate previously checked raw Solcast production ≥ 20 kWh, which passed even when house consumption ate most of the solar (e.g. 22 kWh forecast − 13 kWh house = only 9 kWh net available, too little to cover tonight's floor). Export now only runs when **net solar tomorrow** (slot-by-slot: Σ max(0, solar_kwh − load_kwh)) ≥ export floor. (2) The export floor itself was calculated as `hours × average_load + 2 kWh`, treating every hour until solar takeover as a full 875 W drain — ignoring that solar partially covers the house during the morning and evening ramps. The floor is now computed slot-by-slot as Σ max(0, load_kwh − solar_kwh), so the 3 h morning ramp (05:59–08:59) and 1.75 h evening wind-down (19:00–20:47) contribute only their actual deficit rather than full load, giving a more realistic floor (~2 kWh lower) and more accurate export headroom.

## What's New in 0.5.23

- **Fix: battery didn't charge from solar surplus during daytime** – the dynamic `evening_target_soc` was being calculated from the *next daytime slot* where solar covers load (e.g. 8 minutes away at 11:00), giving `hours_dark = 8 min` and `evening_target_soc ≈ 6%`. With the battery at 20% min SOC, `battery_soc < evening_target_soc` was False → `evening_fill = False` → `prefer_sell = True` → the controller exported solar instead of charging the battery. Root cause: the `solar_covers_at` slot search started from `now` and immediately found today's solar production, not tomorrow morning's. Fix: during daytime (next sunset is before next sunrise), the search starts from **tonight's sunset**, so `solar_covers_at` is correctly anchored to tomorrow morning's solar takeover. `hours_dark` is now calculated as `solar_covers_at − sunset` (the actual dark period), giving a realistic `evening_target_soc` (~30–50%) and restoring `evening_fill` logic during the day.

## What's New in 0.5.22

- **Fix: overnight battery drain to minimum SOC** – proactive export ran continuously from the evening peak window (19:00–21:00) all the way to 04:00 the next morning, draining the battery to 20% min SOC. Root cause: when the absolute minimum price threshold (0.70 kr/kWh) triggered export instead of the percentile, `_effective_threshold` was lowered to 0.70, causing the dispatch window to include every overnight slot (all prices ≥ 0.70 kr/kWh). The price-weighted dispatch then spread the battery's energy over 8–10 cheap night slots, and the floor kept shrinking as sunrise approached — leaving the system always above the floor and exporting continuously. Fix: the dispatch window (`_high_slots`) always uses the percentile threshold regardless of what triggered the export; and when no high-price slots remain in the window but only the absolute minimum is met, `export_active` is forced to `False`, stopping all export. The battery now stops exporting once the genuine high-price evening/morning window closes, even if the current sell price is still above 0.70 kr/kWh.

## What's New in 0.5.21

- **New: EnergyPlanner sensors for plan-vs-actual comparison** – four new entities expose the current planned slot from the day planner, making dashboard comparison with actual battery commands straightforward without diving into logs: `Plan: åtgärd` (action text: export/solar_charge/idle/…), `Plan: laddningseffekt` (W, mirrors battery charge setpoint), `Plan: urladdningseffekt` (W, mirrors battery discharge setpoint), `Plan: anledning` (reason text, with `battery_soc_est_pct`, `export_floor_kwh`, and `evening_target_soc_pct` as attributes).

## What's New in 0.5.20

- **Fix: EnergyPlanner false-positive AVVIKELSE during prefer-sell and proactive export** – three divergence patterns that are expected behavior, not planning errors, now resolve to a silent "soft match" (DEBUG instead of WARNING): (1) plan=idle but controller charges — opportunity charging overrides prefer-sell; (2) plan=solar_charge but controller exports — prefer-sell lets solar flow to grid naturally while proactive export drains residual battery headroom above the floor; (3) plan=solar_charge but controller is idle — prefer-sell is active, no battery charge commanded. True divergences (e.g. plan=export but controller charges, or plan=grid_charge but controller is idle) continue to log at WARNING.

## What's New in 0.5.19

- **Fix: EnergyPlanner AVVIKELSE spam during opportunity charging** – the planner planned `idle` for solar slots where the battery was already above the export floor but still below maximum capacity. The controller then applied opportunity charging (buy price below threshold) from solar surplus, causing a divergence warning every 30 seconds. The planner now plans `solar_charge` whenever solar surplus is available *and* the battery has room below max SOC, regardless of export-floor position. `idle` during solar hours is reserved for the case where the battery is already full. This removes false-positive divergence warnings while keeping accurate divergence detection for cases where the controller genuinely deviates.

## What's New in 0.5.18

- **Fix: battery charge command pulled from grid when solar exceeded house load** – during the transition cycle when the battery switches from discharge (proactive export) to charge mode, the grid sensor still reports a large export value from the previous cycle. The derived house-load formula (`grid + solar − battery_discharge + battery_charge − EV`) produces a negative result that is clamped to 0 W, making the controller believe solar surplus equals the full solar output (e.g. 3 600 W). The battery was then commanded to charge at its maximum rate (e.g. 3 300 W), while actual solar surplus was only ~2 800 W — causing ~500 W grid draw. Fix: `yesterday_consumption_kwh` is now fetched before the house-load calculation; when solar exceeds 200 W and the formula result is below yesterday's 24 h average load, that average is used as a floor, keeping the surplus estimate realistic and preventing spurious grid import.

## What's New in 0.5.17

- **Fix: proactive export caused grid draw when house load exceeded solar** – the battery's `discharge_power_setpoint` during proactive export was set to the price-weighted export target (e.g. 500 W), but the Sonnenbatterie interprets this as the *total* battery output, not the grid feed-in on top of self-consumption. When house load (e.g. 988 W) exceeded solar (e.g. 70 W), the battery covered 500 W of that 488 W deficit, and the house imported the remaining ~488 W from the grid — while the intended export to the grid was zero. The discharge setpoint now adds the house deficit: `discharge_w = export_target + max(0, house_load − solar)`. The battery covers the full house deficit and the net feed-in to the grid matches the planned export target.

## What's New in 0.5.16

- **Fix: evening target SOC was near zero in summer** – the dynamic evening target used `predicted_daily_kwh` (the temperature-based heating model) as a proxy for total house load. In summer, heating demand is zero, so the model returned ~1–2 kWh/day, making the evening target ~8% SOC even when the house was drawing 1 000 W. The evening target now uses `max(predicted_daily_kwh, yesterday_consumption_kwh)`. In summer, yesterday's actual consumption (e.g. 20 kWh/day) dominates; in winter, the temperature model can exceed it. The `solar_covers_at` search uses the same effective load, so the derived hours-dark and energy target are consistent with the export floor (which already used `yesterday_consumption_kwh`).

## Recently Added

- **Fix: evening fill charged too little when solar was being exported** – the system computes a dynamic `evening_target_soc` (e.g. 55%) based on hours to solar takeover × house load. But the condition that triggers filling required `solar_until_sunset_kwh < battery_remaining_kwh`. With strong remaining solar (5–9 kW until sunset), that condition was always false — the system assumed future solar would naturally fill the battery. The problem: that solar was exported by `prefer_sell` and never reached the battery. The deadlock was never broken. Fix: `evening_fill` now also triggers when `sell_price >= sell_solar_min_price`, i.e. when future solar would have been exported anyway. The battery fills immediately from surplus (e.g. 5 200 W for ~20 min), then export resumes automatically once the SOC target is reached.

- **Fix: proactive export ignores tomorrow morning's higher prices** – the export dispatch window was capped at `solar_takeover_dt`, meaning any morning slots with higher prices (e.g. 07:00–09:00 at 1.20 SEK/kWh vs tonight at 0.80 SEK/kWh) were invisible to the price-weighted dispatch. The system exported everything at lower prices tonight and had nothing left for the morning peak. The window boundary is now based on solar production from Solcast (`solar_kw < 2 kW`) rather than a clock cutoff: night and early-morning slots are included automatically, while midday slots (full solar) are excluded. The price-weighting then naturally allocates more kWh to the highest-priced slots regardless of whether they fall tonight or tomorrow morning.

- **Fix: proactive export over-drains battery overnight** – the export floor (`hours_dark × load + 2 kWh`) was computed relative to *now* rather than the start of the export window. During a 3-4h export window this caused the floor to shrink by ~4 kWh (3.5h × 1.05 kW), silently revealing extra headroom that the system then exported — leaving the battery closer to the Sonnenbatterie's 20% minimum than planned. `hours_dark` is now anchored to the earliest today price slot, so the floor stays stable throughout the export window and only shrinks once the window has passed.

- **New: Energy Plan Lovelace card** (`www/sem-energy-plan-card.js`) – a custom card that simulates the battery from now through 09:00 the next morning. Shows a canvas chart with price bars, battery kWh curve, and solar kW profile, plus phase cards for the export window, overnight coast, and solar takeover. Reads live data from Nordpool, Sonnenbatterie, and Solcast. Add to a dashboard with `type: custom:sem-energy-plan-card`. Copy `www/sem-energy-plan-card.js` to `/config/www/` on your HA instance and add it as a Lovelace resource (Dashboard → Resources → Add → `/local/sem-energy-plan-card.js`).

- **Fix: legionella triggers on date, not exact hour** – the `due` check compared exact elapsed hours (`days_since >= interval_days`). If the last run was Thursday at 14:00, the next trigger window opened Thursday 14:00 the following week; good opportunities earlier that day (e.g. solar surplus at 10:00) were missed. Now the due date is compared: if today ≥ due date, a run is allowed any time during the day at a suitable moment.

- **Fix: solar takeover observation duplicated on restart** – `_takeover_observed_today` was always reset to `False` on startup. If HA restarted and net surplus was briefly negative in the first update cycle (cloud, restart near the solar edge), a second observation was appended for the same day. The store now persists the last observation date; on load, `_takeover_observed_today` is restored to `True` if today's observation was already recorded. Backwards-compatible with the old list format.

- **Fix: price-weighted proactive export** – discharge power during proactive export was previously calculated as `exportable / remaining_hours`, producing the same wattage regardless of price. Each 15-minute slot is now weighted against the sum of all remaining high-price slots: `W = exportable × (current_price / price_sum) / 0.25 h`. More energy is sold at high prices and less at low prices, without changing the total exported volume.

- **Official Nord Pool integration support** – in addition to the HACS variant (`custom_components/nordpool`), the official HA Nord Pool integration is now supported. Select integration type and price area (e.g. `SE3`) under Grid & Pricing. Prices are fetched via the `nordpool.get_prices_for_date` service, converted from SEK/MWh to SEK/kWh and cached per day. All scheduler logic (proactive export, opportunistic charging, morning export) works identically regardless of source.
- **Morning export – needs-based logic** – battery export in the morning to make room for incoming solar is now triggered based on actual need rather than a fixed time gate. The logic checks: (1) expected solar surplus > available battery headroom, (2) current sell price > production-weighted solar average, (3) battery is not empty. Prevents incorrect export in the afternoon and export at low prices.
- **Fix: `negative_slots_ahead` used sell price** – the price comparison for negative slots ahead used `sell_sek` instead of `spot_sek`, meaning slots with a slightly negative spot price but positive sell price were not counted. Fixed to use `spot_sek` throughout.
- **Fix: battery charged and discharged simultaneously** – solar surplus charging started even when a discharge decision was already made (e.g. proactive export). Guard added: battery only charges from solar surplus when no discharge is commanded in the same cycle.

## What's New in 0.5.11

- **Fix: stabilt export-golv baserat på gårdagens förbrukning** – exportgolvets timberäkning (`hours_dark × huslast + 2 kWh`) använde tidigare den momentana huslasten, vilket innebar att ett värmepumpsstart kl 04:00 kunde mångdubbla golvet och blockera export. Huslasten i beräkningen ersätts nu med gårdagens dygnsmedelförbrukning (`last_period`-attributet från `yesterday_consumption_entity` delat på 24 h). Golvet varierar nu bara när Solcast-prognosen förändras, inte vid momentana toppar.
- **Fix: solar_takeover_dt sparas undan mot inaktuell Solcast-data** – om Solcast-sensorn serverade gammal data (alla slots i det förflutna) föll koden tillbaka på nästa soluppgång (~22 h), vilket gav ett felaktigt golv på 15–20 kWh. Koordinatorn sparar nu senaste giltiga `solar_takeover_dt` och återanvänder det om Solcast tillfälligt inte kan beräkna ett nytt värde. Värdet nollställs när solen producerar igen. Om Solcast uppdateras och ger ett *tidigare* takeover-datum (bättre prognos) uppdateras det sparade värdet.

## What's New in 0.5.10

- **Fix: solar_takeover söker dagens prognos först** – exportgolvet (energireserven som skyddar batteriurladdning) beräknades mot imorgons Solcast-prognos även när solen redan producerade idag. Resultatet blev ett felaktigt "mörker" på 20+ timmar som blockerade export i onödan. Koden söker nu i dagens detailedForecast-slots (framtida slots) innan den faller tillbaka på imorgons prognos.
- **Fix: proaktiv export blockeras vid solöverskott** – batteriet laddade ur för export samtidigt som det laddades från solöverskott. Proaktiv batteriexport aktiveras nu bara när solproduktionen inte överstiger huslasten med mer än 200 W. Vid solöverskott exporteras solenergin naturligt utan batteriinblandning.
- **Fix: state_class för monetary- och energy-sensorer** – ett antal sensorer använde `state_class = MEASUREMENT` i kombination med `device_class = MONETARY` eller `ENERGY`, vilket HA varnar för. `BatteryAccumulatedCostSensor` använder nu `TOTAL`; övriga (prognos- och beräkningssensorer) använder `None`.

## What's New in 0.5.9

- **Proactive export: absolute minimum sell price** – added a new Battery setting `export_min_sell_price_sek_kwh` (default 0.70 SEK/kWh). When the sell price meets or exceeds this threshold, the battery discharges and exports to the grid regardless of the relative percentile position. This fixes the case where all hours of the day have uniformly high prices, causing the percentile threshold to be too high to trigger even at genuinely profitable sell prices. Set to 0 to disable.
- **Proactive export: today-only percentile** – the price percentile used to decide whether to export is now calculated from today's price slots only (previously it included tomorrow's prices, which could inflate the threshold when both days had high prices).

## What's New in 0.5.8

- **Fix: config/options UI labels** – all config flow steps now have correct labels in both English and Swedish. The charger and car setup steps (`charger_menu`, `charger`, `car_menu`, `car` and their options counterparts) previously used wrong step IDs (`ev_menu`/`ev_car`) that did not match the actual flow, causing fields to show raw key names instead of readable labels. All fields — charger name, connection sensor, charge current setpoint, car onboard charger phases, etc. — now display correctly.
- **Backtest framework** – added `testdata/backtest.py` for replaying historical sensor data through the energy controller. Reads timdata/realtidsdata CSVs, reconstructs `EnergyState` at each timestamp, runs `EnergyController.compute()`, and outputs a semicolon-separated CSV with comma as decimal separator (Excel-compatible). Supports date filtering and parameter overrides (`--min-soc`, `--percentile`).

## What's New in 0.5.7

- **Battery cost accounting** – three new sensors track the accumulated cost of energy currently stored in the battery:
  - `sensor.sem_battery_accumulated_cost` – total cost (SEK) of energy in the battery. Grid energy is priced at the current buy price; solar energy is priced at the current sell price (opportunity cost – you could have sold it instead). During discharge the cost is reduced proportionally.
  - `sensor.sem_battery_average_price` – average cost per kWh currently in the battery (SEK/kWh). Always derived directly from the accumulated cost sensor to stay consistent.
  - Diagnostic attributes on the cost sensor: `solar_kwh_total`, `grid_kwh_total`, `last_sell_price`.
- **Fix: battery power sign inversion** – added a configurable option "Inverted battery power sign" in the Battery settings (enable for Sonnenbatterie, which reports positive = discharging). Without this option the accumulated cost sensor compounded upward with every charge/discharge cycle, producing unrealistically high average prices. After enabling this setting, reset the counter using the new service `smart_energy_manager.reset_battery_cost` via Developer Tools → Services.
- **Proactive export** – battery discharges and exports to grid when the current sell price is at or above a configurable percentile of today's prices, provided tomorrow's Solcast forecast is above a minimum threshold. Configurable in Battery settings (`export_sell_percentile`, `export_min_solar_tomorrow_kwh`).
- **Proactive absorption revised** – when negative electricity prices are expected within 2 h, the system now only holds battery headroom (up to 30%). Extra hot water and EV pre-charging are **no longer started proactively** – they wait until the price is actually negative. After a negative price period has passed today, extra hot water is still offered when the boiler needs it.
- **Solar-active battery charge cap** – when solar production exceeds 100 W the battery is never charged beyond the actual solar surplus; grid power is never drawn into the battery while the sun is producing.
- **Discharge blocked when solar covers load** – if solar output already covers the full house load the system no longer forces battery discharge, even at high prices, since no grid power is being purchased anyway.

## What's New in 0.5.6

- **Sell-price-aware battery charging** – when the sell price is at or above a configurable threshold (default SEK 0.80/kWh), solar surplus is exported to the grid instead of stored in the battery.
- **Solcast-based evening fill** – the system compares the remaining Solcast solar forecast until sunset against how much energy the battery still needs to reach the configured minimum SOC (`evening_min_soc`, default 90 %). If the forecast falls short, the battery is charged from solar (or grid) even when sell prices are high. Because the Solcast forecast is calibrated to the actual panel location and angle, this works correctly across all seasons without any fixed clock time.
- **Fix: battery no longer waits for solar during active production** – previously the "wait for solar" flag blocked battery charging all day whenever significant solar was expected within 2 h. The guard now only activates when current solar output is below 500 W (sun not yet producing).
- **Sun integration** – `sun_next_setting` and `sun_next_rising` are read from the `sun.sun` entity and added to `EnergyState` for use in the evening-fill calculation.

## What's New in 0.5.5

- **Solcast 30-minute solar forecast integration** – the `detailedForecast` attribute from Solcast sensors is matched against Nord Pool price slots, giving the scheduler per-slot solar data. New decisions: skip grid battery charging when significant solar is expected within 2 h, and create extra battery headroom ahead of a large solar peak. Six new sensors exposed (see [Entities](#entities)).

## What's New in 0.5.4

- **Auto-clear active car after full charge** – when a car reaches its SOC target the active car selection is automatically reset to "unknown", so the charger is ready for the next session without manual intervention.

## System Overview

```
Grid (3-phase, max 20A/phase)
    │
    │    Battery inverter (3-phase)     Solar inverter (3-phase)
    │           │                             │
    └─────┬─────┴─────────────────────────────┴──── Meter 4 (house load) ────┬──── Other loads
          │                                                                  │
          │                                                              Meter 5
          │                                                                   │
          │                                                                Boiler
          │                                                              (1-phase compressor
          │                                                             +2-phase heating element
          └────┐                                                        +buffer tank)
               │
           EV charger
        (1- or 3-phase hardware,
         multiple cars take turns,
         car selection via HA entity)
```

> **NOTE:** The charger's number of phases (hardware) and the car's number of phases (`car_phases`, 1/2/3-phase) are two separate settings. Phase load in the phase protection is always governed by the **car's** built-in charger, not by the charger hardware's phase count – see [EV Charger and Car Selection](#ev-charger-and-car-selection).

### Electricity Meter Roles

| Meter | Location | Sign | Unit |
|---|---|---|---|
| Meter 1 | Grid connection | Negative = export, Positive = import | kW |
| Meter 2 | Battery inverter AC side | Negative = consumption | W |
| Meter 3 | Solar inverter | Positive = production | W |
| Meter 4 | Property load (excl. solar, battery, and EV charger) | Positive = consumption | W |
| Meter 5 | Boiler | Positive = consumption | W |

> **NOTE:** Meter 1 reports in **kW** – select the unit `kW` in the configuration. The EV charger (internal meter) also reports in **kW**.

---

## Features

### Auto Mode (Self-Consumption)
1. **Cover household load** – priority 1
2. **Charge cars from solar** – when solar surplus ≥ 1,400 W (1-phase) or 4,140 W (3-phase); current adjusted dynamically
3. **Charge battery from solar surplus**
4. **Extra hot water via heating element** – when the battery is full, solar surplus remains, and tank temperature is below the configured maximum (default 70°C)
5. **Discharge the battery** – when the buy price exceeds SEK 0.20/kWh, *or* when it is the best discharge hour in the coming 12h according to the price planning
6. **Negative electricity prices** – absorb all possible solar power into battery, cars, and hot water

### Price Planning (quarter-hours ahead)
Based on Nord Pool's `raw_today`/`raw_tomorrow` attributes, the following are calculated each cycle:
- **Best charging/discharging hour** for the coming 12h – governs battery decisions in both auto and winter mode
- **Proactive absorption** – if ≥ 4 quarter-hours with negative sell price are expected within 2h, the battery holds headroom (up to 30%) to make room for solar. Extra hot water and EV charging are **not** started proactively; they wait until the price is actually negative
- Results are exposed via `sensor.sem_negative_slots_ahead`, `sensor.sem_best_discharge_price`, and `sensor.sem_best_charge_price`

### Solcast Solar Forecast (30-minute resolution)
If Solcast sensors are configured, the `detailedForecast` attribute (30-minute `pv_estimate` values in kW) is read each cycle and matched against Nord Pool price slots:
- **Per-slot solar data** – each price slot gets an expected solar power value (kW) and energy (kWh)
- **Wait for solar** – if ≥ 2 kWh of solar is expected within 2 h *and* the current buy price is above SEK 0.50/kWh, battery charging from the grid is skipped to preserve headroom for free solar
- **Solar aggregates** – rolling forecasts for the next 2 h, 4 h, and 8 h exposed as sensors
- **Peak solar** – the expected peak power (kW) within 8 h and how many hours until it occurs
- `sensor.sem_wait_for_solar` turns `on` when the system is holding back grid charging in anticipation of solar

### Winter Mode
- Charge battery overnight when the price is below a configurable limit
- Discharge battery during expensive hours (evening peak)
- Always charge from solar when possible

### Force Modes
- **Force EV Charge** – charge all connected cars from the grid (max current, phase-limited)
- **Force Battery Charge** – charge battery from the grid

### Manual Mode
- **Manual** – disables all automatic control. No decisions are made; used when you want to control battery/charger/hot water manually via your own automations. Selected via `select.sem_operating_mode` (there is no dedicated switch for this mode).

### Phase Protection
Calculates phase load per phase and reduces in priority order:
1. Reduces EV charging current (lowest-priority charger first)
2. Reduces battery charging
3. Turns off extra hot water (heating element)

---

## EV Charger and Car Selection

### Charger → Cars Model

Each EV charger is configured as a **hardware unit** with a list of cars that can use it. When a car is connected, the user selects which car it is via a `select` entity in the dashboard.

```
Charger A (3-phase hardware)
├── Connection sensor: sensor.charger_status
├── Car 1: Volvo XC40    (SOC sensor, target 80%, 1-phase car charger, phase L1)
└── Car 2: Tesla Model 3 (SOC sensor, target 90%, 3-phase car charger)
```

The phase load that the phase protection calculates is always determined by the **car's** `car_phases` (1/2/3-phase), not by the charger hardware's phase count. A 1-phase car only loads its selected phase; a 2-phase car loads its phase plus the next one (e.g., L1+L2); a 3-phase car loads all three phases equally.

### Car Selection Flow

1. Car is connected → `sensor.sem_charger_a_connected` → `on`
2. The system pauses charging and sends a persistent HA notification
3. The user selects the car in `select.sem_charger_a_active_car`
4. The system charges using the correct SOC target and phase setting for the selected car
5. The notification closes automatically

### Configuration per Charger

| Setting | Description |
|---|---|
| Name | Display name for the charger |
| Connection sensor | Sensor showing `connected`/`charging`/`disconnected` |
| Charger switch | Switch to enable/disable charging |
| Charging current setpoint | Number entity for current setting (A) |
| Charging power sensor | Sensor for actual charging power (optional) |
| Number of phases | 1-phase or 3-phase (hardware) |

### Configuration per Car

| Setting | Description |
|---|---|
| Name | Display name for the car |
| SOC sensor | The car's battery sensor (optional) |
| Target SOC | Charging target in % (default 80%) |
| Number of phases (car's charger) | 1-phase, 2-phase, or 3-phase – the car's **built-in** charger, governs phase load |
| Phase | Starting phase the car charges on (relevant for 1- and 2-phase cars) |

---

## Electric Boiler – Phase Model and Temperature Control

The boiler has two separate circuits:

| Circuit | Operation | Phases | Typical power |
|---|---|---|---|
| Heat pump (compressor) | Normal house heating | **1-phase** (configurable, default L3) | 500–1,500 W |
| Heating element | Extra hot water | **2-phase** (the two remaining phases, default L1+L2) | 3,000–6,000 W |

The element phases are calculated automatically as the two phases *not* used by the compressor.

### Temperature Control for Extra Hot Water

A temperature sensor on the buffer tank is used to prevent unnecessary heating:

- **Max temp** (default 70°C): turns off extra hot water if the tank reaches this level
- **Min temp** (default 65°C): does not start extra hot water until the tank is below this level (even if everything else allows it)
- The temperature is shown in `sensor.sem_hot_water_temp`

### Minimum Runtime (anti-flicker)

To prevent the heating element from switching on/off every 30-second cycle, extra hot water is forced to stay ON for at least **5 minutes** (default, configurable) from the actual start time, even if the control logic wants to turn it off earlier.

---

## Legionella Disinfection

The boiler's built-in Legionella program runs automatically about once a week to heat the hot water to ≥ 65°C (configurable) and eliminate Legionella bacteria.

### How It Works

The system uses a **separate digital switch** to start the boiler's Legionella program:

- We turn the switch **ON** to start the program
- The boiler finishes the program and turns the switch **OFF** automatically when done
- If the switch is turned off prematurely, the cycle is aborted and the run is not counted as successful
- The run is confirmed via the temperature sensor – if the temperature does not reach the target value, the run is not registered as successful

### Start Priority Order

| Priority | Condition | Description |
|---|---|---|
| 1 | Solar surplus ≥ 3,000 W within the desired time window | Free solar power drives the program |
| 2 | Electricity price ≤ configured max price within the desired time window | Runs on cheap grid power |
| 3 | Interval exceeded by 50% (emergency run) | Runs regardless of price, avoids the night 23:00–06:00 |

### Settings

| Setting | Default | Description |
|---|---|---|
| Enabled | Yes | Turn the feature on/off |
| Legionella switch | – | The boiler's digital program switch |
| Confirmation temp | 65°C | Temperature that confirms a successful run |
| Interval | 7 days | How often disinfection should occur |
| Desired time window | 10–15 | Hours when solar is normally available |
| Max price | SEK 1.50/kWh | Do not run on grid power if more expensive |
| Run time | 60 min | Reference time (the boiler controls the actual time) |

---

## Pricing

| | Formula |
|---|---|
| **Buy price** | `(spot price + grid fees + energy tax) × (1 + VAT)` |
| **Sell price** | `spot price + extra revenue (electricity certificates, etc.)` |

---

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.sem_buy_price` | Current buy price SEK/kWh |
| `sensor.sem_sell_price` | Current sell price SEK/kWh |
| `sensor.sem_spot_price` | Nord Pool spot price |
| `sensor.sem_battery_charge_power` | Battery charging setpoint (W) |
| `sensor.sem_battery_discharge_power` | Battery discharging setpoint (W) |
| `sensor.sem_phase_l1_load` | Calculated phase load L1 (W) |
| `sensor.sem_phase_l2_load` | Calculated phase load L2 (W) |
| `sensor.sem_phase_l3_load` | Calculated phase load L3 (W) |
| `sensor.sem_house_load` | House load W – Meter 4 direct or calculated |
| `sensor.sem_solar_surplus` | Solar surplus (W) |
| `sensor.sem_hot_water_temp` | Buffer tank temperature (°C) |
| `sensor.sem_decision_reason` | Text explanation of the latest decision |
| `sensor.sem_operating_mode` | Active operating mode |
| `sensor.sem_legionella_active` | `on` when the Legionella program is running |
| `sensor.sem_legionella_days_since` | Days since the last confirmed run |
| `sensor.sem_legionella_next_due` | Date of the next planned run |
| `sensor.sem_legionella_temp_confirmed` | `on` if temp is confirmed during an ongoing run |
| `sensor.sem_negative_slots_ahead` | Number of quarter-hours with negative sell price in the coming 8h |
| `sensor.sem_best_discharge_price` | Best (highest) buy price for discharging in the coming 12h, with timestamp as attribute |
| `sensor.sem_best_charge_price` | Lowest buy price for charging in the coming 12h, with timestamp as attribute |
| `sensor.sem_yesterday_consumption` | Yesterday's consumption excl. EV charging (kWh) – requires a configured sensor |
| `sensor.sem_solar_next_2h_kwh` | Expected solar energy next 2 h (kWh, Solcast median) |
| `sensor.sem_solar_next_4h_kwh` | Expected solar energy next 4 h (kWh, Solcast median) |
| `sensor.sem_solar_next_8h_kwh` | Expected solar energy next 8 h (kWh, Solcast median) |
| `sensor.sem_peak_solar_kw_next_8h` | Expected peak solar power within 8 h (kW) – attribute: `peak_solar_time` |
| `sensor.sem_hours_to_solar_peak` | Hours until solar peak within 8 h |
| `sensor.sem_wait_for_solar` | `on` when the system is holding back grid charging in anticipation of solar |
| `sensor.sem_battery_accumulated_cost` | Accumulated cost (SEK) of energy currently in the battery – attributes: `solar_kwh_total`, `grid_kwh_total`, `average_price_sek_kwh`, `last_sell_price` |
| `sensor.sem_battery_average_price` | Average cost per kWh of energy currently in the battery (SEK/kWh) |

**Per charger** (replace `<charger>` with the charger's name in lowercase):

| Entity | Description |
|---|---|
| `sensor.sem_charger_<charger>_connected` | `on`/`off` – car physically connected |
| `sensor.sem_charger_<charger>_active_car` | Name of the selected car |
| `sensor.sem_charger_<charger>_current` | Charging current setpoint (A) |
| `sensor.sem_charger_<charger>_enabled` | Charging active `on`/`off` |

> `sensor.sem_phase_lX_load` is a **forecast**, not a measurement – it reflects the calculated phase load *after* the control decisions have been executed.

### Switches

| Entity | Function |
|---|---|
| `switch.sem_force_ev_charge_from_grid` | Force EV charging from the grid |
| `switch.sem_winter_mode` | Enable winter mode |
| `switch.sem_force_charge_battery_from_grid` | Force battery charging |

### Select

| Entity | Function |
|---|---|
| `select.sem_operating_mode` | Select operating mode: `auto` / `winter` / `force_charge_ev` / `force_charge_battery` / `manual` |
| `select.sem_charger_<charger>_active_car` | Select which car is connected to the charger |

### Number (adjustable in real time)

| Entity | Function |
|---|---|
| `number.sem_battery_min_soc` | Battery min SOC % |
| `number.sem_battery_max_soc` | Battery max SOC % |
| `number.sem_ev_soc_target` | Global default charging target % |
| `number.sem_winter_cheap_threshold` | Cheap price threshold (SEK/kWh) |
| `number.sem_winter_expensive_threshold` | Expensive price threshold (SEK/kWh) |
| `number.sem_winter_min_soc` | Winter min SOC % |
| `number.sem_winter_max_soc` | Winter max SOC % |

---

## Installation via HACS

1. Go to HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/fjonson95/smart_energy_manager`, category: Integration
3. Install "Smart Energy Manager"
4. Restart Home Assistant
5. Settings → Integrations → Add → Smart Energy Manager

---

## Configuration

### Dependencies
One of these Nord Pool integrations must be installed:
- **nordpool (HACS)** – `custom_components/nordpool`, provides `raw_today`/`raw_tomorrow` with 15-minute prices
- **Nord Pool (official)** – built-in HA integration (HA 2024.x+), requires `nordpool_type = official` and `nordpool_area` (e.g. `SE3`) in configuration

Optional but recommended:
- **solcast_solar** – solar forecast

### Configuration Flow

Configuration takes place in six steps:

**Step 1 – Grid & Pricing**
- Nord Pool sensor (required) – spot price sensor from the chosen integration
- Nord Pool integration type – `hacs` (default) or `official`
- Nord Pool price area – e.g. `SE3` (only used with `official`)
- Grid meter per phase (L1/L2/L3)
- Current sensors per phase (for phase protection)
- Max current per phase (default 20 A)
- Grid voltage (default 230 V)
- Grid fees, energy tax, VAT, sales compensation
- House load controller – point to Meter 4 for direct house load measurement (recommended)
- Grid meter unit – select `kW` if Meter 1 reports in kilowatts
- EV charger power unit – select `kW` if the charger reports in kilowatts
- Yesterday's consumption – optional sensor for `sensor.sem_yesterday_consumption`

**Step 2 – Solar Panels**
- Solar inverter total and per phase
- Solcast forecasts today/tomorrow

**Step 3 – Battery**
- SOC sensor, power sensor, charging and discharging entities
- Capacity (kWh) and max power (kW)
- Min/max SOC limits

**Step 4 – Electric Boiler / Heat Pump**
- Power sensor (Meter 5) – the compressor's on/off status is read from the power, no separate switch needed
- Switch for extra hot water (heating element)
- Compressor phase (1-phase, default L3) – element phases are calculated automatically
- Heating element rated power (kW)
- **Buffer tank temperature sensor** (optional)
- **Max temp for extra hot water** (default 70°C)
- **Min temp for extra hot water** (default 65°C)
- **Minimum runtime for extra hot water** (default 5 min) – prevents on/off flicker

**Step 5 – Legionella Disinfection**
- Enable/disable the feature
- **Legionella switch** (the boiler's digital program switch)
- **Confirmation temp** (default 65°C – the run is approved when the tank reaches this temp)
- Interval in days (default 7)
- Desired time window (default 10–15)
- Max electricity price for running on grid power (default SEK 1.50/kWh)
- Run time in minutes (reference time)

**Step 6 – EV Charger**
- Add one or more chargers
- Per charger: name, connection sensor, switch, current setpoint entity, charger phase count (1-phase or 3-phase hardware)
- Per car on the charger: name, SOC sensor, SOC target, **the car's phase count** (1/2/3-phase built-in charger – governs phase load), phase (for 1- and 2-phase cars)
- Repeat for each charger

All settings can be edited afterwards via **Settings → Integrations → Smart Energy Manager → Configure**.

### Unit Notes

| Sensor | Unit in HA | Setting |
|---|---|---|
| Meter 1 (grid meter) | kW | Grid meter unit → **kW** |
| Meter 3 / SolInv_prod | W | (default W) |
| BatInv_in_out | W, pos=charging, neg=discharging | (default W) |
| Meter 4 (house load) | W | House load controller → Meter 4 |
| Meter 5 (boiler) | W | Boiler power sensor → Meter 5 |
| Car_charging (internal) | kW | EV charger power unit → **kW** |

---

## Logging

```yaml
logger:
  logs:
    custom_components.smart_energy_manager: debug
```

---

## Example: Automation for Winter Mode

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

## Example: Dashboard (Lovelace)

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: select.sem_operating_mode
    name: Operating mode

  - type: glance
    entities:
      - entity: sensor.sem_buy_price
        name: Buy SEK/kWh
      - entity: sensor.sem_sell_price
        name: Sell SEK/kWh
      - entity: sensor.sem_solar_surplus
        name: Solar surplus W
      - entity: sensor.sem_house_load
        name: House load W
      - entity: sensor.sem_hot_water_temp
        name: Hot water °C

  - type: gauge
    entity: sensor.sem_battery_charge_power
    name: Battery charging W
    max: 5000

  - type: entities
    title: Charger A
    entities:
      - entity: sensor.sem_charger_laddare_a_connected
        name: Connected
      - entity: select.sem_charger_laddare_a_active_car
        name: Selected car
      - entity: sensor.sem_charger_laddare_a_current
        name: Charging current A
      - entity: sensor.sem_charger_laddare_a_enabled
        name: Charging active

  - type: entities
    title: Legionella
    entities:
      - entity: sensor.sem_legionella_active
        name: In progress
      - entity: sensor.sem_legionella_temp_confirmed
        name: Temp confirmed
      - entity: sensor.sem_legionella_days_since
        name: Days since last
      - entity: sensor.sem_legionella_next_due
        name: Next run

  - type: entity
    entity: sensor.sem_decision_reason
    name: Latest decision
```
