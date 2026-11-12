# impact-assessment-engine
A script based on Pypowsybl for running impact assessment related calculations according to CSAm

# API Docs
Read the documentation for this project here https://baltic-rcc.github.io/impact-assessment-engine/

# Overview
## Engine functions

**`Parameters`** – Settings for island handling (`ignore_emerged_islands`) and result thresholds (identification, filtering, voltage, current), in percent.

**`ImpactAssessment(...)`** – Takes the network, contingencies (*i*), influencing elements (*r*), assessed elements (*t*), assessed nodes (*m*), remedial actions and load-flow parameters. On creation it runs an initial AC load flow, records the network components, checks input consistency and reads the permanent limits.

**`consistency_check_by_element_connectivity()`** – Splits contingencies and influencing elements into available and disconnected sets, based on terminal connection status in the model.

**`get_limits()`** – Reads permanent operational limits (value and type) for each element and side into `limit_t1`/`limit_t2`.

**`get_elements_flow()` / `get_assessed_nodes_voltage()`** – Reads p, i, connection status and country for the listed elements, and bus voltages for the assessed voltage levels.

**`disconnect_contingency()` / `connect_contingency()` / `disconnect_influencing_element()`** – Applies or reverts an outage and solves the AC load flow. Each case is logged as not applied, diverged or creating an island.

**`apply_remedial_action()`** – Applies the alterations of a grid state alteration remedial action and solves the load flow. Only `TopologyAction` with `ACDCTerminal.connected` (connect or disconnect a whole element) works. Shunt compensator and rotating machine actions are stubs.

**`solve_loadflow()`** – Runs an AC load flow with OpenLoadFlow and logs the status of each component.

**`run_outage_flow_influence_assessment()`** – Calculates the power flow filtering and identification influence factors for each (t, r, i) combination and stores them in `flow_results_df`.

**`run_outage_voltage_influence_assessment()`** – Calculates \|V_post − V_pre\| / V_nom · 100 for each assessed bus, influencing element and contingency, and stores the result in `voltage_results_df`.

**`run_remedial_actions_flow_influence_assessment()`** – Calculates \|ΔI_t\| / PATL_t · 100 (or the same with ΔP when the limit is not a current limit) for each remedial action, in the base case and under each contingency, or in the base case only when no contingencies are given. `if_above_threshold` marks rows where the side 1 factor reaches `threshold_current_percent`.

**`max_influence_factors_by_xnec()` and the `max_if_*` / `threshold_if_*` properties** – Keep the maximum factor per (assessed element, influencing element) pair, or filter rows by a threshold.

**Diagnostic properties** – `diverged_*`, `not_applied_*` and `island_leading_*` list the contingencies, influencing elements and remedial actions that could not be evaluated.

**`impact_assessment_reporting.get_impact_assessment_matrix_rcp()`** – Converts the results into NC ImpactAssessmentMatrix classes, taking the maximum impact per remedial action (or schedule) per impacted system operator.

## Remedial action lookup jobs

The `remedial_action_lookup` folder has four scripts that run on a CGMES model. Each one reads its settings from the `.conf` file of the same name in that folder.

| Script | Settings file | Result |
| --- | --- | --- |
| `run_ae_lookup.py` | `ae_lookup.conf` | Assessed element list: lines, transformers and tie line sides at the relevant voltage level |
| `run_co_lookup.py` | `co_lookup.conf` | Contingency list: N-1 of those elements and of large generators in service |
| `run_ra_lookup.py` | `ra_lookup.conf` | Topological remedial action candidates, flagged as cross-border relevant by their influence factor on neighbouring TSO elements |
| `run_ra_assessment.py` | `ra_assessment.conf` | Flow influence factors of a remedial action list, in the base case and under each contingency |

Set `MODEL_PATH` and the other paths in the `.conf` files, then run each script from the repository root, for example `python remedial_action_lookup/run_ae_lookup.py`. The [remedial action lookup guide](https://baltic-rcc.github.io/impact-assessment-engine/guides/remedial_action_lookup/) explains every setting, the outputs and common errors.

## Not supported (CSAm power and voltage influence assessment)

- Influence factors in active power (MW), and PATL in MVA/MW.
- The N state as a calculation case (needed for AI.2.1 voltage and for consistency with the remedial action run).
- The voltage influence factor as N vs N-r only; currently it is calculated only under contingencies.
- One influence factor per *r*: MAX over i, t and scenarios *s*, by element id.
- Classifying *r* with the combined rule (IF^id > T1 and IF^f > T2, strict `>`).
- Multi-scenario runs (one network per engine instance).
- IF = 0 across synchronous areas; HVDC outages as *i* or *r*.
- Checks that *r* is outside and *t* is inside the TSO's control area (left to the caller).
- Checks that thresholds fall within the CSAm AI.1 ranges.
- Remedial actions:
  - The influence factor defined by the AI.2.1 formula.
  - Topology actions that close an element, switch a switch or bus coupler, or change several elements at once (limitations will be addressed together with support for RA NC import to pypowsybl)
  - Power remedial actions (redispatching, countertrading); PST and HVDC set-point actions. (redispatching/countertrading will be addressed at a later stage, PST and HVDC setpoints are not considered as neccessary for Baltics use cases so will probably not be addressed soon)
- Generators or loads as *r*, or elements without operational limits.
