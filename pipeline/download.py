#!/usr/bin/env python3
"""Download scan images for all ZK II cards."""

import argparse
import sys
import time
import urllib.request
from pathlib import Path

from pipeline.config import (
    DOWNLOAD_DELAY, DOWNLOAD_RETRIES, DOWNLOAD_TIMEOUT,
    IMAGE_HEADERS, IMAGE_URL, IMAGES_DIR,
)
from pipeline.utils import get_cards


def download_image(image_id, dest_path, retries=DOWNLOAD_RETRIES):
    url = IMAGE_URL.format(image_id=image_id)
    req = urllib.request.Request(url, headers=IMAGE_HEADERS)

    for attempt in range(retries):
        try:
            tmp = dest_path.with_suffix(".tmp")
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                data = resp.read()
                if len(data) < 1000:
                    raise ValueError(f"Response too small ({len(data)} bytes)")
                tmp.write_bytes(data)
                tmp.rename(dest_path)
                return len(data)
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"    Retry {attempt + 1}/{retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def main():
    parser = argparse.ArgumentParser(description="Download Luhmann ZK II scan images")
    parser.add_argument("--start", type=int, default=0, help="Skip first N cards")
    parser.add_argument("--limit", type=int, default=0, help="Max cards to download (0=all)")
    parser.add_argument("--delay", type=float, default=DOWNLOAD_DELAY, help="Seconds between requests")
    parser.add_argument("--not-ready-only", action="store_true", help="Only download untranscribed cards")
    args = parser.parse_args()

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    cards = list(get_cards(not_ready_only=args.not_ready_only))
    total = len(cards)
    print(f"Found {total} cards with images")

    downloaded = 0
    skipped = 0
    errors = 0

    for i, card in enumerate(cards):
        if i < args.start:
            continue
        if args.limit and downloaded >= args.limit:
            break

        image_id = card["image"]["id"]
        dest = IMAGES_DIR / f"{image_id}.jpg"

        if dest.exists() and dest.stat().st_size > 1000:
            skipped += 1
            if skipped % 500 == 0:
                print(f"  Skipped {skipped} existing...")
            continue

        try:
            size = download_image(image_id, dest)
            downloaded += 1
            if downloaded % 100 == 0:
                print(f"  [{i+1}/{total}] Downloaded {downloaded}, skipped {skipped}, errors {errors}")
        except Exception as e:
            errors += 1
            print(f"  ERROR [{i+1}/{total}] {image_id}: {e}")

        time.sleep(args.delay)

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
