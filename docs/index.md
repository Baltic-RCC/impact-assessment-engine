# impact-assessment-engine

A script based on Pypowsybl for running impact assessment related calculations according to CSAm

## Packages

- [`brcc_pypowsybl_helpers`](api/brcc_pypowsybl_helpers/converters.md) &mdash; PowSyBl network helpers, converters and load-flow parameters. Internal usage of BRCC, set to be deprecated, removed from this package and published as part of a separate project in the future.
- [`impact_assessment_engine`](api/impact_assessment_engine/impact_assessment.md) &mdash; the `ImpactAssessment` calculation engine
- [`impact_assessment_reporting`](api/impact_assessment_reporting/iam_builder.md) &mdash; ImpactAssessmentMatrix profile reporting
- [`remedial_action_lookup`](api/remedial_action_lookup/ra_lookup.md) &mdash; jobs that look up assessed elements, contingencies and cross-border relevant remedial actions from a CGMES model, and assess remedial actions

## Guides

- [Remedial action lookup jobs](guides/remedial_action_lookup.md) &mdash; how to configure and run the AE, CO and RA lookups and the RA impact assessment
