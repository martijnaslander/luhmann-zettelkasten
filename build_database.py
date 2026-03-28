#!/usr/bin/env python3
"""Blok 1: Build the Luhmann Zettelkasten SQLite database.

Creates a searchable database with:
- All 73,715 cards with full-text search
- Cross-references extracted via regex
- Folgezettel tree structure
- Schlagwortregister (keyword index)
- Card type classification
"""

import json
import re
import sqlite3
from pathlib import Path

PROJECT = Path(__file__).parent
DATA = PROJECT / "data"
DB_PATH = PROJECT / "luhmann.db"

# Luhmann reference patterns
# ZK I: comma-separated (1,1 → 1,1a → 1,1a1)
# ZK II: slash-separated (1/1 → 1/1a → 1/1a1)
# Up to 13 characters, mix of digits and lowercase letters
REF_PATTERN = re.compile(
    r'(?<![a-zA-Z/,\d])'           # not preceded by alphanumeric
    r'(\d{1,3}'                     # starts with 1-3 digits
    r'[/,]'                         # separator (slash or comma)
    r'\d{1,4}'                      # section number
    r'[a-z0-9/,]*'                  # optional continuation (letters, digits, separators)
    r')'
    r'(?![a-zA-Z\d])'              # not followed by alphanumeric
)

# Luhmann number from ekin: ZK_2_NB_52-4_V → 52-4, ZK_1_NB_1-1a_V → 1-1a
EKIN_TO_NUM = re.compile(r'ZK_[12]_(?:NB|SWR|BIBL|PE|VS|PR|WL)_(.+?)_[VR]$')


def normalize_luhmann_number(num):
    """Normalize a Luhmann number for matching: 52/4b → 52-4b, 1,1a → 1-1a"""
    return num.replace('/', '-').replace(',', '-')


def parse_parent_number(luhmann_num):
    """Get parent in Folgezettel tree. 21/3d4 → 21/3d3 or 21/3d, 1/1a → 1/1"""
    if not luhmann_num:
        return None
    # Remove trailing letter: 1/1a → 1/1
    if re.search(r'[a-z]$', luhmann_num):
        return luhmann_num[:-1] if len(luhmann_num) > 1 else None
    # Remove trailing digit after letter: 1/1a2 → 1/1a1 or 1/1a
    if re.search(r'[a-z]\d+$', luhmann_num):
        m = re.search(r'^(.+[a-z])(\d+)$', luhmann_num)
        if m:
            base, digit = m.group(1), int(m.group(2))
            if digit > 1:
                return f"{base}{digit - 1}"
            return base
    # Remove trailing number: 1/2 → 1/1 or 1
    m = re.match(r'^(.+[/,])(\d+)$', luhmann_num)
    if m:
        base, digit = m.group(1), int(m.group(2))
        if digit > 1:
            return f"{base}{digit - 1}"
        return base.rstrip('/,')
    return None


def extract_references(text, own_number=""):
    """Extract Luhmann card references from transcription text."""
    if not text:
        return []
    refs = []
    for m in REF_PATTERN.finditer(text):
        ref = m.group(1)
        # Skip if it's the card's own number
        if ref == own_number:
            continue
        # Skip dates (19xx, 20xx patterns)
        if re.match(r'^19\d\d', ref) or re.match(r'^20\d\d', ref):
            continue
        # Skip page numbers that look like references but are in citation context
        if len(ref) > 20:
            continue
        refs.append(ref)
    return refs


def extract_keywords_from_register(text):
    """Parse Schlagwortregister cards: keyword → list of card numbers."""
    if not text:
        return []
    entries = []
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Pattern: "Keyword  1/2, 3/4, 5/6" or "Keyword → 1/2"
        # Look for keyword followed by numbers
        parts = re.split(r'\s{2,}|→|──|—|–|\t', line, maxsplit=1)
        if len(parts) == 2:
            keyword = parts[0].strip()
            numbers = parts[1].strip()
            if keyword and numbers:
                refs = REF_PATTERN.findall(numbers)
                if refs:
                    entries.append((keyword, refs))
    return entries


def build():
    print("Loading JSON data...")
    cards = []
    for zk in [1, 2]:
        path = DATA / f"luhmann_zk{zk}_complete.json"
        with open(path) as f:
            data = json.load(f)
        for r in data["results"]:
            r["_zk"] = zk
            cards.append(r)
    print(f"  Loaded {len(cards)} cards")

    print("Creating database...")
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # Tables
    c.executescript("""
        CREATE TABLE cards (
            ekin TEXT PRIMARY KEY,
            zk INTEGER,
            luhmann_number TEXT,
            normalized_number TEXT,
            title TEXT,
            text TEXT,
            word_count INTEGER,
            card_type TEXT,
            abteilung TEXT,
            is_front INTEGER,
            has_text INTEGER,
            parent_number TEXT,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            section_number TEXT,
            image_id TEXT
        );

        CREATE TABLE refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_ekin TEXT,
            target_raw TEXT,
            target_normalized TEXT,
            target_ekin TEXT,
            ref_source TEXT DEFAULT 'regex',
            FOREIGN KEY (source_ekin) REFERENCES cards(ekin)
        );

        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            target_raw TEXT,
            target_normalized TEXT,
            target_ekin TEXT,
            source_ekin TEXT
        );

        CREATE TABLE sections (
            section_number TEXT,
            zk INTEGER,
            card_count INTEGER,
            max_depth INTEGER,
            first_ekin TEXT
        );

        CREATE VIRTUAL TABLE cards_fts USING fts5(
            ekin,
            luhmann_number,
            title,
            text,
            content='cards',
            content_rowid='rowid'
        );

        CREATE INDEX idx_cards_number ON cards(normalized_number);
        CREATE INDEX idx_cards_zk ON cards(zk);
        CREATE INDEX idx_cards_type ON cards(card_type);
        CREATE INDEX idx_refs_source ON refs(source_ekin);
        CREATE INDEX idx_refs_target ON refs(target_ekin);
        CREATE INDEX idx_refs_target_norm ON refs(target_normalized);
        CREATE INDEX idx_keywords_keyword ON keywords(keyword);
    """)

    # Build number → ekin lookup
    print("Building number lookup...")
    num_to_ekin = {}
    for card in cards:
        ln = card.get("luhmann_number", "")
        ekin = card.get("ekin", "")
        if ln and ekin:
            norm = normalize_luhmann_number(ln)
            num_to_ekin[norm] = ekin
            # Also store with ZK prefix for disambiguation
            zk = card["_zk"]
            num_to_ekin[f"zk{zk}:{norm}"] = ekin

    # Insert cards
    print("Inserting cards...")
    for card in cards:
        ekin = card.get("ekin", "")
        ln = card.get("luhmann_number", "")
        text = card.get("transcriptionPreview", "").strip()
        text_clean = re.sub(r'[\t\n\r]+', '\n', text).strip()

        # Section number (first part before separator)
        section = ""
        if ln:
            m = re.match(r'^(\d+)', ln)
            if m:
                section = m.group(1)

        c.execute("""
            INSERT OR IGNORE INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)
        """, (
            ekin,
            card["_zk"],
            ln,
            normalize_luhmann_number(ln) if ln else "",
            card.get("title", ""),
            text_clean,
            len(text_clean.split()) if text_clean else 0,
            card.get("meta", {}).get("zettel_type", ""),
            card.get("meta", {}).get("abteilung", ""),
            1 if card.get("is_front") else 0,
            1 if len(text_clean) > 10 else 0,
            parse_parent_number(ln),
            section,
            card.get("image", {}).get("id", ""),
        ))

    # Populate FTS index
    print("Building full-text search index...")
    c.execute("""
        INSERT INTO cards_fts(ekin, luhmann_number, title, text)
        SELECT ekin, luhmann_number, title, text FROM cards WHERE has_text = 1
    """)

    # Extract cross-references
    print("Extracting cross-references...")
    ref_count = 0
    for card in cards:
        ekin = card.get("ekin", "")
        ln = card.get("luhmann_number", "")
        text = card.get("transcriptionPreview", "")
        zk = card["_zk"]

        refs = extract_references(text, own_number=ln)
        for ref in refs:
            norm = normalize_luhmann_number(ref)
            # Try to resolve to ekin
            target_ekin = num_to_ekin.get(norm, "")
            if not target_ekin:
                # Try with ZK prefix (same ZK first)
                target_ekin = num_to_ekin.get(f"zk{zk}:{norm}", "")
            if not target_ekin:
                # Try other ZK
                other_zk = 1 if zk == 2 else 2
                target_ekin = num_to_ekin.get(f"zk{other_zk}:{norm}", "")

            c.execute("INSERT INTO refs (source_ekin, target_raw, target_normalized, target_ekin) VALUES (?,?,?,?)",
                      (ekin, ref, norm, target_ekin))
            ref_count += 1

    print(f"  Extracted {ref_count} references")

    # Count resolved
    c.execute("SELECT COUNT(*) FROM refs WHERE target_ekin != ''")
    resolved = c.fetchone()[0]
    print(f"  Resolved: {resolved} ({resolved*100//ref_count}%)")

    # Update degrees
    print("Calculating node degrees...")
    c.execute("""
        UPDATE cards SET out_degree = (
            SELECT COUNT(*) FROM refs WHERE refs.source_ekin = cards.ekin
        )
    """)
    c.execute("""
        UPDATE cards SET in_degree = (
            SELECT COUNT(*) FROM refs WHERE refs.target_ekin = cards.ekin
        )
    """)

    # Parse Schlagwortregister
    print("Parsing Schlagwortregister...")
    kw_count = 0
    c.execute("SELECT ekin, text FROM cards WHERE card_type = 'Registerzettel' AND has_text = 1")
    for row in c.fetchall():
        source_ekin, text = row
        entries = extract_keywords_from_register(text)
        for keyword, refs in entries:
            for ref in refs:
                norm = normalize_luhmann_number(ref)
                target_ekin = num_to_ekin.get(norm, "")
                c.execute("INSERT INTO keywords (keyword, target_raw, target_normalized, target_ekin, source_ekin) VALUES (?,?,?,?,?)",
                          (keyword, ref, norm, target_ekin, source_ekin))
                kw_count += 1
    print(f"  Parsed {kw_count} keyword entries")

    # Build sections summary
    print("Building sections summary...")
    c.execute("""
        INSERT INTO sections
        SELECT section_number, zk, COUNT(*) as card_count,
               MAX(LENGTH(luhmann_number)) as max_depth,
               MIN(ekin) as first_ekin
        FROM cards
        WHERE section_number != '' AND card_type = 'Notizzettel'
        GROUP BY section_number, zk
        ORDER BY zk, CAST(section_number AS INTEGER)
    """)

    conn.commit()

    # Print summary
    print("\n" + "="*60)
    print("DATABASE SUMMARY")
    print("="*60)

    for label, query in [
        ("Total cards", "SELECT COUNT(*) FROM cards"),
        ("Cards with text", "SELECT COUNT(*) FROM cards WHERE has_text = 1"),
        ("ZK I cards", "SELECT COUNT(*) FROM cards WHERE zk = 1"),
        ("ZK II cards", "SELECT COUNT(*) FROM cards WHERE zk = 2"),
        ("Notizzettel", "SELECT COUNT(*) FROM cards WHERE card_type = 'Notizzettel'"),
        ("Registerzettel", "SELECT COUNT(*) FROM cards WHERE card_type = 'Registerzettel'"),
        ("Bibliographiezettel", "SELECT COUNT(*) FROM cards WHERE card_type = 'Bibliographiezettel'"),
        ("Total references", "SELECT COUNT(*) FROM refs"),
        ("Resolved references", "SELECT COUNT(*) FROM refs WHERE target_ekin != ''"),
        ("Unique source cards", "SELECT COUNT(DISTINCT source_ekin) FROM refs"),
        ("Unique target cards", "SELECT COUNT(DISTINCT target_ekin) FROM refs WHERE target_ekin != ''"),
        ("Keywords parsed", "SELECT COUNT(*) FROM keywords"),
        ("Sections (ZK I)", "SELECT COUNT(*) FROM sections WHERE zk = 1"),
        ("Sections (ZK II)", "SELECT COUNT(*) FROM sections WHERE zk = 2"),
    ]:
        c.execute(query)
        val = c.fetchone()[0]
        print(f"  {label}: {val:,}")

    # Top 10 most referenced cards
    print("\nTop 10 most-referenced cards (in-degree):")
    c.execute("""
        SELECT ekin, luhmann_number, title, in_degree, zk
        FROM cards ORDER BY in_degree DESC LIMIT 10
    """)
    for row in c.fetchall():
        print(f"  [{row[4]}] {row[1]:>15} ({row[3]:>3} refs) — {row[2]}")

    # Top 10 cards with most outgoing references
    print("\nTop 10 Sammelverweise (out-degree):")
    c.execute("""
        SELECT ekin, luhmann_number, title, out_degree, zk
        FROM cards ORDER BY out_degree DESC LIMIT 10
    """)
    for row in c.fetchall():
        print(f"  [{row[4]}] {row[1]:>15} ({row[3]:>3} refs) — {row[2]}")

    db_size = DB_PATH.stat().st_size / (1024*1024)
    print(f"\nDatabase: {DB_PATH} ({db_size:.1f} MB)")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    build()
