#!/usr/bin/env python
"""Evaluate Airlock's injection classifiers against a labeled corpus.

Runs each sample through the local tripwire and, when configured, one or more
semantic providers, then reports per-classifier confusion matrices, latency, and
disagreement. Designed for the 0.5.9 corpus-equivalence evidence and for
re-validating after a provider or filter-version change.

Corpus format — JSONL, one object per line::

    {"text": "...", "label": 1, "split": "test"}

``label`` is 1 for attack and 0 for benign. Extra keys are ignored.

Usage::

    python scripts/benchmark_injection.py data/corpora/corpus.jsonl \\
        --template projects/P/locations/us-central1/templates/T \\
        --split test --out dev/plans/runs/injection-benchmark.json

The report contains **no corpus text** — only counts, rates, and indices — so it
is safe to retain in Git even when the corpus itself is not redistributable.
Sample text stays in the (gitignored) corpus file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airlock.guardrails.prompt_injection import (  # noqa: E402
    InputInjectionTripwire,
    ProviderInjectionClassifier,
)
from airlock.guardrails.providers.model_armor import ModelArmorProvider  # noqa: E402


def load_corpus(path: Path, split: str | None) -> list[dict]:
    samples = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if split and record.get("split") not in (None, split):
                continue
            if "text" not in record or "label" not in record:
                continue
            samples.append(record)
    return samples


def confusion(pairs: list[tuple[int, int | None]]) -> dict:
    """Confusion matrix over (expected, predicted) pairs.

    ``predicted is None`` means the classifier had no verdict. Those are counted
    separately and excluded from precision/recall — scoring an unavailable
    verdict as either outcome would misstate the classifier's quality.
    """
    tp = sum(1 for e, p in pairs if p == 1 and e == 1)
    fp = sum(1 for e, p in pairs if p == 1 and e == 0)
    tn = sum(1 for e, p in pairs if p == 0 and e == 0)
    fn = sum(1 for e, p in pairs if p == 0 and e == 1)
    unavailable = sum(1 for _, p in pairs if p is None)
    answered = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "unavailable": unavailable,
        "answered": answered,
        "accuracy": round((tp + tn) / answered, 4) if answered else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "false_positive_rate": (round(fp / (fp + tn), 4) if (fp + tn) else None),
    }


async def run(args: argparse.Namespace) -> dict:
    corpus_path = Path(args.corpus)
    samples = load_corpus(corpus_path, args.split)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        raise SystemExit(f"no samples loaded from {corpus_path}")

    classifiers: list = [InputInjectionTripwire()]
    provider = None
    if args.template:
        provider = ModelArmorProvider(
            template=args.template, timeout_seconds=args.timeout
        )
        classifiers.append(ProviderInjectionClassifier([provider]))

    semaphore = asyncio.Semaphore(args.concurrency)
    results: dict[str, list] = {c.name: [] for c in classifiers}
    latencies: dict[str, list[float]] = {c.name: [] for c in classifiers}
    confidences: dict[str, dict[str, int]] = {c.name: {} for c in classifiers}
    errors: dict[str, dict[str, int]] = {c.name: {} for c in classifiers}

    async def classify_one(index: int, sample: dict) -> None:
        async with semaphore:
            for classifier in classifiers:
                result = await classifier.classify(sample["text"])
                name = classifier.name
                latencies[name].append(result.duration_ms)
                if result.label == "unavailable":
                    predicted = None
                    errors[name][result.error or "unknown"] = (
                        errors[name].get(result.error or "unknown", 0) + 1
                    )
                else:
                    predicted = 1 if result.blocked else 0
                for entry in result.metadata.get("provider_results", []):
                    level = entry.get("confidence")
                    if level:
                        confidences[name][level] = confidences[name].get(level, 0) + 1
                results[name].append((index, int(sample["label"]), predicted))

    started = time.monotonic()
    await asyncio.gather(*(classify_one(i, s) for i, s in enumerate(samples)))
    wall_seconds = time.monotonic() - started

    report: dict = {
        "corpus": {
            "path": str(corpus_path),
            "split": args.split,
            "samples": len(samples),
            "attacks": sum(1 for s in samples if int(s["label"]) == 1),
            "benign": sum(1 for s in samples if int(s["label"]) == 0),
        },
        "run": {
            "wall_seconds": round(wall_seconds, 2),
            "concurrency": args.concurrency,
        },
        "classifiers": {},
    }
    if provider is not None:
        report["provider"] = provider.describe()

    for classifier in classifiers:
        name = classifier.name
        pairs = [(expected, predicted) for _, expected, predicted in results[name]]
        lat = latencies[name]
        report["classifiers"][name] = {
            **confusion(pairs),
            "latency_ms": {
                "mean": round(statistics.mean(lat), 2),
                "p50": round(statistics.median(lat), 2),
                "p95": round(sorted(lat)[int(len(lat) * 0.95)], 2),
                "max": round(max(lat), 2),
            },
            "confidence_levels": confidences[name],
            "errors": errors[name],
            # Indices only — the text stays in the corpus file.
            "false_positive_indices": sorted(
                i for i, e, p in results[name] if p == 1 and e == 0
            )[:100],
            "false_negative_indices": sorted(
                i for i, e, p in results[name] if p == 0 and e == 1
            )[:100],
        }

    if len(classifiers) > 1:
        a, b = classifiers[0].name, classifiers[1].name
        by_index = {name: {i: p for i, _, p in results[name]} for name in (a, b)}
        disagreements = [
            i
            for i in by_index[a]
            if by_index[a][i] is not None
            and by_index[b][i] is not None
            and by_index[a][i] != by_index[b][i]
        ]
        report["tier_disagreement"] = {
            "count": len(disagreements),
            "indices": sorted(disagreements)[:100],
            "note": (
                "Under adaptive selection a light-tier detection short-circuits "
                "the heavy tier, so light-tier false positives become system "
                "false positives."
            ),
        }

    if provider is not None:
        await provider.aclose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", help="JSONL corpus with text/label fields")
    parser.add_argument("--split", help="only evaluate this split")
    parser.add_argument("--limit", type=int, help="cap sample count")
    parser.add_argument("--template", help="Model Armor template resource name")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    print(rendered)


if __name__ == "__main__":
    main()
