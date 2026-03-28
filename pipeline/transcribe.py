#!/usr/bin/env python3
"""Transcribe Luhmann ZK II scan images using Claude Vision."""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env if present
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import anthropic

from pipeline.config import ANTHROPIC_MODEL, IMAGES_DIR, MAX_TOKENS, TRANSCRIBE_PROMPT, TRANSCRIPTIONS_DIR
from pipeline.utils import get_cards


def transcribe_image(client, image_path, model=ANTHROPIC_MODEL):
    image_data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": TRANSCRIBE_PROMPT,
                },
            ],
        }],
    )

    text = response.content[0].text
    usage = response.usage

    return {
        "transcription": text,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def main():
    parser = argparse.ArgumentParser(description="Transcribe Luhmann ZK II cards with Claude Vision")
    parser.add_argument("--limit", type=int, default=0, help="Max cards to transcribe (0=all)")
    parser.add_argument("--ground-truth-only", action="store_true", help="Only transcribe cards with known transcriptions (for validation)")
    parser.add_argument("--model", type=str, default=ANTHROPIC_MODEL, help="Model to use")
    parser.add_argument("--delay", type=float, default=0.1, help="Seconds between API calls")
    args = parser.parse_args()

    TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()

    cards = list(get_cards(ready_only=args.ground_truth_only, not_ready_only=not args.ground_truth_only))
    print(f"Found {len(cards)} cards to process")

    transcribed = 0
    skipped = 0
    errors = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for i, card in enumerate(cards):
        if args.limit and transcribed >= args.limit:
            break

        image_id = card["image"]["id"]
        image_path = IMAGES_DIR / f"{image_id}.jpg"
        output_path = TRANSCRIPTIONS_DIR / f"{image_id}.json"

        if output_path.exists():
            skipped += 1
            continue

        if not image_path.exists():
            continue

        try:
            result = transcribe_image(client, image_path, model=args.model)

            output = {
                "image_id": image_id,
                "ekin": card.get("ekin", ""),
                "luhmann_number": card.get("luhmann_number", ""),
                "title": card.get("title", ""),
                "model": args.model,
                "transcription": result["transcription"],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if card.get("transcription", {}).get("readyForPublication"):
                output["reference_transcription"] = card.get("transcriptionPreview", "")

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            transcribed += 1
            total_input_tokens += result["input_tokens"]
            total_output_tokens += result["output_tokens"]

            if transcribed % 10 == 0:
                cost_in = total_input_tokens * 0.80 / 1_000_000
                cost_out = total_output_tokens * 4.00 / 1_000_000
                print(f"  [{transcribed}] {card['title']} | "
                      f"tokens: {result['input_tokens']}+{result['output_tokens']} | "
                      f"total cost: ${cost_in + cost_out:.2f}")

        except Exception as e:
            errors += 1
            print(f"  ERROR {image_id}: {e}")
            time.sleep(2)

        time.sleep(args.delay)

    cost_in = total_input_tokens * 0.80 / 1_000_000
    cost_out = total_output_tokens * 4.00 / 1_000_000
    print(f"\nDone: {transcribed} transcribed, {skipped} skipped, {errors} errors")
    print(f"Tokens: {total_input_tokens:,} input + {total_output_tokens:,} output")
    print(f"Estimated cost: ${cost_in + cost_out:.2f}")


if __name__ == "__main__":
    main()
