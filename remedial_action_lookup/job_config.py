from pathlib import Path
from typing import Dict, List, Literal
from pydantic import FilePath
from pydantic_settings import BaseSettings, SettingsConfigDict

CONF_DIR = Path(__file__).parent


def split(values: str) -> List[str]:
    """Comma separated configuration value to list"""
    return [value.strip() for value in values.split(',') if value.strip()]


class Job(BaseSettings):
    """Common job parameters, environment variable with the same name overrides job conf file, empty value means default"""
    model_config = SettingsConfigDict(env_ignore_empty=True)

    model_path: FilePath
    min_nominal_voltage_kv: float = 330
    cgmes_import_parameters: Dict[str, str] = {"iidm.import.cgmes.source-for-iidm-id": "rdfID",
                                               "iidm.import.cgmes.import-node-breaker-as-bus-breaker": "False"}
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR'] = 'INFO'


class AssessedElementLookup(Job):
    """run_ae_lookup.py parameters from ae_lookup.conf"""
    model_config = SettingsConfigDict(env_file=CONF_DIR / 'ae_lookup.conf')

    assessed_regions: str = ''
    output_path: Path = Path('assessed_elements.xlsx')


class ContingencyCriteria(Job):
    """Parameters of jobs which look up contingencies from model"""
    generator_energy_sources: str = 'THERMAL,NUCLEAR,HYDRO'
    min_generator_p_mw: float = 100
    contingency_regions: str = ''


class ContingencyLookup(ContingencyCriteria):
    """run_co_lookup.py parameters from co_lookup.conf"""
    model_config = SettingsConfigDict(env_file=CONF_DIR / 'co_lookup.conf')

    output_path: Path = Path('contingencies.xlsx')


class RemedialActionLookup(ContingencyCriteria):
    """run_ra_lookup.py parameters from ra_lookup.conf"""
    model_config = SettingsConfigDict(env_file=CONF_DIR / 'ra_lookup.conf')

    assessed_element_list_path: FilePath | None = None
    contingency_list_path: FilePath | None = None
    run_if_assessment_for_ras: bool = True
    run_n1_for_ras: bool = True
    threshold_percent: float = 5
    ignore_emerged_islands: bool = True
    output_path: Path = Path('cross_border_relevant_remedial_actions.xlsx')


class RemedialActionAssessment(Job):
    """run_ra_assessment.py parameters from ra_assessment.conf"""
    model_config = SettingsConfigDict(env_file=CONF_DIR / 'ra_assessment.conf')

    remedial_action_list_path: FilePath
    remedial_action_names: str = ''
    contingency_list_path: FilePath | None = None
    contingency_names: str = ''
    assessed_element_list_path: FilePath | None = None
    assessed_regions: str = ''
    threshold_percent: float = 5
    ignore_emerged_islands: bool = True
    output_path: Path = Path('remedial_action_flow_influence.xlsx')
