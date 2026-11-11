# Reliability recovery audit v1

This namespace is an offline audit and versioned labeling supplement. It is not
candidate-5, changes no inference behavior and launches no model. Frozen v3,
candidate-2/3, v3.1 and v4 scores/methods/gold/raw outputs remain unchanged.
Use `reports/private/reliability_recovery_case/20261111` for private audit receipts.

Separate variable-aligned candidate coverage, literal reference agreement,
actual JSON/schema acceptance, structural binding acceptance, supported-reference
agreement, omitted references, explicit abstention and final rejections. Semantic
equivalence requires adjudication; exact reference disagreement is not proof of
a wrong meaning. Real semantic precision/recall and wrong/unnecessary decisions
remain NOT_MEASURED without independent labels. Never regrade frozen failures.

The old critic diagnostic is also confounded: its reference arm has three
SUPPORTED_CONDITIONAL and three INSUFFICIENT_EVIDENCE labels. Its new CLAIM selects
all compatible routes, including missing exposure components, while inheriting
the old expected verdict. It changes the evidence representation as well. Twelve
UNSUPPORTED outputs are an observed constant-rejection pattern, not a causal
estimate of semantic critic over-rejection. Always-reject cannot pass a gate
requiring positive recall/acceptance, regardless of specificity or class imbalance.

## Blind human labeling

Use only source-only assignments for the two independent primary reviewers.
The model-visible adjudication queue is for a separate post-blind panel, or after
primary annotations have been sealed. It must not be used to create primary gold.
Reviewer IDs in generated templates are placeholders, not actual people. The
reused 22 development paragraphs can receive independent annotations but cannot
be relabeled as a new unseen final holdout.

```powershell
python -m extensions.reliability_recovery.labels assign --corpus CORPUS.reference.json --reviewer reviewer-a --out reviewer-a
python -m extensions.reliability_recovery.labels import --assignment reviewer-a.json --review reviewer-a-results.json --out review-a.sealed.json
python -m extensions.reliability_recovery.labels compare --a review-a.sealed.json --b review-b.sealed.json --out disagreements.json
python -m extensions.reliability_recovery.labels adjudicate --corpus CORPUS.reference.json --a review-a.sealed.json --b review-b.sealed.json --reviewer reviewer-c --out adjudicator
python -m extensions.reliability_recovery.labels import --assignment adjudicator.json --review reviewer-c-results.json --out review-c.sealed.json
python -m extensions.reliability_recovery.labels seal --corpus CORPUS.reference.json --a review-a.sealed.json --b review-b.sealed.json --adjudication review-c.sealed.json --out GOLD.json
```

Open each HTML locally. Select exact quotes, fill fields and reasons, affirm
blinding, save each paragraph, and download the JSON. Preserve the original
assignment file: its hash identifies the review. The supplement supports removing
the last draft, resetting one paragraph and restoring only the review belonging
to that same assignment/reviewer/role. Do not transfer another person's review
to a primary reviewer. Imported partial files stay incomplete; sealing requires
both complete primaries and a distinct third reviewer for every disagreement.

The v2 supplement rejects extra model/answer fields, changed source hashes,
duplicate labels/cases, contradictory absence/context flags and backdated review
times. Agreement ignores only list order and rationale wording, not semantic
differences. Adjudication binds the exact pair of sealed primary files. Reviewer
identity and independence remain offline human declarations requiring owner
verification. No actual reviewers means NOT_READY, never independent validation.

Candidate-5 is a conditional proposal only. First adjudicate representation and
scope, repair the evaluation design, freeze new balanced task-specific labels,
methods and denominators, and then preregister one experiment. Development
examples never become an unseen holdout. A separate verifier checkpoint stays
TBD until licensed/pinned bytes and measured RAM/VRAM/latency/throughput are
available. Do not download models, use paid APIs, bypass memory floors or change
the five jobs, 23 factors, locked final data, SHADOW or null investment outputs.
Root documents and tracked private legacy files are preserved; no automatic push.
