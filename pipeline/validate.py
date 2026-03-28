#!/usr/bin/env python3
"""Validate AI transcriptions against known ground truth."""

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path

from pipeline.config import TRANSCRIPTIONS_DIR, OUTPUT_DIR
from pipeline.utils import get_cards, normalize_text


def character_error_rate(reference, hypothesis):
    if not reference:
        return 0.0 if not hypothesis else 1.0
    sm = SequenceMatcher(None, reference, hypothesis)
    distance = len(reference) + len(hypothesis) - 2 * sum(m.size for m in sm.get_matching_blocks())
    return distance / len(reference)


def word_error_rate(reference, hypothesis):
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    sm = SequenceMatcher(None, ref_words, hyp_words)
    distance = len(ref_words) + len(hyp_words) - 2 * sum(m.size for m in sm.get_matching_blocks())
    return distance / len(ref_words)


def main():
    parser = argparse.ArgumentParser(description="Validate transcriptions against ground truth")
    parser.add_argument("--detailed", action="store_true", help="Show per-card results")
    args = parser.parse_args()

    cards_with_truth = list(get_cards(ready_only=True))
    print(f"Found {len(cards_with_truth)} cards with ground truth")

    results = []
    missing = 0

    for card in cards_with_truth:
        image_id = card["image"]["id"]
        trans_path = TRANSCRIPTIONS_DIR / f"{image_id}.json"

        if not trans_path.exists():
            missing += 1
            continue

        with open(trans_path) as f:
            ai_data = json.load(f)

        ref = normalize_text(card.get("transcriptionPreview", ""))
        hyp = normalize_text(ai_data.get("transcription", ""))

        if not ref:
            continue

        cer = character_error_rate(ref, hyp)
        wer = word_error_rate(ref, hyp)

        results.append({
            "image_id": image_id,
            "title": card.get("title", ""),
            "luhmann_number": card.get("luhmann_number", ""),
            "cer": round(cer, 4),
            "wer": round(wer, 4),
            "ref_length": len(ref),
            "hyp_length": len(hyp),
        })

    if not results:
        print(f"No transcriptions found to validate ({missing} cards missing transcription files)")
        return

    cers = [r["cer"] for r in results]
    wers = [r["wer"] for r in results]
    avg_cer = sum(cers) / len(cers)
    avg_wer = sum(wers) / len(wers)
    median_cer = sorted(cers)[len(cers) // 2]
    median_wer = sorted(wers)[len(wers) // 2]

    print(f"\n{'='*60}")
    print(f"QUALITY REPORT — {len(results)} cards validated")
    print(f"{'='*60}")
    print(f"Character Error Rate (CER): avg {avg_cer:.1%}, median {median_cer:.1%}")
    print(f"Word Error Rate (WER):      avg {avg_wer:.1%}, median {median_wer:.1%}")
    print(f"")

    # Distribution
    buckets = {"<5%": 0, "5-10%": 0, "10-20%": 0, "20-40%": 0, ">40%": 0}
    for cer in cers:
        if cer < 0.05: buckets["<5%"] += 1
        elif cer < 0.10: buckets["5-10%"] += 1
        elif cer < 0.20: buckets["10-20%"] += 1
        elif cer < 0.40: buckets["20-40%"] += 1
        else: buckets[">40%"] += 1

    print("CER Distribution:")
    for label, count in buckets.items():
        bar = "#" * (count * 40 // len(results))
        print(f"  {label:>6}: {count:4} ({count*100//len(results):2}%) {bar}")

    # Best and worst
    sorted_results = sorted(results, key=lambda r: r["cer"])
    print(f"\nBest 5 (lowest CER):")
    for r in sorted_results[:5]:
        print(f"  {r['title']}: CER {r['cer']:.1%}")
    print(f"\nWorst 5 (highest CER):")
    for r in sorted_results[-5:]:
        print(f"  {r['title']}: CER {r['cer']:.1%}")

    # Save report
    report = {
        "total_validated": len(results),
        "missing_transcriptions": missing,
        "avg_cer": round(avg_cer, 4),
        "median_cer": round(median_cer, 4),
        "avg_wer": round(avg_wer, 4),
        "median_wer": round(median_wer, 4),
        "distribution": buckets,
        "results": sorted_results if args.detailed else sorted_results[:10] + sorted_results[-10:],
    }

    report_path = OUTPUT_DIR / "quality_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
