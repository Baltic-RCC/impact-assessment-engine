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

**`run_remedial_actions_flow_influence_assessment()`** – Calculates \|ΔI_t\| / PATL_t · 100 (or the same with ΔP when the limit is not a current limit) for each remedial action, in the base case and under each contingency. It currently fails at the threshold step (`Parameters.threshold` is not defined).

**`max_influence_factors_by_xnec()` and the `max_if_*` / `threshold_if_*` properties** – Keep the maximum factor per (assessed element, influencing element) pair, or filter rows by a threshold.

**Diagnostic properties** – `diverged_*`, `not_applied_*` and `island_leading_*` list the contingencies, influencing elements and remedial actions that could not be evaluated.

**`impact_assessment_reporting.get_impact_assessment_matrix_rcp()`** – Converts the results into NC ImpactAssessmentMatrix classes, taking the maximum impact per remedial action (or schedule) per impacted system operator.

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
