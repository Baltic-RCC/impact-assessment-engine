import sys
import pypowsybl as pp
import pandas as pd
import numpy as np
from pydantic_settings import BaseSettings
from brcc_pypowsybl_helpers.lf_parameters import LF_PARAMETERS
from brcc_pypowsybl_helpers.helper import (get_network_elements, get_network_elements_by_ids, get_connected_component_counts,
                                    attr_to_dict, performance_counter)
from brcc_pypowsybl_helpers.models import (Contingency, AssessedElement, InfluencingElement, GridSateAlterationRemedialAction,
                                    PowerRemedialAction, GridSateAlteration)
from brcc_pypowsybl_helpers import converters
import logging
from typing import List, Dict, Any

# Start logger
logger = logging.getLogger(__name__)


class Parameters(BaseSettings):
    # Core
    ignore_emerged_islands: bool = True
    # Results thresholds
    threshold_voltage_percent: int | None = 5
    threshold_identification_percent: int | None = 15
    threshold_filtering: int | None = 3


class ImpactAssessment:
    """
    Assessed elements (t)
    Influencing elements (r)
    Contingency elements (i)
    """
    ELEMENTS_COLS = ['name', 'type', 'p', 'p1', 'p2', 'i', 'i1', 'i2', 'connected', 'connected1', 'connected2', 'country', 'country1', 'country2']
    NODES_COLS = ['name', 'v_mag', 'voltage_level_name', 'nominal_v', 'substation_name', 'country']

    def __init__(self,
                 network: pp.network,
                 contingencies: List[Contingency] | None = None,
                 influencing_elements: List[InfluencingElement] | None = None,
                 remedial_actions: List[GridSateAlterationRemedialAction | PowerRemedialAction] | None = None,
                 assessed_elements: List[AssessedElement] | None = None,
                 assessed_nodes: pd.DataFrame | None = None,
                 lf_parameters: pp.loadflow.Parameters = LF_PARAMETERS,
                 parameters: Parameters = Parameters(),
                 ):

        self.network = network
        self.contingencies = contingencies
        self.influencing_elements = influencing_elements
        self.remedial_actions = remedial_actions
        self.assessed_elements = assessed_elements
        self.assessed_nodes = assessed_nodes
        self.lf_parameters = lf_parameters
        self.parameters = parameters

        self.converged = None
        self.available_contingencies = None
        self.available_influencing_elements = None
        self.disconnected_contingencies = None
        self.disconnected_influencing_elements = None
        self.flow_results_df = None
        self.voltage_results_df = None

        self._diverged_contingencies = []
        self._not_applied_contingencies = []
        self._island_leading_contingencies = []
        self._diverged_influencing_elements = []
        self._not_applied_influencing_elements = []
        self._island_leading_influencing_elements = []
        self._diverged_remedial_actions = []
        self._not_applied_remedial_actions = []
        self._island_leading_remedial_actions = []

        # Initial validation of load flow
        logger.info(f"Performing initial loadflow validation")
        result = self.solve_loadflow(suspend_output=True)
        if result[0].status.value == 0:  # status.value equals 0 means converged
            self.converged = True
            logger.info(f"[INITIAL] Load flow status: {result[0].status_text}")
            self.initial_network_components = get_connected_component_counts(network=self.network)
            logger.info(f"[INITIAL] Network connected components: {self.initial_network_components}")
        else:
            self.converged = False
            logger.error(f"[INITIAL] Load flow status: {result[0].status.name}")

        # Run input data consistency check
        self.consistency_check_by_element_connectivity()

        # Get static data of network
        self.limits = self.get_limits()

    @property
    def assessed_elements_df(self):
        return pd.DataFrame([ae.model_dump() for ae in self.assessed_elements])

    @property
    def contingencies_df(self):
        return pd.DataFrame([co.model_dump() for co in self.contingencies])

    @property
    def influencing_elements_df(self):
        return pd.DataFrame([ie.model_dump() for ie in self.influencing_elements])

    @property
    def remedial_actions_df(self):
        return pd.DataFrame([ra.model_dump() for ra in self.remedial_actions])

    @property
    def diverged_contingencies(self):
        return pd.DataFrame([x.model_dump() for x in self._diverged_contingencies]).drop_duplicates()

    @property
    def diverged_influencing_elements(self):
        return pd.DataFrame([x.model_dump() for x in self._diverged_influencing_elements]).drop_duplicates()

    @property
    def diverged_remedial_actions(self):
        return pd.DataFrame([x.model_dump() for x in self._diverged_remedial_actions]).drop_duplicates()

    @property
    def not_applied_contingencies(self):
        return pd.DataFrame([x.model_dump() for x in self._not_applied_contingencies]).drop_duplicates()

    @property
    def not_applied_influencing_elements(self):
        return pd.DataFrame([x.model_dump() for x in self._not_applied_influencing_elements]).drop_duplicates()

    @property
    def not_applied_remedial_actions(self):
        return pd.DataFrame([x.model_dump() for x in self._not_applied_remedial_actions]).drop_duplicates()

    @property
    def island_leading_contingencies(self):
        return pd.DataFrame([x.model_dump() for x in self._island_leading_contingencies]).drop_duplicates()

    @property
    def island_leading_influencing_elements(self):
        return pd.DataFrame([x.model_dump() for x in self._island_leading_influencing_elements]).drop_duplicates()

    @property
    def island_leading_remedial_actions(self):
        return pd.DataFrame([x.model_dump() for x in self._island_leading_remedial_actions]).drop_duplicates()

    @property
    def threshold_if_identification(self):
        # Filtering rows where identification factor above threshold
        return self.flow_results_df[self.flow_results_df['if_identification'] >= self.parameters.threshold_identification_percent]

    @property
    def threshold_if_filtering(self):
        # Filtering rows where filtering factor above threshold
        return self.flow_results_df[self.flow_results_df['if_filtering'] >= self.parameters.threshold_filtering]

    @property
    def max_if_identification(self):
        # Getting maximum values for each assessed and influencing element combination for identification factor
        max_if_identification_values = self.flow_results_df.groupby(['name', 'influencing_name'])['if_identification_max'].transform('max')
        return self.flow_results_df[self.flow_results_df['if_identification_max'] == max_if_identification_values]

    @property
    def max_if_filtering(self):
        # Getting maximum values for each assessed and influencing element combination for filtering factor
        max_if_filtering_values = self.flow_results_df.groupby(['name', 'influencing_name'])['if_filtering_max'].transform('max')
        return self.flow_results_df[self.flow_results_df['if_filtering_max'] == max_if_filtering_values]

    @property
    def max_if_voltage(self):
        # Getting maximum values for each assessed and influencing element combination for voltage factor
        max_if_voltage_values = self.voltage_results_df.groupby(['name', 'influencing_name'])['if_voltage'].transform('max')
        return self.voltage_results_df[self.voltage_results_df['if_voltage'] == max_if_voltage_values]

    @property
    def disconnected_assessed_elements(self):
        # TODO not working correctly, because it reports assessed elements as disconected if the same element contingency applied
        mask_t1 = self.flow_results_df.connected1 == False
        mask_t2 = (self.flow_results_df.connected2 == False) | (self.flow_results_df.connected2.isna())
        df = self.flow_results_df[mask_t1 & mask_t2]

        return df.drop_duplicates('name')

    def max_influence_factors_by_xnec(self,
                                      threshold: int | None = None,
                                      if_column_key: str = 'if_filtering_max') -> pd.DataFrame | None:

        if not threshold:
            threshold = self.parameters.threshold_filtering

        if self.flow_results_df is None:
            logger.warning(f"Flow influence factor results not available")
            return None
        # Getting maximum values for each XNEC combination for flow influence factors
        self.flow_results_df.index.name = 'id'
        df = self.flow_results_df.reset_index()
        max_if = df.groupby(['name', 'influencing_name'])[if_column_key].transform('max')
        df = df[df[if_column_key] == max_if].dropna(subset=[if_column_key])

        # Filtering by threshold
        if threshold:
            df = df[df[if_column_key] >= threshold]

        return df.set_index('id')

    def consistency_check_by_element_connectivity(self):
        """
        Workflow is as follows:
         - Find all elements in network model
         - Check their terminals connected status
         - Filter our disconnected elements where all connected terminal is False
         - Split original input data list into two disconnected_* and available_* dataframes
         - Log out to stream out of service elements

        Where contingency consists of multiple elements (exceptional or outofrange) it still can be simulated for
        impact assessment even if one element is already disconnected in network model

        """
        logger.info(f"Running consistency check of input data")
        # Consistency check of contingency elements
        if self.contingencies is not None:
            contingencies = self.contingencies_df.explode('equipment')
            elements = get_network_elements_by_ids(self.network, id=contingencies.equipment)
            connected_flags = elements.loc[:, elements.columns.str.contains('connected')]
            disconnected_elements = elements[~connected_flags.all(axis=1)]
            self.disconnected_contingency_elements = contingencies[contingencies.equipment.isin(disconnected_elements.index)]
            available_contingencies = pd.concat([contingencies, self.disconnected_contingencies]).drop_duplicates(keep=False)
            available_contingencies = available_contingencies.groupby('mRID').agg({'name': 'first', 'type': 'first', 'equipment': list}).reset_index()
            # BaseModel.model_construct is used to ignore validation step in increase performance (data is already validated in previous steps)
            self.available_contingencies = [Contingency.model_construct(**co) for co in available_contingencies.to_dict('records')]
            for _, row in self.disconnected_contingency_elements.iterrows():
                logger.warning(f"Element out of service of {row.type.lower()} contingency {row['name']}: {row.equipment}")

        # Consistency check of remedial action and its alterations
        # TODO most likely not needed as remedial actions can be not only to disconnect element
        # if self.remedial_actions is not None:
        #     elements = get_network_elements_by_ids(self.network, id=self.remedial_actions.grid_element_id)
        #     connected_flags = elements.loc[:, elements.columns.str.contains('connected')]
        #     disconnected_elements = elements[~connected_flags.all(axis=1)]
        #     self.disconnected_remedial_actions = self.remedial_actions[self.remedial_actions.grid_element_id.isin(disconnected_elements.index)]
        #     self.available_remedial_actions = pd.concat([self.remedial_actions, self.disconnected_remedial_actions]).drop_duplicates(keep=False)
        #     for _, row in self.disconnected_remedial_actions.iterrows():
        #         logger.warning(f"Element out of service of {row.alt_type} of remedial action {row.ra_name}: {row.alt_name} [mrid: {row.grid_element_id}]")

        # Consistency check of influencing elements
        if self.influencing_elements is not None:
            influencing_elements = self.influencing_elements_df
            elements = get_network_elements_by_ids(self.network, id=influencing_elements.equipment)
            connected_flags = elements.loc[:, elements.columns.str.contains('connected')]
            disconnected_elements = elements[~connected_flags.all(axis=1)]
            self.disconnected_influencing_elements = influencing_elements[influencing_elements.equipment.isin(disconnected_elements.index)]
            available_influencing_elements = pd.concat([influencing_elements, self.disconnected_influencing_elements]).drop_duplicates(keep=False)
            # BaseModel.model_construct is used to ignore validation step in increase performance (data is already validated in previous steps)
            self.available_influencing_elements = [InfluencingElement.model_construct(**ia) for ia in available_influencing_elements.to_dict('records')]
            for _, row in self.disconnected_influencing_elements.iterrows():
                logger.warning(f"Element out of service of influencing element {row['name']}: {row.equipment}")

    def get_limits(self):
        limits = self.network.get_operational_limits()
        limits = limits[limits["name"] == "permanent_limit"].copy()
        # Exctract element side values from index
        idx = limits.index
        limits["element_id"] = idx.get_level_values("element_id")
        limits["side"] = idx.get_level_values("side")
        limits["type"] = idx.get_level_values("type")

        limits = limits[["element_id", "side", "type", "value"]]
        limits = limits.set_index(["element_id"]).sort_index()
        limits_by_value = limits.pivot(columns=['side'])['value'].rename(
            columns={'NONE': 'limit', 'ONE': 'limit_t1', 'TWO': 'limit_t2', 'THREE': 'limit_t3'})
        limits_by_type = limits.pivot(columns=['side'])['type'].rename(
            columns={'NONE': 'limit_type', 'ONE': 'limit_type_t1', 'TWO': 'limit_type_t2', 'THREE': 'limit_type_t3'})
        limits = pd.concat([limits_by_value, limits_by_type], axis=1)
        # In case if one terminal elements does not have any limits defined in network model
        if "limit" in limits.columns and "limit_type" in limits.columns:
            limits["limit_t1"] = limits.get("limit_t1").combine_first(limits["limit"])
            limits["limit_type_t1"] = limits.get("limit_type_t1").combine_first(limits["limit_type"])
            limits = limits.drop(columns=[c for c in ("limit", "limit_type") if c in limits.columns])

        return limits

    def get_elements_flow(self, ids: List[str]):
        elements = get_network_elements_by_ids(network=self.network, id=ids)
        elements = elements[elements.columns[elements.columns.isin(self.ELEMENTS_COLS)]]  # takes only found ones

        # Merging p and connected columns of single port elements into p1 and connected1
        for colname in ['p', 'i', 'connected']:
            if colname in elements.columns:
                if not f"{colname}1" in elements.columns:
                    elements[f"{colname}1"] = np.nan
                elements[f"{colname}1"] = elements[f"{colname}1"].combine_first(elements[colname])
                elements = elements.drop(colname, axis=1)

        # Merging multiple CGMES.regionName columns into single one
        # region_column_keys = elements.columns[elements.columns.str.contains('CGMES.regionName')]
        # if not 'CGMES.regionName' in region_column_keys:  # in case if single terminal elements were not in returned elements
        #     elements['CGMES.regionName'] = np.nan
        # elements['CGMES.regionName'].update(elements.pop('CGMES.regionName1'))
        # elements['CGMES.regionName'].update(elements.pop('CGMES.regionName2'))

        if not 'country' in elements.columns:
            elements['country'] = np.nan
        for colname in ['country1', 'country2']:
            if colname in elements.columns:
                elements['country'] = elements['country'].combine_first(elements[colname])
                elements = elements.drop(colname, axis=1)

        return elements

    def get_assessed_nodes_voltage(self, assessed_nodes: pd.DataFrame):
        # elements = get_network_elements(network=self.network, element_type=pp.network.ElementType.BUS, id=assessed_nodes.index)
        elements = get_network_elements(network=self.network, element_type=pp.network.ElementType.BUS)
        elements = elements[elements.voltage_level_id.isin(assessed_nodes.index)]
        elements = elements[self.NODES_COLS]

        return elements

    def disconnect_contingency(self, contingency: Contingency) -> bool:
        # TODO question whether to continue to calculate exceptional contingency if one of the elements already is disconnected in network model
        responses = []
        for id in contingency.equipment:
            # Disconnect
            logger.info(f"Disconnecting element of {contingency.name} contingency: {id}")
            response = self.network.disconnect(id)
            logger.debug(f"Pypowsybl response: {response}")
            responses.append(response)

        if not all(responses):
            # Connect back contingency elements
            logger.info("Not possible to apply contingency, continue to next")
            response = self.connect_contingency(contingency=contingency)
            self._not_applied_contingencies.append(contingency)
            return False

        # Solve loadflow
        result = self.solve_loadflow()

        ## Check for divergence
        if result[0].status.value != 0:  # status.value equals 0 means converged
            logger.warning("Case diverged after contingency, continue to next")
            response = self.connect_contingency(contingency=contingency)  # connect back contingency element
            self._diverged_contingencies.append(contingency)
            return False

        ## Check for new additional islands (connected components count)
        if len(result) > len(self.initial_network_components.keys()):
            logger.warning(f"Contingency leads to number of additional islands in network: {len(result) - len(self.initial_network_components.keys())}")
            self._island_leading_contingencies.append(contingency)
            if self.parameters.ignore_emerged_islands:
                return True
            else:
                response = self.connect_contingency(contingency=contingency)  # connect back contingency element
                return False

        return True

    def connect_contingency(self, contingency: Contingency) -> bool:
        responses = []
        for id in contingency.equipment:
            logger.debug(f"Connecting element of {contingency.name} contingency: {id}")
            response = self.network.connect(id)
            logger.debug(f"Pypowsybl response: {response}")
            responses.append(response)

        result = self.solve_loadflow(suspend_output=True)

        return all(responses)

    def disconnect_influencing_element(self, influencing_element: InfluencingElement) -> bool:
        logger.info(f"Disconnecting influencing element: {influencing_element.name} [mrid: {influencing_element.equipment}]")
        response = self.network.disconnect(influencing_element.equipment)
        logger.debug(f"Pypowsybl response: {response}")

        if not response:
            logger.warning("Not possible to disconnect influencing element, continue to next")
            self._not_applied_influencing_elements.append(influencing_element)
            return False

        # Solve loadflow
        result = self.solve_loadflow()

        ## Check for divergence
        if result[0].status.value != 0:  # status.value equals 0 means converged
            logger.warning("Case diverged after disconnection of influencing element, continue to next")
            self._diverged_influencing_elements.append(influencing_element)
            return False

        ## Check for new additional islands (connected components count)
        if len(result) > len(self.initial_network_components.keys()):
            logger.warning(f"Disconnection of influencing element leads to number of additional islands in network: {len(result) - len(self.initial_network_components.keys())}")
            self._island_leading_influencing_elements.append(influencing_element)
            if self.parameters.ignore_emerged_islands:
                return True
            else:
                return False

        return True

    def apply_remedial_action(self, remedial_action: GridSateAlterationRemedialAction | PowerRemedialAction) -> bool:

        _available_alteration_actions = {'ShuntCompensatorModification': self.apply_shunt_compensator_modification,
                                         'TopologyAction': self.apply_topology_action,
                                         'RotatingMachineAction': self.apply_rotating_machine_action,
                                         }

        logger.info(f"Applying remedial action: {remedial_action.name}")

        # TODO add support for power remedial actions
        responses = []
        for alteration in remedial_action.alteration:
            # Apply alteration of remedial action
            alteration_action = _available_alteration_actions.get(alteration.type)
            response = alteration_action(alteration=alteration)
            responses.append(response)

        if not all(responses):
            logger.error("Not possible to apply remedial action, continue to next")
            self._not_applied_remedial_actions.append(remedial_action)
            return False

        # Solve loadflow
        result = self.solve_loadflow()

        ## Check for divergence
        if result[0].status.value != 0:  # status.value equals 0 means converged
            logger.error("Case diverged after remedial action, continue to next")
            self._diverged_remedial_actions.append(remedial_action)
            return False

        ## Check for new additional islands (connected components count)
        if len(result) > len(self.initial_network_components.keys()):
            logger.warning(f"Remedial action leads to number of additional islands in network: {len(result) - len(self.initial_network_components.keys())}")
            self._island_leading_remedial_actions.append(remedial_action)
            if self.parameters.ignore_emerged_islands:
                return True
            else:
                return False

        return True

    def apply_topology_action(self, alteration: GridSateAlteration):
        logger.info(f"Applying TopologyAction alteration: {alteration.name} [equipment mrid: {alteration.equipment}]")
        if alteration.property == 'ACDCTerminal.connected':
            logger.info(f"Updating property {alteration.property} to {alteration.value}")
            if alteration.value == 0:
                response = self.network.disconnect(alteration.equipment)
                logger.debug(f"Pypowsybl response: {response}")
            elif alteration.value == 1:
                response = self.network.connect(alteration.equipment)
                logger.debug(f"Pypowsybl response: {response}")
            else:
                raise Exception(f"Value of attribute 'normal_value' not supported: {alteration.value}")
        else:
            raise Exception(f"Property not supported: {alteration.property}")

        return response

    def apply_shunt_compensator_modification(self, alteration: GridSateAlteration):
        logger.info(f"Applying ShuntCompensatorModification alteration: {alteration.name} [equipment mrid: {alteration.equipment}]")
        if alteration.property == 'ShuntCompensator.sections':
            self.network.update_shunt_compensators(id=alteration.equipment,
                                                   section_count=int(alteration.value),
                                                   connected=True,
                                                   voltage_regulation_on=True)
        else:
            raise Exception(f"Property not supported: {alteration.property}")

    def apply_rotating_machine_action(self, alteration: GridSateAlteration):
        # TODO
        logger.info(f"Applying RotatingMachineAction alteration: {alteration.name} [equipment mrid: {alteration.equipment}]")

    def solve_loadflow(self, suspend_output: bool = False):
        """Method to solve AC loadflow with printing of results"""
        logger.debug(f"Solving load flow for network model variant: {self.network.get_working_variant_id()}")
        results = pp.loadflow.run_ac(network=self.network, parameters=self.lf_parameters)

        # If output not suspended - printing information about all network components results
        if not suspend_output:
            for result in results:
                if result.slack_bus_results:  # suspending output print for inactive islands (without slack generator)
                    result_dict = attr_to_dict(result)
                    logger.info(f"[COMPONENT {result_dict.get('connected_component_num')}] Load flow status: {result_dict.get('status').name}")
                    logger.debug(f"[COMPONENT {result_dict.get('connected_component_num')}] Load flow results: {result_dict}")

        return results

    def calculate_remedial_actions_influence(self, contingency: Contingency | None = None) -> pd.DataFrame | None:
        """
        Method to calculate single or multiple remedial actions impact at single contingency or base case

        Remedial actions to be calculated are given during ImpactAssessment class initiation
        """

        # Creating contingency object instance for base case
        if contingency is None:
            contingency = Contingency(mRID='BASE_CASE', name='BASE_CASE', type='BASE_CASE', equipment=[])

        # Get flow on xnes before applying remedial action
        logger.info(f"Parsing pre XNEs flows")
        assessed_elements = self.assessed_elements_df  # serializing to dataframe
        xnes_pre = self.get_elements_flow(ids=assessed_elements.equipment.to_list())
        xnes_pre.rename(columns={'p1': 'p1_pre', 'p2': 'p2_pre', 'i1': 'i1_pre', 'i2': 'i2_pre'}, inplace=True)
        xnes_pre = xnes_pre.merge(self.limits, right_index=True, left_index=True)

        # Iterating over each remedial action
        co_results = []  # results dataframe at some contingency or base case
        for remedial_action in self.remedial_actions:
            logger.info(f"Simulating remedial action: {remedial_action.name} [mrid: {remedial_action.mRID}]")
            # Check whether any of this remedial action alteration at this iteration already simulated as contingency
            # If yes then we skip it and continue to next
            # TODO this solution might need improvement if one remedial action has multiple alterations
            if contingency.type != 'BASE_CASE':  # skipping this parts if running for base case
                alteration_equipment_ids = [alteration.equipment for alteration in remedial_action.alteration]
                if any(item in contingency.equipment for item in alteration_equipment_ids):
                    logger.warning(f"Remedial action elements are already simulated as contingency, continue to next")
                    continue

            # Creating new working variant of the network model to apply remedial action
            self.network.clone_variant(src='InitialState', target=remedial_action.mRID)
            self.network.set_working_variant(remedial_action.mRID)
            logger.info(f"Network model working variant initialized and activated: {remedial_action.mRID}")

            # Apply remedial action
            response = self.apply_remedial_action(remedial_action=remedial_action)
            if not response:
                continue

            # Get flow on xnes after applying remedial action
            logger.info(f"Parsing post XNEs flows")
            xnes_post = self.get_elements_flow(ids=assessed_elements.equipment.to_list())
            xnes_post.rename(columns={'p1': 'p1_post', 'p2': 'p2_post', 'i1': 'i1_post', 'i2': 'i2_post'}, inplace=True)
            xnes_post['influencing_id'] = remedial_action.mRID
            xnes_post['influencing_name'] = remedial_action.name
            xnes_post['influencing_type'] = remedial_action.type
            co_results.append(xnes_post)
        else:  # when the loop is finished
            # Get back to initial state working variant
            self.network.set_working_variant('InitialState')

        # Check if any results of remedial action evaluation is available at current iteration
        if not co_results:
            logger.warning(f"There is no results available for contingency: {contingency.name} [mrid: {contingency.mRID}]")
            return

        co_results_df = pd.concat(co_results)

        # Amend flows of xnes before applying remedial action
        co_results_df = co_results_df.merge(xnes_pre[['p1_pre', 'p2_pre', 'i1_pre', 'i2_pre', 'limit_t1', 'limit_t2', 'limit_type_t1', 'limit_type_t2']],
                                            right_index=True, left_index=True)
        co_results_df['contingency_id'] = contingency.mRID
        co_results_df['contingency_name'] = contingency.name

        return co_results_df

    @performance_counter(units='seconds')
    def run_remedial_actions_flow_influence_assessment(self):
        """Method to calculate remedial actions influence factors"""
        self._diverged_contingencies = []  # removing diverged contingencies status from other runs
        self._diverged_remedial_actions = []  # removing diverged remedial actions status from other runs
        results = []  # global results dataframe

        # Calculating for base case
        logger.info(f"Calculating remedial actions influence at: BASE_CASE")
        bc_results_df = self.calculate_remedial_actions_influence(contingency=None)  # None is understood as base case - no contingency
        if bc_results_df is None:
            logger.error(f"Contingencies will not be simulated further due to unavailability to simulate BASE_CASE")
            return  # exit calculation with without setting results to self
        results.append(bc_results_df)

        # Iterating over each contingency
        for contingency in self.available_contingencies:
            logger.info(f"Calculating remedial actions influence at contingency: {contingency.name}")

            # Disconnect contingency element(s)
            response = self.disconnect_contingency(contingency=contingency)
            if not response:
                continue

            # Calculating remedial actions influence at current contingency iteration
            co_results_df = self.calculate_remedial_actions_influence(contingency=contingency)
            if co_results_df is None:
                continue
            results.append(co_results_df)

            # Connect back contingency element
            response = self.connect_contingency(contingency=contingency)

        # Make global results dataframe
        results_df = pd.concat(results)

        # Calculate influence factors in percent
        ## Checking if limit is in current then uses i1/i2 (current) values, if not - p1/p2 (active power)
        # TODO might some cases then apparent power limits, then would need to calculate S but currently not the case for baltics
        results_df['if_filtering1'] = np.where(results_df.limit_type_t1 == 'CURRENT',
                                     abs((results_df['i1_post'] - results_df['i1_pre']) / results_df['limit_t1']) * 100,
                                     abs((results_df['p1_post'] - results_df['p1_pre']) / results_df['limit_t1']) * 100)
        results_df['if_filtering2'] = np.where(results_df.limit_type_t1 == 'CURRENT',
                                     abs((results_df['i2_post'] - results_df['i2_pre']) / results_df['limit_t2']) * 100,
                                     abs((results_df['p2_post'] - results_df['p2_pre']) / results_df['limit_t2']) * 100)
        results_df['if_filtering_max'] = results_df[['if_filtering1', 'if_filtering2']].max(axis=1)
        results_df['if_filtering_mean'] = results_df[['if_filtering1', 'if_filtering2']].mean(axis=1)

        results_df.sort_values(by=['name', 'influencing_name', 'contingency_name'], inplace=True)
        self.flow_results_df = results_df

    @performance_counter(units='seconds')
    def run_outage_flow_influence_assessment(self):
        """Method to calculate identification and filtering influence factors an outage of influencing element"""
        # Iterating over each contingency
        self._diverged_contingencies = []  # removing diverged contingencies status from other runs
        self._diverged_influencing_elements = []  # removing diverged influencing elements status from other runs
        results = []  # global results dataframe

        # Serializing assessed elements
        assessed_elements = self.assessed_elements_df

        # Iterating over each contingency
        for contingency in self.available_contingencies:

            # Disconnect contingency element(s)
            response = self.disconnect_contingency(contingency=contingency)
            if not response:
                continue

            # Get flow on assessed elements before disconnecting influencing element (r)
            logger.info(f"Parsing pre assessed element flows")
            assessed_elements_pre = self.get_elements_flow(ids=assessed_elements.equipment.to_list())
            assessed_elements_pre.rename(columns={'p1': 'p1_pre', 'p2': 'p2_pre', 'i1': 'i1_pre', 'i2': 'i2_pre'}, inplace=True)
            assessed_elements_pre = assessed_elements_pre.merge(self.limits, right_index=True, left_index=True)

            # Get flow on influencing elements (r) before disconnecting it
            influencing_elements_ids = [x.equipment for x in self.available_influencing_elements]
            influencing_elements_pre = self.get_elements_flow(ids=influencing_elements_ids)
            influencing_elements_pre = influencing_elements_pre.merge(self.limits, right_index=True, left_index=True)
            influencing_elements_pre = influencing_elements_pre.add_prefix('influencing_', axis=1)

            # Iterating over each influencing element
            co_results = []  # results dataframe at some contingency
            for influencing_element in self.available_influencing_elements:
                # Check whether equipment of influencing element at this iteration already was simulated as contingency
                # If yes then we skip it and continue to next
                if influencing_element.equipment in contingency.equipment:
                    logger.warning(f"Influencing element is already simulated as contingency, continue to next")
                    continue

                # Creating new working variant of the network model to apply influencing element
                self.network.clone_variant(src='InitialState', target=influencing_element.mRID)
                self.network.set_working_variant(influencing_element.mRID)
                logger.info(f"Network model working variant initialized and activated: {influencing_element.mRID}")

                # Disconnect each influencing element (r)
                response = self.disconnect_influencing_element(influencing_element=influencing_element)
                if not response:
                    continue

                # Get flow on assessed elements after disconnecting influencing element (r)
                logger.info(f"Parsing post assessed element flows")
                assessed_elements_post = self.get_elements_flow(ids=assessed_elements.equipment.to_list())
                assessed_elements_post.rename(columns={'p1': 'p1_post', 'p2': 'p2_post', 'i1': 'i1_post', 'i2': 'i2_post'}, inplace=True)
                assessed_elements_post['influencing_id'] = influencing_element.equipment
                co_results.append(assessed_elements_post)

            else:  # when the loop is finished
                # Get back to initial state working variant
                self.network.set_working_variant('InitialState')

            # Check if any results of influencing element evaluation is available at current contingency iteration
            if not co_results:
                logger.warning(f"There is no results available for contingency: {contingency.name} [mrid: {contingency.mRID}]")
                continue

            co_results_df = pd.concat(co_results)

            # Amend flows of assessed elements (t) before disconnection of (r)
            co_results_df = co_results_df.merge(
                assessed_elements_pre[['p1_pre', 'p2_pre', 'i1_pre', 'i2_pre', 'limit_t1', 'limit_t2', 'limit_type_t1', 'limit_type_t2']],
                right_index=True, left_index=True)
            # Amend flows of influencing elements (r) before disconnection
            co_results_df = co_results_df.merge(influencing_elements_pre, left_on='influencing_id', right_index=True)
            co_results_df['contingency_id'] = contingency.mRID
            co_results_df['contingency_name'] = contingency.name
            results.append(co_results_df)

            # Connect back contingency element
            response = self.connect_contingency(contingency=contingency)

        # Check if any results of this calculation is available
        if not results:
            logger.warning(f"There is no outage flow influence assessment results")
            return  # exit calculation with without setting results to self

        # Make global results dataframe
        results_df = pd.concat(results)

        # Calculate influence factors in percent
        results_df['if_filtering1'] = abs((results_df['i1_post'] - results_df['i1_pre']) / results_df['influencing_i1']) * 100
        results_df['if_filtering2'] = abs((results_df['i2_post'] - results_df['i2_pre']) / results_df['influencing_i2']) * 100
        results_df['if_filtering_max'] = results_df[['if_filtering1', 'if_filtering2']].max(axis=1)
        results_df['if_filtering_mean'] = results_df[['if_filtering1', 'if_filtering2']].mean(axis=1)
        results_df['if_identification1'] = results_df['if_filtering1'] * (results_df['influencing_limit_t1'] / results_df['limit_t1'])
        results_df['if_identification2'] = results_df['if_filtering2'] * (results_df['influencing_limit_t2'] / results_df['limit_t2'])
        results_df['if_identification_max'] = results_df[['if_identification1', 'if_identification2']].max(axis=1)
        results_df['if_identification_mean'] = results_df[['if_identification1', 'if_identification2']].mean(axis=1)

        results_df.sort_values(by=['name', 'influencing_name', 'contingency_name'], inplace=True)
        self.flow_results_df = results_df

    @performance_counter(units='seconds')
    def run_outage_voltage_influence_assessment(self):
        """Method to calculate voltage influence factors simulating an outage of influencing element"""
        # Iterating over each contingency
        self._diverged_contingencies = []  # removing diverged contingencies status from other runs
        self._diverged_influencing_elements = []  # removing diverged influencing elements status from other runs
        results = []  # global results dataframe

        # Iterating over each contingency
        for contingency in self.available_contingencies:

            # Disconnect contingency element(s)
            response = self.disconnect_contingency(contingency=contingency)
            if not response:
                continue

            # Get voltage on assessed elements before disconnecting influencing element (r)
            assessed_nodes_pre = self.get_assessed_nodes_voltage(assessed_nodes=self.assessed_nodes)
            assessed_nodes_pre.rename(columns={'v_mag': 'v_mag_pre'}, inplace=True)

            # Iterating over each influencing element
            co_results = []  # results dataframe at some contingency
            for influencing_element in self.available_influencing_elements:
                # Check whether equipment of influencing element at this iteration already was simulated as contingency
                # If yes then we skip it and continue to next
                if influencing_element.equipment in contingency.equipment:
                    logger.warning(f"Influencing element is already simulated as contingency, continue to next")
                    continue

                # Creating new working variant of the network model to apply influencing element
                self.network.clone_variant(src='InitialState', target=influencing_element.mRID)
                self.network.set_working_variant(influencing_element.mRID)
                logger.info(f"Network model working variant initialized and activated: {influencing_element.mRID}")

                # Disconnect each influencing element (r)
                response = self.disconnect_influencing_element(influencing_element=influencing_element)
                if not response:
                    continue

                # Get voltage on assessed elements after disconnecting influencing element (r)
                assessed_nodes_post = self.get_assessed_nodes_voltage(assessed_nodes=self.assessed_nodes)
                assessed_nodes_post.rename(columns={'v_mag': 'v_mag_post'}, inplace=True)
                assessed_nodes_post['influencing_id'] = influencing_element.equipment
                assessed_nodes_post['influencing_name'] = influencing_element.name
                co_results.append(assessed_nodes_post)

            else:  # when the loop is finished
                # Get back to initial state working variant
                self.network.set_working_variant('InitialState')

            # Check if any results of influencing element evaluation is available at current contingency iteration
            if not co_results:
                logger.warning(f"There is no results available for contingency: {contingency.name} [mrid: {contingency.mRID}]")
                continue

            co_results_df = pd.concat(co_results)

            # Amend voltages of assessed elements (t) before disconnection of (r)
            co_results_df = co_results_df.merge(assessed_nodes_pre[['v_mag_pre']], right_index=True, left_index=True)
            co_results_df['contingency_id'] = contingency.mRID
            co_results_df['contingency_name'] = contingency.name
            results.append(co_results_df)

            # Connect back contingency element
            response = self.connect_contingency(contingency=contingency)

        # Check if any results of this calculation is available
        if not results:
            logger.warning(f"There is no outage voltage influence assessment results")
            return  # exit calculation with without setting results to self

        # Make global results dataframe
        results_df = pd.concat(results)

        # Calculate voltage influence factor
        results_df['if_voltage'] = abs((results_df['v_mag_post'] - results_df['v_mag_pre']) / results_df['nominal_v']) * 100
        results_df.sort_values(by=['country', 'substation_name', 'name', 'influencing_name', 'contingency_name'], inplace=True)
        self.voltage_results_df = results_df


if __name__ == '__main__':
    # Testing pypowsybl impact assessment
    # Load network model
    model = r"C:\Users\lukas.navickas\Documents\Models\RMM_1D_001_20260526T1530Z_BA_2b4ad995-a536-4644-8b71-83d95666d0aa.zip"
    network = pp.network.load(model, parameters={"iidm.import.cgmes.source-for-iidm-id": "rdfID",
                                                 "iidm.import.cgmes.import-node-breaker-as-bus-breaker": 'True'})

    # Getting input data
    # from general import input_data_retriever
    DEFAULT_COMMON_ASSESSED_ELEMENT_LIST = r"C:\Users\lukas.navickas\Documents\projekts\sa\crosa\common_assessed_element_list.xlsx"
    DEFAULT_COMMON_CONTINGENCY_LIST = r"C:\Users\lukas.navickas\Documents\projekts\sa\crosa\common_contingency_list.xlsx"
    DEFAULT_COMMON_REMEDIAL_ACTION_LIST = r"C:\Users\lukas.navickas\Documents\projekts\sa\crosa\common_remedial_action_list.xlsx"

    # Getting assessed elements
    ## From BMS
    # assessed_elements = input_data_retriever.get_assessed_elements(period_start="2023-12-18 10:00",
    #                                                                period_end="2023-12-18 11:00")
    ## From default Excel file
    assessed_elements_df = pd.read_excel(DEFAULT_COMMON_ASSESSED_ELEMENT_LIST)
    assessed_elements_df_filtered = assessed_elements_df[assessed_elements_df.secured_for_region == 'PL']
    assessed_elements = converters.assessed_elements_from_dataframe(data=assessed_elements_df_filtered)
    # assessed_nodes = get_network_elements(network=network, element_type=pp.network.ElementType.BUS)
    # assessed_nodes = assessed_nodes[assessed_nodes.nominal_v >= 330]
    voltage_levels = network.get_voltage_levels(all_attributes=True).rename(columns={"name": "voltage_level_name"})
    substations = network.get_substations(all_attributes=True).rename(columns={"name": "substation_name"})
    assessed_voltage_levels = voltage_levels.merge(substations[['country', 'substation_name']], left_on='substation_id', right_index=True)
    assessed_voltage_levels = assessed_voltage_levels[assessed_voltage_levels.nominal_v >= 330]

    # Getting contingencies
    ## From BMS
    # contingency_elements = input_data_retriever.get_contingencies(period_start="2023-12-18 10:00",
    #                                                               period_end="2023-12-18 11:00")
    ## From default Excel file
    contingency_df = pd.read_excel(DEFAULT_COMMON_CONTINGENCY_LIST)
    # contingency_df_filtered = contingency_df[contingency_df.area.isin(['LT', 'LV'])].sample(5)
    # contingency_df_filtered = contingency_df[contingency_df.area.isin(['LT', 'LV', 'EE'])]
    contingency_df_filtered = contingency_df[contingency_df.area.isin(['LT', 'PL'])]
    # contingency_df_filtered = contingency_df[contingency_df.co_name.isin(['OCO_LN425'])]
    contingencies = converters.contingencies_from_dataframe(data=contingency_df_filtered)

    # Getting remedial actions
    remedial_action_df = pd.read_excel(DEFAULT_COMMON_REMEDIAL_ACTION_LIST)
    remedial_action_df_filtered = remedial_action_df[remedial_action_df.ra_name.isin(['RA_LN328_OUT', 'RA_AT-2_GROBINA_IN'])]
    remedial_actions = converters.remedial_actions_from_dataframe(data=remedial_action_df_filtered)

    # Selecting influencing elements
    influencing_elements_df_filtered = assessed_elements_df[assessed_elements_df.secured_for_region == 'LT']
    influencing_elements = converters.influencing_elements_from_dataframe(data=influencing_elements_df_filtered)

    ia = ImpactAssessment(network=network,
                          contingencies=contingencies,
                          influencing_elements=influencing_elements,
                          assessed_elements=assessed_elements,
                          assessed_nodes=assessed_voltage_levels[assessed_voltage_levels['country'] == 'PL'],
                          remedial_actions=None,
                          )

    ia.run_outage_flow_influence_assessment()
    # ia.run_outage_voltage_influence_assessment()
    # ia.run_remedial_actions_flow_influence_assessment()
    # ia.max_influence_factors_by_xnec()

    # Report diverged/not applied contingencies and influencing elements
    print(ia.diverged_contingencies)
    print(ia.not_applied_contingencies)
    print(ia.diverged_influencing_elements)
    print(ia.not_applied_influencing_elements)
    print(ia.island_leading_contingencies)
    print(ia.island_leading_influencing_elements)

    # Exporting to excel
    ia.flow_results_df.to_excel("test_if.xlsx")
    # ia.voltage_results_df.to_excel("test_voltage_if.xlsx")
