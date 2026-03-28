from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
IMAGES_DIR = PROJECT_ROOT / "images"
OUTPUT_DIR = PROJECT_ROOT / "output"
TRANSCRIPTIONS_DIR = OUTPUT_DIR / "transcriptions"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"

# Image download
IMAGE_URL = "https://images.niklas-luhmann-archiv.de/image/{image_id}?size=2"
IMAGE_HEADERS = {"Referer": "https://niklas-luhmann-archiv.de/"}
DOWNLOAD_DELAY = 0.5
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_RETRIES = 3

# Transcription
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2048

TRANSCRIBE_PROMPT = """You are transcribing a handwritten German note card from Niklas Luhmann's Zettelkasten (1963-1996). The card is written in German, sometimes with Latin, English, or French passages. Luhmann used abbreviations extensively.

Transcribe the card exactly as written. Preserve:
- Line breaks where visible
- Underlined text as **underlined**
- Cross-references (e.g., "vgl. 21/3d14") exactly as written
- Abbreviations unchanged (e.g., "s.", "vgl.", "Fn.")
- Margin notes, marked with [margin:]
- If text is illegible, mark as [illegible]

Output ONLY the transcription. No commentary, no explanation."""
