# Current ResearchEngine maturation scope (2026-11-11)

Read [MATURATION.md](MATURATION.md) before extending this directory. The reference
commit is 64bd4cc. The original candidate-2 and CUDA registrations, source files,
gold, failed trials and historical reports are immutable. New report provenance
uses `research_v3_report_v2` and `backend_identity`; the old report builder has a
known CUDA-as-CPU classification bug and remains solely for frozen replay.

Use `reports/private/maturation_case/20261111/development.baseline.json` for this
declared development scope and its final baseline after completion. Root README,
AGENTS and architecture documents contain historical status; the current runbook
records this conflict. Do not edit frozen method files or weaken historical
validators to make documentation changes pass.

Candidate-3 is a separate prompt experiment. Its preregistration and new authored
holdout must precede inference; never tune after viewing outputs or pool results
with candidate-2. Developer-authored holdout is not independent validation.

Keep actual model execution, software tests, raw accuracy, rule blocks and
independent validation distinct. Keep unknown exposure, SHADOW mode, zero paid
data/external inference, null scores/targets/plans and all five jobs, price
coefficients, 23 factors and locked finals unchanged. No automatic Git push.
