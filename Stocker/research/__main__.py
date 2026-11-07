"""Run with: python -m research --snapshot <reviewed SEC snapshot>."""

import argparse
import json
import sys
import time
from pathlib import Path

import jsonschema

from .sec_report import ROOT, SourceAdapter, build_report, digest, render_markdown


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate a local SEC-only research report; no quote/model API calls")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--ticker", default="BE")
    parser.add_argument("--as-of", help="Timezone-aware cutoff; defaults to the snapshot's reviewed_as_of")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/private"))
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        header = json.loads(args.snapshot.read_text(encoding="utf-8"))
        cutoff = args.as_of or header["reviewed_as_of"]
        snapshot, facts, snapshot_hash = SourceAdapter.load(args.snapshot, cutoff)
        report, audit = build_report(snapshot, facts, args.ticker, cutoff, snapshot_hash)
        audit.update(report_id=report["report_id"], generated_at=report["generated_at"], as_of=cutoff,
                     code_sha256={path.name: digest(path.read_bytes()) for path in sorted((ROOT / "research").glob("*.py"))},
                     schema_sha256=digest((ROOT / "schemas/research_report.schema.json").read_bytes()),
                     elapsed_seconds=round(time.perf_counter() - started, 6))
        payloads = {
            "report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            "report.md": render_markdown(report),
            "provenance.json": json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        }
        destination = args.output_dir / report["report_id"]
        destination.mkdir(parents=True, exist_ok=False)
        for name, content in payloads.items():
            with (destination / name).open("x", encoding="utf-8") as handle:
                handle.write(content)
        print(json.dumps({"report_id": report["report_id"], "output_dir": str(destination.resolve()),
                          "metrics_available": sum(item["value"] is not None for item in report["metrics"]),
                          "swing_status": report["swing"]["status"], "llm_api_calls": 0}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, jsonschema.ValidationError) as error:
        print(f"Report generation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
