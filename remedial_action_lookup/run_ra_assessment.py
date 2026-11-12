import sys
import logging
from pathlib import Path
import pandas as pd
import pypowsybl as pp
sys.path.append(str(Path(__file__).parents[1]))  # repository root, to run this file directly
from brcc_pypowsybl_helpers import converters
from impact_assessment_engine.impact_assessment import ImpactAssessment, Parameters
from remedial_action_lookup.job_config import RemedialActionAssessment, split
from remedial_action_lookup.ra_lookup import get_assessed_elements, read_assessed_element_list

# Start logger
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    conf = RemedialActionAssessment()
    logging.basicConfig(level=conf.log_level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logger.info(f"Job parameters: {conf.model_dump()}")

    network = pp.network.load(str(conf.model_path), parameters=conf.cgmes_import_parameters)

    # Remedial actions from common remedial action list, ImpactAssessment supports grid state alteration remedial actions only
    remedial_actions = pd.read_excel(conf.remedial_action_list_path)
    if split(conf.remedial_action_names):
        remedial_actions = remedial_actions[remedial_actions['ra_name'].isin(split(conf.remedial_action_names))]
    power_remedial_actions = remedial_actions['type'] != 'GridStateAlterationRemedialAction'
    if power_remedial_actions.any():
        logger.warning(f"Power remedial actions are not supported, skipped: {remedial_actions.loc[power_remedial_actions, 'ra_name'].unique().tolist()}")
    remedial_actions = converters.remedial_actions_from_dataframe(data=remedial_actions[~power_remedial_actions])

    # Contingencies from common contingency list, base case only when not provided
    contingencies = None
    if conf.contingency_list_path:
        contingencies = pd.read_excel(conf.contingency_list_path)
        if split(conf.contingency_names):
            contingencies = contingencies[contingencies['co_name'].isin(split(conf.contingency_names))]
        # ImpactAssessment fails on empty contingency list
        contingencies = converters.contingencies_from_dataframe(data=contingencies) or None
        if contingencies is None:
            logger.warning(f"No contingencies selected from {conf.contingency_list_path}, assessing base case only")

    # Assessed elements from assessed element list or looked up from model, filtered by regions in both cases
    if conf.assessed_element_list_path:
        assessed_elements = read_assessed_element_list(conf.assessed_element_list_path, network)
    else:
        assessed_elements = get_assessed_elements(network=network, min_nominal_v=conf.min_nominal_voltage_kv)
    if split(conf.assessed_regions):
        assessed_elements = assessed_elements[assessed_elements['secured_for_region'].isin(split(conf.assessed_regions))]
    logger.info(f"Assessed elements: {len(assessed_elements)}")

    ia = ImpactAssessment(network=network,
                          contingencies=contingencies,
                          remedial_actions=remedial_actions,
                          assessed_elements=converters.assessed_elements_from_dataframe(data=assessed_elements),
                          parameters=Parameters(ignore_emerged_islands=conf.ignore_emerged_islands,
                                                threshold_current_percent=conf.threshold_percent))
    if not ia.converged:
        raise Exception("Initial load flow did not converge, remedial actions can not be assessed")
    ia.run_remedial_actions_flow_influence_assessment()

    if ia.flow_results_df is None:
        logger.error(f"No remedial action flow influence results available, nothing written")
    else:
        with pd.ExcelWriter(conf.output_path) as writer:
            ia.flow_results_df.to_excel(writer, sheet_name='flow_influence', index_label='equipment')
            assessed_elements.to_excel(writer, sheet_name='assessed_elements', index=False)
        logger.info(f"Remedial action flow influence results written to: {conf.output_path}")
