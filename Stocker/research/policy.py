"""Versioned, explicitly unvalidated research policy, shared by candidate code."""

import json
from pathlib import Path


def candidate_policy():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / "config/swing_candidate_policy.json").read_text(encoding="utf-8"))
    weights = policy["attractiveness"]["weights"]
    if sum(weights.values()) != 100 or policy["public_score_enabled"] is not False:
        raise ValueError("Candidate policy must total 100 points and remain disabled for release")
    if policy["target"]["atr_period"] != 14 or policy["target"]["observation_sessions"] != 5:
        raise ValueError("Unsupported methodology change; register a new implementation/version first")
    return policy
