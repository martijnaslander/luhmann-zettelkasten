import json
import re
from pathlib import Path
from pipeline.config import DATA_DIR, PROGRESS_FILE


def load_zk2_data():
    path = DATA_DIR / "luhmann_zk2_complete.json"
    with open(path) as f:
        return json.load(f)


def get_cards(ready_only=False, not_ready_only=False, has_image=True):
    data = load_zk2_data()
    for card in data["results"]:
        if has_image and card.get("isDummy", False):
            continue
        if has_image and not card.get("image", {}).get("id"):
            continue
        ready = card.get("transcription", {}).get("readyForPublication", False)
        if ready_only and not ready:
            continue
        if not_ready_only and ready:
            continue
        yield card


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"downloaded": [], "transcribed": []}


def save_progress(progress):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def normalize_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[\t\n\r]+", "\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()
