import sys
import logging
from pathlib import Path
import pandas as pd
import pypowsybl as pp
sys.path.append(str(Path(__file__).parents[1]))  # repository root, to run this file directly
from impact_assessment_engine.impact_assessment import Parameters
from remedial_action_lookup.job_config import RemedialActionLookup, split
from remedial_action_lookup.ra_lookup import (find_cross_border_relevant_remedial_actions, read_assessed_element_list,
                                              get_contingencies, read_contingency_list, solve_initial_loadflow)

# Start logger
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    conf = RemedialActionLookup()
    logging.basicConfig(level=conf.log_level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logger.info(f"Job parameters: {conf.model_dump()}")

    network = pp.network.load(str(conf.model_path), parameters=conf.cgmes_import_parameters)
    assessed_elements = read_assessed_element_list(conf.assessed_element_list_path, network) if conf.assessed_element_list_path else None

    # Contingencies from contingency list or looked up from model, base case only when N-1 is not run
    contingencies = None
    if conf.run_if_assessment_for_ras and conf.run_n1_for_ras:
        if conf.contingency_list_path:
            contingencies = read_contingency_list(conf.contingency_list_path, network)
        else:
            solve_initial_loadflow(network)  # element status is taken from currents
            contingencies = get_contingencies(network=network,
                                              min_nominal_v=conf.min_nominal_voltage_kv,
                                              min_generator_p=conf.min_generator_p_mw,
                                              energy_sources=split(conf.generator_energy_sources),
                                              regions=split(conf.contingency_regions))
        logger.info(f"Contingencies: {contingencies['registered_resource'].nunique()}")

    remedial_actions = find_cross_border_relevant_remedial_actions(network=network,
                                                                   min_nominal_v=conf.min_nominal_voltage_kv,
                                                                   threshold_percent=conf.threshold_percent,
                                                                   parameters=Parameters(ignore_emerged_islands=conf.ignore_emerged_islands),
                                                                   assessed_elements=assessed_elements,
                                                                   contingencies=contingencies,
                                                                   run_if_assessment=conf.run_if_assessment_for_ras)

    with pd.ExcelWriter(conf.output_path) as writer:
        if conf.run_if_assessment_for_ras:
            remedial_actions[remedial_actions['cross_border_relevant']].to_excel(writer, sheet_name='xra', index=False)
        remedial_actions.to_excel(writer, sheet_name='all', index=False)
        if contingencies is not None:
            contingencies.to_excel(writer, sheet_name='contingencies', index=False)
    logger.info(f"Remedial actions written to: {conf.output_path}")
