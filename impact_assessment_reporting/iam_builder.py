import logging
from typing import Optional

import pandas as pd
from nc_data_exchange.profiles.ImpactAssessmentMatrix import (
    CalculationBasedImpactAssessmentMatrix,
    ImpactAssessmentMatrix,
    RemedialActionOutcomeValue,
    RemedialActionScheduleOutcomeValue,
)

# Start logger
logger = logging.getLogger(__name__)

SCHEDULE_KEY_COLUMN = "ra_schedule_mRID"

# Columns carrying the remedial action reference, first existing one is used
RA_KEY_COLUMNS = ("influencing_id", "ra_schedule_remedial_action")

# Column carrying impacted system operator (TSO short name), resolved by the caller
OPERATOR_COLUMN = "impacted_system_operator"


def _strip_leading_underscore(mrid: str) -> str:
    return mrid[1:] if mrid.startswith("_") else mrid


def _max_rows(df: pd.DataFrame, group_keys: list, if_column: str) -> pd.DataFrame:
    """Keep row with maximum influence factor for each group"""
    if df.empty:
        return df
    return df.loc[df.groupby(group_keys)[if_column].idxmax()]


def get_impact_assessment_matrix_rcp(
    results: pd.DataFrame,
    threshold_percent: float = 5.0,
    if_column: str = "if_filtering_max",
    operator_column: str = OPERATOR_COLUMN,
    matrix: Optional[ImpactAssessmentMatrix] = None,
) -> tuple[ImpactAssessmentMatrix, list]:
    """
    Impact assessment results converted to network code ImpactAssessmentMatrix profile classes,
    same as SecurityAnalysis.get_processed_branch_results_rcp() does for the SAR profile

    Consumes the ImpactAssessment results DataFrame (single-run max_influence_factors_by_xnec()
    output, or the pd.concat of those with ra_schedule_* columns added for the scheduled-RA
    handler flow). Returned elements are ready to be handed to a nc_data_exchange.profile_constructor.

    Two types of outcome values are created:
    - RemedialActionOutcomeValue per remedial action and impacted system operator, with impactQuantity
    - RemedialActionScheduleOutcomeValue per remedial action schedule and impacted system operator,
      created for the scheduled RA flow

    Expects the results to be already resolved by the caller, no reference data lookups are done here:
    operator_column holds the impacted system operator (TSO short name).
    """
    if operator_column not in results.columns:
        raise KeyError(f"Results does not contain impacted system operator column: {operator_column!r}")

    ra_column = next((column for column in RA_KEY_COLUMNS if column in results.columns), None)
    if not ra_column:
        raise KeyError(f"Results does not contain remedial action reference, expected one of: {RA_KEY_COLUMNS}")

    matrix = matrix or CalculationBasedImpactAssessmentMatrix(
        name="IAM",
        description=f"Calculation based IAM, impact threshold {threshold_percent:g}% "
                    f"for neighbouring system operators",
    )
    is_scheduled = SCHEDULE_KEY_COLUMN in results.columns

    logger.info(f"Building ImpactAssessmentMatrix outcomes from {'scheduled' if is_scheduled else 'immediate'} results")

    df = results.dropna(subset=[if_column, operator_column])
    outcome_values = []

    # RemedialActionOutcomeValue: maximum impact of each remedial action on each impacted system operator
    for _, row in _max_rows(df, [ra_column, operator_column], if_column).iterrows():
        impact_quantity = float(row[if_column])
        outcome_values.append(RemedialActionOutcomeValue(
            ImpactAssessmentMatrix=matrix,
            outcome=bool(impact_quantity >= threshold_percent),
            ImpactedSystemOperator=row[operator_column],
            RemedialAction=_strip_leading_underscore(row[ra_column]),
            impactQuantity=impact_quantity,
        ))

    # RemedialActionScheduleOutcomeValue: maximum impact of each schedule on each impacted system operator
    if is_scheduled:
        for _, row in _max_rows(df, [SCHEDULE_KEY_COLUMN, operator_column], if_column).iterrows():
            impact_quantity = float(row[if_column])
            outcome_values.append(RemedialActionScheduleOutcomeValue(
                ImpactAssessmentMatrix=matrix,
                outcome=bool(impact_quantity >= threshold_percent),
                ImpactedSystemOperator=row[operator_column],
                RemedialActionSchedule=_strip_leading_underscore(row[SCHEDULE_KEY_COLUMN]),
            ))

    return matrix, outcome_values


if __name__ == '__main__':
    # Example: build IAM profile elements from a synthetic results DataFrame and export to RDF/XML
    from nc_data_exchange.profile_constructor import Profile

    results_df = pd.DataFrame([
        {"name": "XNEC_1", "country": "LT", "impacted_system_operator": "LITGRID",
         "influencing_id": "7e258bc8-e18c-44a6-ad2d-da1dd4c6bf69", "if_filtering_max": 7.5},
        {"name": "XNEC_1", "country": "LV", "impacted_system_operator": "AST",
         "influencing_id": "7e258bc8-e18c-44a6-ad2d-da1dd4c6bf69", "if_filtering_max": 1.2},
    ])

    ia_matrix, ia_outcomes = get_impact_assessment_matrix_rcp(results=results_df)

    profile = Profile(profile_name='ImpactAssessmentMatrix')
    profile.add_document_header(startDate="2026-08-10T10:00:00Z", endDate="2026-08-10T11:00:00Z")
    profile.add_element(element=ia_matrix)
    for ia_outcome in ia_outcomes:
        profile.add_element(element=ia_outcome)

    print(profile.get_profile_xml(fix_rdf_about=True, remove_rdf_datatype=True, save=False).decode())
