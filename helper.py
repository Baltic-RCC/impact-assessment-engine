from uuid import uuid4
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import logging
import pypowsybl
import pandas as pd
import io
from typing import List
import time
import functools
from datetime import datetime

# Start logger
logger = logging.getLogger(__name__)


def performance_counter(units='seconds'):
    """Counts performance of the function"""
    def decorator_performance_counter(func):
        @functools.wraps(func)
        def wrapper_performance_counter(*args, **kwargs):
            start_time = time.perf_counter()
            response = func(*args, **kwargs)
            duration = round(time.perf_counter() - start_time, 2)
            if units == 'minutes':
                duration = round(duration / 60, 2)  # counting by minutes
            logger.info(f"Process {func.__name__!r} finished with duration: {duration} {units}", extra={'numeric_value': duration})
            return response
        return wrapper_performance_counter
    return decorator_performance_counter


def attr_to_dict(instance: object, sanitize_to_strings: bool = False):
    """
    Method to return class variables/attributes as dictionary
    Example: LimitViolation(subject_id='e49a61d1-632a-11ec-8166-00505691de36', subject_name='', limit_type=HIGH_VOLTAGE, limit=450.0, limit_name='', acceptable_duration=2147483647, limit_reduction=1.0, value=555.6890952917897, side=ONE)
    pypowsybl._pypowsybl.LimitViolation -> dict
    :param instance: class instance
    :param sanitize_to_strings: flag to convert attributes to string type
    :return: dict
    """

    def convert_value(value):
        if isinstance(value, datetime):
            return value.isoformat() if sanitize_to_strings else value
        elif isinstance(value, list):
            return [convert_value(item) for item in value]
        elif isinstance(value, dict):
            return {k: convert_value(v) for k, v in value.items()}
        elif hasattr(value, '__dict__'):
            return attr_to_dict(value, sanitize_to_strings)
        elif sanitize_to_strings:
            return str(value)
        return value

    attribs = [attr for attr in dir(instance) if (not callable(getattr(instance, attr)) and not attr.startswith("_"))]
    result_dict = {attr_key: convert_value(getattr(instance, attr_key)) for attr_key in attribs}

    return result_dict


def package_for_pypowsybl(opdm_objects: list | None = None,
                          minio_objects: list | None = None,
                          return_zip: bool = False,
                          export_path: str | None = None):
    """
    Method to transform OPDM objects into sufficient binary buffer or zip package
    :param opdm_objects: list of OPDM objects
    :param minio_objects: list of Minio objects
    :param return_zip: flag to save OPDM objects as zip package in directory provided in export_path
    :param export_path: path to local filesystem
    :return: binary buffer or zip package file name
    """
    output_object = BytesIO()
    if return_zip:
        output_object = export_path if export_path else f"{uuid4()}.zip"
        logger.info(f"Adding files to: {output_object}")

    with ZipFile(output_object, "w") as global_zip:
        if opdm_objects:
            for opdm_components in opdm_objects:
                for instance in opdm_components['opdm:OPDMObject']['opde:Component']:
                    with ZipFile(BytesIO(instance['opdm:Profile']['DATA'])) as instance_zip:
                        for file_name in instance_zip.namelist():
                            logger.debug(f"Adding file: {file_name}")
                            global_zip.writestr(file_name, instance_zip.open(file_name).read())
        if minio_objects:
            for minio_object in minio_objects:
                with ZipFile(minio_object) as model_zip:
                    for file_name in model_zip.namelist():
                        instance_zip = model_zip.open(file_name).read()
                        with ZipFile(BytesIO(instance_zip)) as xml_zip:
                            for file_name in xml_zip.namelist():
                                logger.debug(f"Adding file: {file_name}")
                                global_zip.writestr(file_name, xml_zip.open(file_name).read())
    if not return_zip:
        output_object.seek(0)

    return output_object


def repackage_model_zip(path_or_buffer: str):
    """
    Extracts zipped profiles of a model to global zip.
    Detects automatically if the zipped folder is read from file bath or BytesIO buffer.
    :param path_or_buffer: path of zipped model or BytesIO buffer
    :return: output_zip_buffer
    """
    # Checks if path_or_buffer is string
    if isinstance(path_or_buffer, str):
        with open(path_or_buffer, 'rb') as original_zip_file:
            original_zip_buffer = io.BytesIO(original_zip_file.read())
    elif isinstance(path_or_buffer, io.BytesIO):  # checks if path_or_buffer is BytesIO class
        original_zip_buffer = path_or_buffer
    else:
        raise Exception("Provided variable is nor string nor BytesIO object")

    output_zip_buffer = io.BytesIO()

    # Read the original zipped folder from provided buffer
    with ZipFile(original_zip_buffer, 'r') as original_zip:
        # Create a new zip file where we will store the unzipped XMLs
        with ZipFile(output_zip_buffer, 'w', ZIP_DEFLATED) as new_zip:
            # Iterate trough each file in the original zip
            for file_name in original_zip.namelist():
                # Read the zipped xml file
                with original_zip.open(file_name) as zipped_xml_file:
                    with ZipFile(zipped_xml_file) as zipped_xml:
                        # Extract each file and add it to the new zip
                        for xml_file_name in zipped_xml.namelist():
                            xml_data = zipped_xml.read(xml_file_name)
                            # Add unzipped XML file to the new zip
                            new_zip.writestr(xml_file_name, xml_data)

    output_zip_buffer.seek(0)

    return output_zip_buffer


def get_network_elements_by_ids(network: pypowsybl.network,
                                id: List[str] | pd.Series,
                                all_attributes: bool = True,
                                attributes: List[str] = None,
                                **kwargs
                                ):

    # Convert list of ids into pd.Series
    if isinstance(id, list):
        id = pd.Series(id)

    # Identify type of requested elements
    identifiables = network.get_identifiables().reindex(id).dropna(how='all')

    # Log out not found ids
    missing_ids = id[~id.isin(identifiables.index)]
    if not missing_ids.empty:
        logger.warning(f"Elements not found in network: {missing_ids.tolist()}")

    elements = []
    for type, gdf in identifiables.groupby('type'):
        single_type_df = get_network_elements(network=network,
                                              element_type=getattr(pypowsybl.network.ElementType, type),
                                              all_attributes=all_attributes,
                                              attributes=attributes,
                                              id=gdf.index,
                                              **kwargs
                                              )

        single_type_df['type'] = type
        elements.append(single_type_df)

    if elements:
        elements = pd.concat(elements)

    return elements


def get_network_elements(network: pypowsybl.network,
                         element_type: pypowsybl.network.ElementType,
                         all_attributes: bool = True,
                         attributes: List[str] = None,
                         **kwargs
                         ):

    voltage_levels = network.get_voltage_levels(all_attributes=True).rename(columns={"name": "voltage_level_name"})
    substations = network.get_substations(all_attributes=True).rename(columns={"name": "substation_name"})

    elements = network.get_elements(element_type=element_type, all_attributes=all_attributes, attributes=attributes, **kwargs)

    # If requested element is VOLTAGE_LEVEL - just need to merge substation without any suffixes
    if element_type.name == 'VOLTAGE_LEVEL':
        elements = elements.merge(substations, left_on='substation_id', right_index=True, suffixes=(None, '_substation'))

    # If requested element is TIE_LINE it does not have voltage levels yet in returned dataframe
    if element_type.name == 'TIE_LINE':
        dangling_lines = network.get_dangling_lines()
        elements = elements.merge(dangling_lines['voltage_level_id'].rename('voltage_level_id1'),
                                  how='left', left_on='dangling_line1_id', right_index=True)
        elements = elements.merge(dangling_lines['voltage_level_id'].rename('voltage_level_id2'),
                                  how='left', left_on='dangling_line2_id', right_index=True)

    # Rest of elements which has voltage level in returned dataframe
    voltage_level_column_keys = elements.columns[elements.columns.str.contains('voltage_level')]
    if len(voltage_level_column_keys) == 1:
        elements = elements.merge(voltage_levels, left_on='voltage_level_id', right_index=True, suffixes=(None, '_voltage_level'))
        elements = elements.merge(substations, left_on='substation_id', right_index=True, suffixes=(None, '_substation'))
    else:
        for i, colkey in enumerate(voltage_level_column_keys):
            elements = elements.merge(voltage_levels.add_suffix(f"{i + 1}"), left_on=colkey, right_index=True, suffixes=(None, '_voltage_level'))
            elements = elements.merge(substations.add_suffix(f"{i + 1}"), left_on=f"substation_id{i + 1}", right_index=True, suffixes=(None, '_substation'))

    return elements


def get_slack_buses(network: pypowsybl.network):

    slack_terminals = network.get_extension('slackTerminal')
    # TODO backup implementation
    # slack_buses = get_network_elements_by_ids(network=network,
    #                                           all_attributes=True,
    #                                           id=slack_terminals['element_id'])

    slack_buses = get_network_elements(network=network, element_type=pypowsybl.network.ElementType.BUS)
    slack_buses = slack_buses.loc[slack_terminals['bus_id']]

    return slack_buses


def get_connected_component_counts(network: pypowsybl.network):
    return network.get_buses().connected_component.value_counts().to_dict()