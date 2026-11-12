import sys
import logging
from pathlib import Path
import pypowsybl as pp
sys.path.append(str(Path(__file__).parents[1]))  # repository root, to run this file directly
from remedial_action_lookup.job_config import AssessedElementLookup, split
from remedial_action_lookup.ra_lookup import get_assessed_elements

# Start logger
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    conf = AssessedElementLookup()
    logging.basicConfig(level=conf.log_level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logger.info(f"Job parameters: {conf.model_dump()}")

    network = pp.network.load(str(conf.model_path), parameters=conf.cgmes_import_parameters)
    assessed_elements = get_assessed_elements(network=network, min_nominal_v=conf.min_nominal_voltage_kv, regions=split(conf.assessed_regions))

    assessed_elements.to_excel(conf.output_path, index=False)
    logger.info(f"Assessed elements at {conf.min_nominal_voltage_kv} kV and above: {len(assessed_elements)}, written to: {conf.output_path}")
