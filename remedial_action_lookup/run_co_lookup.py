import sys
import logging
from pathlib import Path
import pypowsybl as pp
sys.path.append(str(Path(__file__).parents[1]))  # repository root, to run this file directly
from remedial_action_lookup.job_config import ContingencyLookup, split
from remedial_action_lookup.ra_lookup import get_contingencies, solve_initial_loadflow

# Start logger
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    conf = ContingencyLookup()
    logging.basicConfig(level=conf.log_level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logger.info(f"Job parameters: {conf.model_dump()}")

    network = pp.network.load(str(conf.model_path), parameters=conf.cgmes_import_parameters)
    solve_initial_loadflow(network)  # element status is taken from currents
    contingencies = get_contingencies(network=network,
                                      min_nominal_v=conf.min_nominal_voltage_kv,
                                      min_generator_p=conf.min_generator_p_mw,
                                      energy_sources=split(conf.generator_energy_sources),
                                      regions=split(conf.contingency_regions))

    contingencies.to_excel(conf.output_path, index=False)
    logger.info(f"Contingencies of elements at {conf.min_nominal_voltage_kv} kV and above and generators of {conf.min_generator_p_mw} MW "
                f"and above: {len(contingencies)}, written to: {conf.output_path}")
