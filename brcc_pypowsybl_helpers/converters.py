import logging
from triplets import rdf_parser
from typing import List
import pandas as pd
from brcc_pypowsybl_helpers.models import (Contingency, AssessedElement, GridSateAlterationRemedialAction,
                                    PowerRemedialAction, InfluencingElement, GridSateAlteration,
                                    OperatorStrategyAction, OperatorStrategy)
# from nc_data_exchange.profiles.DocumentHeader import FullModel

# Start logger
logger = logging.getLogger(__name__)


def get_profile_header_objects(data: pd.DataFrame):
    # Parse and store parsed profile header metadata
    headers = data.type_tableview('FullModel', string_to_number=False)
    headers.columns = headers.columns.map(lambda x: x.split(".")[-1])
    headers.identifier = headers.identifier.map(lambda x: x.split(":")[-1])
    # header_objects = [FullModel(**profile_meta, profile_name='Contingency') for profile_meta in headers.to_dict('records')]

    return headers.to_dict('records')


def contingencies_from_network_code_xml(cimxml_paths: List[str]) -> List[Contingency]:
    """Converts NC Contingency profile [CO] to contingencies format for security analysis input"""
    converted_contingencies = []

    # Load cimxml profiles
    data = rdf_parser.load_all_to_dataframe(cimxml_paths)

    logger.info(f"Converting from NetworkCode Contingency profiles")

    # Get all contingency classes
    classes = ['OrdinaryContingency', 'ExceptionalContingency', 'OutOfRangeContingency']
    contingencies = pd.concat([data.type_tableview(cls, string_to_number=False) for cls in classes])

    # Filter out contingencies where attribute normalMustStudy == 'false'
    contingencies = contingencies[contingencies['Contingency.normalMustStudy'] == 'true']

    for id, contingency in contingencies.iterrows():
        # Find referenced ContingencyEquipment classes
        referenced_coeq = data.references_to_simple(id).query("Type == 'ContingencyEquipment'").reset_index()
        contingency_equipments = data.type_tableview('ContingencyEquipment', string_to_number=False).query("ID in @referenced_coeq.ID_FROM")

        # Build Contingency instances
        contingency.index = contingency.index.map(lambda x: x.split(".")[-1])
        co_instance = Contingency(**contingency,
                                  equipment=contingency_equipments['ContingencyEquipment.Equipment'].to_list())
        converted_contingencies.append(co_instance)

    # Get header data
    # header_objects = get_profile_header_objects(data=data)  # TODO

    return converted_contingencies


def contingencies_from_dataframe(data: str | pd.DataFrame) -> List[Contingency]:
    """
    Converts Contingency Excel or Dataframe file to contingencies format for security analysis input

    Dataframe should have columns:
    - mRID or registered_resource
    - equipment or grid_element_id
    - name or co_name
    """

    # Load Excel file if input was path to file
    if isinstance(data, str):
        logger.info(f"Parsing file: {data}")
        contingencies = pd.read_excel(data)
    else:
        contingencies = data

    logger.info(f"Converting Contingencies from dataframe")

    # Rename the columns if they are present
    column_name_map = {'registered_resource': 'mRID', 'grid_element_id': 'equipment', 'co_name': 'name'}
    relevant_mapping = {k: v for k, v in column_name_map.items() if k in contingencies.columns}
    contingencies = contingencies.rename(columns=relevant_mapping)

    # Filter out contingencies where grid_element_id is missing
    contingencies = contingencies.dropna(subset='equipment')

    # Filter out contingencies where attribute normalMustStudy == 'false' if columns is in dataframe
    if 'normal_must_study' in contingencies.columns:
        contingencies = contingencies[contingencies.normal_must_study == True]

    # Combine contingency equipments grid_element_id to lists
    aggregated_dict = contingencies.groupby('mRID').agg({'equipment': list,
                                                         'type': 'first',
                                                         'name': 'first'}).reset_index().to_dict('records')

    # Build Contingency instances
    converted_contingencies = [Contingency(**contingency) for contingency in aggregated_dict]

    return converted_contingencies


def assessed_elements_from_network_code_xml(cimxml_paths: List[str]) -> List[AssessedElement]:
    """Converts NC AssessedElement [AE] profile to assessed elements format for security analysis input"""
    converted_assessed_elements = []

    # Load cimxml profiles
    data = rdf_parser.load_all_to_dataframe(cimxml_paths)

    logger.info(f"Converting from NetworkCode AssessedElement profiles")

    # Find all assessed elements
    assessed_elements = data.type_tableview('AssessedElement', string_to_number=False)

    # Remove class names from keys in columns
    assessed_elements.columns = assessed_elements.columns.map(lambda x: x.split(".")[-1])

    # Filter out assessed elements where attribute normalEnabled == 'false' if this attribute present
    if 'normalEnabled' in assessed_elements.columns:
        assessed_elements = assessed_elements[assessed_elements['normalEnabled'] == 'true']

    # Iterate over each AssessedElement
    for id, assessed_element in assessed_elements.iterrows():
        # Find assessed element linkage with contingencies and remedial actions
        referred_by = data.references_simple(assessed_element.mRID)
        contingency = data.query("ID in @referred_by.ID & KEY == 'AssessedElementWithContingency.Contingency'").VALUE
        remedial_action = data.query("ID in @referred_by.ID & KEY == 'AssessedElementWithRemedialAction.RemedialAction'").VALUE

        # Build AssessedElement instances
        ae_instance = AssessedElement(**assessed_element,
                                      contingency=contingency.to_list(),
                                      remedial_action=remedial_action.to_list())
        converted_assessed_elements.append(ae_instance)

    # Get header data
    # header_objects = get_profile_header_objects(data=data)  # TODO

    return converted_assessed_elements


def influencing_elements_from_dataframe(data: str | pd.DataFrame) -> List[InfluencingElement]:
    """
    Converts Assessed elements Excel or Dataframe file to influencing elements format for impact assessment input

    Dataframe should have columns:
    - mRID or registered_resource
    - equipment or grid_element_id
    """

    # Load Excel file if input was path to file
    if isinstance(data, str):
        logger.info(f"Parsing file: {data}")
        influencing_elements = pd.read_excel(data)
    else:
        influencing_elements = data

    logger.info(f"Converting Influencing elements from dataframe")

    # Rename the columns if they are present
    column_name_map = {'registered_resource': 'mRID', 'grid_element_id': 'equipment'}
    relevant_mapping = {k: v for k, v in column_name_map.items() if k in influencing_elements.columns}
    influencing_elements = influencing_elements.rename(columns=relevant_mapping)

    # Filter out influencing elements where grid_element_id is missing
    influencing_elements = influencing_elements.dropna(subset='equipment')

    # Filter out influencing elements where attribute normal_enabled == 'false' if columns is in dataframe
    if 'normal_enabled' in influencing_elements.columns:
        influencing_elements = influencing_elements[influencing_elements.normal_enabled == True]

    # Build InfluencingElement instances
    converted_influencing_elements = [InfluencingElement(**influencing_element) for influencing_element in influencing_elements.to_dict('records')]

    return converted_influencing_elements


def assessed_elements_from_dataframe(data: str | pd.DataFrame) -> List[AssessedElement]:
    """
    Converts Assessed elements Excel or Dataframe file to assessed elements format for security analysis input

    Dataframe should have columns:
    - mRID or registered_resource
    - equipment or grid_element_id
    """

    # Load Excel file if input was path to file
    if isinstance(data, str):
        logger.info(f"Parsing file: {data}")
        assessed_elements = pd.read_excel(data)
    else:
        assessed_elements = data

    logger.info(f"Converting Assessed elements from dataframe")

    # Rename the columns if they are present
    column_name_map = {'registered_resource': 'mRID', 'grid_element_id': 'equipment'}
    relevant_mapping = {k: v for k, v in column_name_map.items() if k in assessed_elements.columns}
    assessed_elements = assessed_elements.rename(columns=relevant_mapping)

    # Filter out assessed elements where equipment is missing
    assessed_elements = assessed_elements.dropna(subset='equipment')

    # Filter out assessed elements where attribute normal_enabled == 'false' if columns is in dataframe
    if 'normal_enabled' in assessed_elements.columns:
        assessed_elements = assessed_elements[assessed_elements.normal_enabled == True]

    # Build AssessedElement instances
    converted_assessed_elements = [AssessedElement(**assessed_element) for assessed_element in assessed_elements.to_dict('records')]

    return converted_assessed_elements


# TODO WIP
def remedial_actions_from_network_code_xml(cimxml_paths: List[str]) -> List[GridSateAlterationRemedialAction | PowerRemedialAction]:
    """Converts NC RemedialAction profile to remedial actions format for security analysis input"""
    converted_remedial_actions = []

    # Load cimxml profiles
    data = rdf_parser.load_all_to_dataframe(cimxml_paths)

    logger.info(f"Converting from NetworkCode RemedialAction profiles")

    # Get all remedial action classes
    classes = ['GridStateAlterationRemedialAction', 'CountertradeRemedialAction', 'RedispatchRemedialAction']
    remedial_actions = pd.concat([data.type_tableview(cls, string_to_number=False) for cls in classes])

    # Remove class names from keys in columns
    remedial_actions.columns = remedial_actions.columns.map(lambda x: x.split(".")[-1])

    # Filter out remedial actions where attribute normalAvailable == 'false'
    remedial_actions = remedial_actions[remedial_actions['normalAvailable'] == 'true']

    for id, remedial_action in remedial_actions.iterrows():
        # Find referenced GridStateAlteration classes
        referred_by = data.references_simple(id).query("ID_TO == @id")
        property = data.query("ID in @referred_by.ID & KEY == 'GridStateAlteration.PropertyReference'").VALUE
        equipment = None

        # alterations = data.type_tableview('ContingencyEquipment', string_to_number=False).query("ID in @referenced_coeq.ID_FROM")

        alterations = pd.concat([data.type_tableview(cls, string_to_number=False) for cls in referenced_alterations.ID_FROM])

        # for alteration_id in referenced_alterations.ID_FROM:
        #     alteration = data.get_object_data(alteration_id)

        # Build RemedialAction instances
        remedial_actions.index = remedial_actions.index.map(lambda x: x.split(".")[-1])
        ra_instance = GridSateAlterationRemedialAction(**remedial_action)
        converted_remedial_actions.append(ra_instance)

    return converted_remedial_actions


def remedial_actions_from_dataframe(data: str | pd.DataFrame) -> List[GridSateAlterationRemedialAction | PowerRemedialAction]:
    """
    Converts Remedial actions Excel or Dataframe file to RemedialAction elements format for loadflow analysis

    Dataframe should have columns:
    - mRID or registered_resource
    - equipment or grid_element_id for GridStateAlterationRemedialActions
    """

    # Load Excel file if input was path to file
    if isinstance(data, str):
        logger.info(f"Parsing file: {data}")
        remedial_actions = pd.read_excel(data)
    else:
        remedial_actions = data

    logger.info(f"Converting Remedial actions from dataframe")

    # Rename the columns if they are present
    column_name_map = {'registered_resource': 'mRID', 'grid_element_id': 'equipment', 'ra_name': 'name', 'connected_object_id': 'alt_mrid'}
    relevant_mapping = {k: v for k, v in column_name_map.items() if k in remedial_actions.columns}
    remedial_actions = remedial_actions.rename(columns=relevant_mapping)

    # Filter out GridStateAlteration type remedial actions where equipment is missing
    remedial_actions = remedial_actions[~(remedial_actions.equipment.isna() & (remedial_actions.type == 'GridStateAlterationRemedialAction'))]

    # Filter out remedial actions where attribute normal_available == 'false' if columns is in dataframe
    if 'normal_available' in remedial_actions.columns:
        remedial_actions = remedial_actions[remedial_actions.normal_available == True]

    # Build GridSateAlterationRemedialAction or PowerRemedialAction instances
    converted_remedial_actions = []
    for mrid, radf in remedial_actions.groupby('mRID'):
        # Validate that there is no multiple remedial actions with different types under same mRID
        if len(radf.type.unique()) > 1:
            raise Exception(f"mRID is used for multiple different type remedial actions: {radf.mRID.to_list()}")
        _ra_type = radf.type.unique().item()
        if _ra_type == 'GridStateAlterationRemedialAction':  # processing GridStateAlterationRemedialAction type
            alterations = [GridSateAlteration(**alteration) for alteration in radf.to_dict('records')]
            aggregated_ra_dict = radf.groupby('mRID').agg({'name': 'first', 'type': 'first'}).reset_index().to_dict('records')[0]
            remedial_action = GridSateAlterationRemedialAction(**aggregated_ra_dict, alteration=alterations)
        elif _ra_type in ['CountertradeRemedialAction', 'RedispatchRemedialAction']:  # processing PowerRemedialAction type
            ra_dict = radf.fillna("").to_dict('records')[0]
            remedial_action = PowerRemedialAction(**ra_dict)
        else:
            logger.error(f"Remedial action type not supported, continue to next: {_ra_type}")
            continue

        converted_remedial_actions.append(remedial_action)

    return converted_remedial_actions


def operator_strategies_from_dataframe(data: str | pd.DataFrame) -> List[OperatorStrategy]:
    # TODO WIP
    """
    Converts Operator strategy Excel or Dataframe file to OperatorStrategy elements format for loadflow analysis

    Dataframe should have columns:
    - equipment or grid_element_id for OperatorStrategyAction
    """

    # Load Excel file if input was path to file
    if isinstance(data, str):
        logger.info(f"Parsing file: {data}")
        operator_strategies = pd.read_excel(data)
    else:
        operator_strategies = data

    return None


if __name__ == '__main__':
    # Test conversion from Excel
    contingencies_path = r"C:\Users\martynas.karobcikas\Documents\python_projects\sa\nc_csa_profiles\workspace\common_contingency_list.xlsx"
    assessed_elements_path = r"C:\Users\martynas.karobcikas\Documents\python_projects\sa\nc_csa_profiles\workspace\common_assessed_element_list.xlsx"
    contingencies_from_excel = contingencies_from_dataframe(data=contingencies_path)
    assessed_elements_from_excel = assessed_elements_from_dataframe(data=assessed_elements_path)

    # Test conversion from NC profiles
    pass

    # Test conversion from BMS dataset
    from general.input_data_retriever import get_contingencies, get_assessed_elements, get_remedial_actions
    url = r"https://test-rcc-bht.elering.sise/"
    period_start = "2024-08-01 09:00"
    period_end = "2024-08-01 10:00"
    contingencies_data = get_contingencies(url=url, period_start=period_start, period_end=period_end)
    assessed_elements_data = get_assessed_elements(url=url, period_start=period_start, period_end=period_end)
    remedial_actions_data = get_remedial_actions(url=url, period_start=period_start, period_end=period_end)
    contingencies_from_bms = contingencies_from_dataframe(data=contingencies_data)
    assessed_elements_from_bms = assessed_elements_from_dataframe(data=assessed_elements_data)
    remedial_actions_from_bms = remedial_actions_from_dataframe(data=remedial_actions_data)
