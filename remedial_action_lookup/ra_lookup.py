import logging
import uuid
from pathlib import Path
from typing import List
import numpy as np
import pandas as pd
import pypowsybl as pp
from nc_data_exchange.config import Areas
from brcc_pypowsybl_helpers.helper import get_network_elements
from brcc_pypowsybl_helpers import converters
from impact_assessment_engine.impact_assessment import ImpactAssessment, Parameters

# Start logger
logger = logging.getLogger(__name__)

# Relevant element types with their eq_type in common remedial action list and in common assessed element list
ELEMENT_TYPES = {pp.network.ElementType.LINE: ('ACLineSegment', 'line'),
                 pp.network.ElementType.TWO_WINDINGS_TRANSFORMER: ('PowerTransformer', 'transformer'),
                 pp.network.ElementType.THREE_WINDINGS_TRANSFORMER: ('PowerTransformer', 'transformer')}

# Generator energy sources of contingency lookup, pypowsybl EnergySource values: HYDRO, NUCLEAR, WIND, THERMAL, SOLAR, OTHER
GENERATOR_ENERGY_SOURCES = ['THERMAL', 'NUCLEAR', 'HYDRO']


def get_operator(country: pd.Series) -> pd.Series:
    """Maps country codes to TSO names, country code is kept where TSO is not known"""
    return country.map(Areas().df.set_index('short_name')['tso']).fillna(country)


def is_in_service(currents: pd.DataFrame) -> pd.Series:
    """
    Element is in service when current flows at each given end: connected flags stay True when element is opened by
    a switch, but its open end has zero current. Network model must have solved load flow
    """
    return currents.fillna(0).gt(1e-3).all(axis=1)


def solve_initial_loadflow(network: pp.network.Network) -> None:
    """Initial load flow validation of ImpactAssessment, lookups take element status from currents of solved load flow"""
    if not ImpactAssessment(network=network).converged:
        raise Exception("Initial load flow did not converge, element status can not be taken from currents")


def get_relevant_elements(network: pp.network.Network, min_nominal_v: float = 330) -> pd.DataFrame:
    """Lines and transformers at relevant voltage level, transformer is relevant if its highest winding is"""
    elements = pd.concat([get_network_elements(network=network, element_type=element_type).assign(ra_eq_type=ra_eq_type, ae_eq_type=ae_eq_type)
                          for element_type, (ra_eq_type, ae_eq_type) in ELEMENT_TYPES.items()])
    return elements[elements.filter(regex=r'^nominal_v\d$').max(axis=1) >= min_nominal_v]


def get_tie_lines(network: pp.network.Network) -> pd.DataFrame:
    """Tie lines with voltage level and country of each side"""
    # get_network_elements fails on network model without tie lines
    if network.get_tie_lines().empty:
        return pd.DataFrame(columns=['name', 'dangling_line1_id', 'dangling_line2_id', 'country1', 'country2', 'nominal_v1', 'nominal_v2'])
    return get_network_elements(network=network, element_type=pp.network.ElementType.TIE_LINE)


def get_assessed_elements(network: pp.network.Network, min_nominal_v: float = 330, regions: List[str] | None = None) -> pd.DataFrame:
    """
    Lines, transformers and tie line sides at relevant voltage level as assessed elements, only of given regions
    (countries) if provided. Each tie line side is separate assessed element of its own TSO.
    Returned in common assessed element list columns, which converters.assessed_elements_from_dataframe() accepts
    """
    elements = get_relevant_elements(network=network, min_nominal_v=min_nominal_v)
    tie_lines = get_tie_lines(network=network)
    assessed_elements = pd.concat([pd.DataFrame({'name': elements['name'],
                                                 'grid_element_id': elements.index,
                                                 'eq_type': elements['ae_eq_type'],
                                                 'secured_for_region': elements['country1']})] +
                                  [pd.DataFrame({'name': tie_lines['name'],
                                                 'grid_element_id': tie_lines[f'dangling_line{side}_id'],
                                                 'eq_type': 'tieline',
                                                 'secured_for_region': tie_lines[f'country{side}']})[tie_lines[f'nominal_v{side}'] >= min_nominal_v]
                                   for side in (1, 2)], ignore_index=True)
    if regions:
        assessed_elements = assessed_elements[assessed_elements['secured_for_region'].isin(regions)]

    return assessed_elements.assign(registered_resource=[f"_{uuid.uuid4()}" for _ in range(len(assessed_elements))],
                                    operator=get_operator(assessed_elements['secured_for_region']))


def read_assessed_element_list(path: str | Path, network: pp.network.Network) -> pd.DataFrame:
    """
    Assessed element list xlsx in common assessed element list columns, as written by run_ae_lookup.py.
    Elements not found in network model, e.g. list made from another model, are skipped
    """
    # Empty name is read as NaN, which AssessedElement does not accept
    assessed_elements = pd.read_excel(path).fillna({'name': ''})
    found = assessed_elements['grid_element_id'].isin(network.get_identifiables().index)
    if not found.any():
        raise Exception(f"None of assessed elements in {path} found in network model")
    if not found.all():
        logger.warning(f"Assessed elements not found in network model, skipped: {assessed_elements.loc[~found, 'name'].tolist()}")

    return assessed_elements[found]


def get_contingencies(network: pp.network.Network,
                      min_nominal_v: float = 330,
                      min_generator_p: float = 100,
                      energy_sources: List[str] = GENERATOR_ENERGY_SOURCES,
                      regions: List[str] | None = None) -> pd.DataFrame:
    """
    Ordinary N-1 contingency of each element in service: lines, transformers and tie line sides at relevant voltage
    level, and generators of given energy sources with maximum active power of min_generator_p MW and above at any
    voltage level, as units connect through step-up transformers. Only of given regions (countries) if provided.
    Each tie line side is separate contingency of its own TSO, disconnecting either side opens the tie line.
    Network model must have solved load flow, element status is taken from its currents.
    Returned in common contingency list columns, which converters.contingencies_from_dataframe() accepts
    """
    branches = get_relevant_elements(network=network, min_nominal_v=min_nominal_v)
    tie_lines = get_tie_lines(network=network)
    dangling_lines = network.get_dangling_lines(attributes=['name', 'i'])
    generators = get_network_elements(network=network, element_type=pp.network.ElementType.GENERATOR)
    generators = generators[generators['energy_source'].isin([source.upper() for source in energy_sources]) &
                            (generators['max_p'] >= min_generator_p)]

    # Third winding is ignored as tertiary windings are often open while in service
    # Tie line is in service when current flows at both sides, same as line at both ends
    tie_lines_in_service = is_in_service(pd.concat([tie_lines[f'dangling_line{side}_id'].map(dangling_lines['i']) for side in (1, 2)], axis=1))
    elements = pd.concat([pd.DataFrame({'eq_name': branches['name'],
                                        'grid_element_id': branches.index,
                                        'eq_type': branches['ra_eq_type'],
                                        'area': branches['country1'],
                                        'in_service': is_in_service(branches[['i1', 'i2']])}),
                          pd.DataFrame({'eq_name': generators['name'],
                                        'grid_element_id': generators.index,
                                        'eq_type': 'SynchronousMachine',
                                        'area': generators['country'],
                                        'in_service': is_in_service(generators[['i']])})] +
                         [pd.DataFrame({'eq_name': tie_lines[f'dangling_line{side}_id'].map(dangling_lines['name']),
                                        'grid_element_id': tie_lines[f'dangling_line{side}_id'],
                                        'eq_type': 'ACLineSegment',
                                        'area': tie_lines[f'country{side}'],
                                        'in_service': tie_lines_in_service})[tie_lines[f'nominal_v{side}'] >= min_nominal_v]
                          for side in (1, 2)], ignore_index=True)
    if regions:
        elements = elements[elements['area'].isin(regions)]

    out_of_service = ~elements['in_service']
    if out_of_service.any():
        logger.info(f"Elements out of service, skipped as contingencies: {elements.loc[out_of_service, 'eq_name'].tolist()}")
    elements = elements[~out_of_service]

    return pd.DataFrame({
        'registered_resource': [f"_{uuid.uuid4()}" for _ in range(len(elements))],
        'co_name': 'OCO_' + elements['eq_name'],
        'type': 'Ordinary',
        'area': elements['area'],
        'operator': get_operator(elements['area']),
        'grid_element_id': elements['grid_element_id'],
        'eq_name': elements['eq_name'],
        'eq_type': elements['eq_type'],
    })


def read_contingency_list(path: str | Path, network: pp.network.Network) -> pd.DataFrame:
    """
    Contingency list xlsx in common contingency list columns, as written by run_co_lookup.py.
    Contingencies with any element not found in network model, e.g. list made from another model, are skipped whole,
    as applying them partially would assess another contingency
    """
    # Empty name is read as NaN, which Contingency does not accept
    contingencies = pd.read_excel(path).fillna({'co_name': ''})
    found = contingencies['grid_element_id'].isin(network.get_identifiables().index)
    found = found.groupby(contingencies['registered_resource']).transform('all')
    if not found.any():
        raise Exception(f"None of contingencies in {path} found in network model")
    if not found.all():
        logger.warning(f"Contingencies with elements not found in network model, skipped: {contingencies.loc[~found, 'co_name'].unique().tolist()}")

    return contingencies[found]


def get_remedial_action_candidates(network: pp.network.Network, min_nominal_v: float = 330) -> pd.DataFrame:
    """
    Topological remedial action for each line and transformer at relevant voltage level, opposite to element status
    in network model: disconnect element in service or connect element out of service.
    Network model must have solved load flow, element status is taken from its currents.
    Returned in common remedial action list columns, which converters.remedial_actions_from_dataframe() accepts
    """
    elements = get_relevant_elements(network=network, min_nominal_v=min_nominal_v)

    # Third winding is ignored as tertiary windings are often open while in service.
    # ponytail: network.connect() does not close switches, so connecting element opened by a switch is reported as not applied
    in_service = is_in_service(elements[['i1', 'i2']])

    return pd.DataFrame({
        'type': 'GridStateAlterationRemedialAction',
        'registered_resource': [f"_{uuid.uuid4()}" for _ in range(len(elements))],
        'ra_name': 'RA_' + elements['name'],
        'region': elements['country1'],
        'operator': get_operator(elements['country1']),
        'alt_type': 'TopologyAction',
        'alt_name': elements['name'],
        'property': 'ACDCTerminal.connected',
        'equipment': elements.index,
        'eq_type': elements['ra_eq_type'],
        'normal_value': np.where(in_service, 0, 1),
        'direction': 'none',
        'value_kind': 'absolute',
    })


def get_neighbouring_countries(tie_lines: pd.DataFrame) -> pd.DataFrame:
    """Country pairs sharing a tie line, in both directions"""
    pairs = tie_lines[['country1', 'country2']].dropna()
    pairs = pairs[pairs.country1 != pairs.country2]
    return pd.concat([pairs, pairs.set_axis(['country2', 'country1'], axis=1)]).drop_duplicates()


def find_cross_border_relevant_remedial_actions(network: pp.network.Network,
                                                min_nominal_v: float = 330,
                                                threshold_percent: float = 5,
                                                parameters: Parameters = Parameters(),
                                                assessed_elements: pd.DataFrame | None = None,
                                                contingencies: pd.DataFrame | None = None,
                                                run_if_assessment: bool = True) -> pd.DataFrame:
    """
    Runs flow influence assessment of each candidate topological remedial action on assessed elements, by default
    lines, transformers and tie line sides at relevant voltage level, at base case and at each N-1 contingency when
    contingencies are given in common contingency list columns, e.g. from get_contingencies().
    Remedial action is cross border relevant when its influence factor on any element of neighbouring TSO (sharing
    a tie line) is at least threshold_percent at base case or any contingency, impacted_contingency is where it is highest.
    Remedial actions which diverged or could not be applied have no impact_factor.
    With run_if_assessment False all candidates are returned without influence factor assessment
    """
    # Initial load flow of ImpactAssessment also gives element currents for remedial action candidates
    # ImpactAssessment fails on empty contingency list, base case only then
    contingencies = converters.contingencies_from_dataframe(data=contingencies) if contingencies is not None else []
    ia = ImpactAssessment(network=network, contingencies=contingencies or None, parameters=parameters)
    if not ia.converged:
        raise Exception("Initial load flow did not converge, remedial actions can not be assessed")

    remedial_actions = get_remedial_action_candidates(network=network, min_nominal_v=min_nominal_v)
    logger.info(f"Remedial action candidates at {min_nominal_v} kV and above: {len(remedial_actions)}")
    if not run_if_assessment:
        return remedial_actions

    neighbours = get_neighbouring_countries(get_tie_lines(network=network))
    if neighbours.empty:
        logger.warning(f"No tie lines between different countries in network model, remedial actions can not be cross border relevant")
        return remedial_actions.assign(impact_factor=np.nan, cross_border_relevant=False)

    ia.remedial_actions = converters.remedial_actions_from_dataframe(data=remedial_actions)
    if assessed_elements is None:
        assessed_elements = get_assessed_elements(network=network, min_nominal_v=min_nominal_v)
    ia.assessed_elements = converters.assessed_elements_from_dataframe(data=assessed_elements)
    ia.run_remedial_actions_flow_influence_assessment()

    if ia.flow_results_df is None:
        logger.error(f"No remedial action influence results available")
        return remedial_actions.assign(impact_factor=np.nan, cross_border_relevant=False)
    if contingencies:
        # Base case has its own contingency_id, contingencies without results are logged by ImpactAssessment
        assessed_contingencies = ia.flow_results_df['contingency_id'].nunique() - 1
        logger.info(f"Remedial actions assessed at base case and {assessed_contingencies} of {len(contingencies)} contingencies, "
                    f"rest were out of service, not applied, diverged or without results")

    # Influence on elements of neighbouring TSOs only, maximum per remedial action over base case and contingencies
    results = ia.flow_results_df.merge(remedial_actions[['registered_resource', 'region']], left_on='influencing_id', right_on='registered_resource')
    results = results.merge(neighbours, left_on=['region', 'country'], right_on=['country1', 'country2'])
    results = results.dropna(subset=['if_filtering_max'])
    results = results.loc[results.groupby('registered_resource')['if_filtering_max'].idxmax()]
    results = pd.DataFrame({'registered_resource': results['registered_resource'],
                            'impact_factor': results['if_filtering_max'],
                            'impacted_operator': get_operator(results['country']),
                            'impacted_element': results['name'],
                            'impacted_contingency': results['contingency_name']})

    remedial_actions = remedial_actions.merge(results, how='left', on='registered_resource')
    remedial_actions['cross_border_relevant'] = remedial_actions['impact_factor'] >= threshold_percent
    logger.info(f"Cross border relevant remedial actions: {remedial_actions['cross_border_relevant'].sum()} of {len(remedial_actions)}")

    return remedial_actions
