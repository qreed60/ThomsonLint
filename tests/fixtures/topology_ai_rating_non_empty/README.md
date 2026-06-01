# topology_ai_rating_non_empty fixture

This directory contains static, hand-authored offline fixtures for PR45 rating
candidate coverage. These files are not live model output and must not be used
as evidence of a real datasheet review.

The response fixture set mirrors the TestProject PR26 packet IDs. One packet
contains a fixture-only `fuse_rating` / `current_max` extraction for `R50`; the
remaining packet responses are empty completed fixtures so response import and
validation can run deterministically without calling AI.

The approval fixture approves exactly one deterministic rating promotion queue
item and keeps `safe_to_apply` false. The phase driver never auto-approves; use
`--approval-decisions tests/fixtures/topology_ai_rating_non_empty/approval-decisions.json`
when an approved PR34 dry-run operation is desired.
