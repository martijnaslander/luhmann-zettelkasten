#!/usr/bin/env python3
"""Export all data for the web visualization.

Generates:
- web/data/vis_nodes.json — top 2000 most-connected nodes with positions, folgezettel tree
- web/data/vis_edges.json — edges between top nodes
- web/data/search_index.json — ALL 43K+ cards with text (truncated for search)
- web/data/card_texts.json — full text for top 2000 nodes
- web/data/keywords.json — cleaned Schlagwortregister
- web/data/network_stats.json — summary stats
- web/data/sections.json — section overview with names
"""

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).parent
DB_PATH = PROJECT / "luhmann.db"
WEB_DATA = PROJECT / "web" / "data"
WEB_DATA.mkdir(parents=True, exist_ok=True)

# Known section names from Schmidt's description
SECTION_NAMES = {
    # ZK I (108 sections, here are the major ones)
    "1:7": "Wert der Organisation",
    "1:12": "Organisation und Recht",
    "1:17": "Ideologie",
    "1:21": "Führerschaft / Führung",
    "1:28": "Das Wesen der Organisation",
    "1:32": "Methode / Theorie-Praxis",
    "1:45": "Autorität",
    "1:57": "Wissenschaft",
    "1:60": "Entscheidungen",
    "1:62": "Rolle",
    "1:76": "Kausalität",
    "1:83": "Leistungssteigerung",
    # ZK II (11 main sections)
    "2:1": "Organisationstheorie",
    "2:2": "Funktionalismus",
    "2:3": "Entscheidungstheorie",
    "2:4": "Amt",
    "2:5": "Formale/informale Ordnung",
    "2:6": "Souveränität/Staat",
    "2:7": "Einzelbegriffe/Einzelprobleme",
    "2:8": "Wirtschaft",
    "2:9": "Ad hoc Notizen",
    "2:10": "Archaische Gesellschaften",
    "2:11": "Hochkulturen",
    "2:21": "Organisationstheorie (erweitert)",
    "2:32": "Methode",
    "2:35": "Erziehung",
    "2:42": "Recht",
    "2:44": "Sprache",
    "2:52": "Persönlichkeit/Sozialordnung",
    "2:54": "Gesellschaftstheorie",
    "2:532": "Politik",
    "2:533": "Kunst",
    "2:536": "Religion",
}


def get_conn():
    return sqlite3.connect(str(DB_PATH))


def export_all():
    conn = get_conn()
    c = conn.cursor()

    # 1. SEARCH INDEX — all cards with text, truncated
    print("Exporting search index (all cards with text)...")
    c.execute("""
        SELECT ekin, luhmann_number, title, SUBSTR(text, 1, 200) as preview,
               zk, section_number, in_degree, out_degree, card_type
        FROM cards WHERE has_text = 1
        ORDER BY in_degree + out_degree DESC
    """)
    search_cards = []
    for row in c.fetchall():
        search_cards.append({
            "ekin": row[0], "luhmann_number": row[1], "title": row[2],
            "text": row[3], "zk": row[4], "section": row[5],
            "in_degree": row[6], "out_degree": row[7], "type": row[8]
        })
    with open(WEB_DATA / "search_index.json", "w") as f:
        json.dump(search_cards, f, ensure_ascii=False)
    print(f"  {len(search_cards)} cards ({(WEB_DATA / 'search_index.json').stat().st_size // 1024}KB)")

    # 2. VIS NODES — top 2000 most connected, with layout positions
    print("Exporting visualization nodes...")
    c.execute("""
        SELECT ekin, luhmann_number, title, zk, section_number, card_type,
               in_degree, out_degree, image_id, abteilung
        FROM cards
        WHERE card_type = 'Notizzettel'
        ORDER BY in_degree + out_degree DESC
        LIMIT 2000
    """)
    top_nodes = {}
    for row in c.fetchall():
        top_nodes[row[0]] = {
            "id": row[0], "luhmann_number": row[1], "title": row[2],
            "zk": row[3], "section": row[4], "type": row[5],
            "in_degree": row[6], "out_degree": row[7],
            "image_id": row[8], "abteilung": row[9],
        }

    # Add folgezettel parent/children for each node
    print("  Adding folgezettel relationships...")
    for ekin, node in top_nodes.items():
        ln = node["luhmann_number"]
        if not ln:
            continue
        # Find parent
        c.execute("SELECT ekin, luhmann_number FROM cards WHERE normalized_number = ? AND zk = ? LIMIT 1",
                  (get_parent_norm(ln), node["zk"]))
        parent = c.fetchone()
        node["parent_ekin"] = parent[0] if parent else None
        node["parent_number"] = parent[1] if parent else None

        # Find children (cards whose parent is this card)
        norm = ln.replace('/', '-').replace(',', '-')
        c.execute("""
            SELECT ekin, luhmann_number FROM cards
            WHERE parent_number = ? AND zk = ? AND card_type = 'Notizzettel'
            ORDER BY luhmann_number LIMIT 20
        """, (ln, node["zk"]))
        node["children"] = [{"ekin": r[0], "number": r[1]} for r in c.fetchall()]

    # Compute initial positions based on section (circular layout)
    print("  Computing initial positions...")
    assign_positions(top_nodes)

    vis_nodes = list(top_nodes.values())
    with open(WEB_DATA / "vis_nodes.json", "w") as f:
        json.dump(vis_nodes, f, ensure_ascii=False)
    print(f"  {len(vis_nodes)} nodes")

    # 3. VIS EDGES
    print("Exporting edges...")
    top_ekins = set(top_nodes.keys())
    c.execute("""
        SELECT source_ekin, target_ekin, ref_type FROM refs
        WHERE target_ekin != '' AND ref_type IN ('fernverweis', 'cross_zk', 'same_section')
    """)
    vis_edges = []
    for row in c.fetchall():
        if row[0] in top_ekins and row[1] in top_ekins:
            vis_edges.append({"source": row[0], "target": row[1], "type": row[2]})
    with open(WEB_DATA / "vis_edges.json", "w") as f:
        json.dump(vis_edges, f, ensure_ascii=False)
    print(f"  {len(vis_edges)} edges")

    # 4. CARD TEXTS — full text for vis nodes
    print("Exporting full card texts...")
    card_texts = {}
    for ekin in top_ekins:
        c.execute("SELECT text FROM cards WHERE ekin = ?", (ekin,))
        row = c.fetchone()
        if row and row[0]:
            card_texts[ekin] = row[0]
    with open(WEB_DATA / "card_texts.json", "w") as f:
        json.dump(card_texts, f, ensure_ascii=False)
    print(f"  {len(card_texts)} texts ({(WEB_DATA / 'card_texts.json').stat().st_size // 1024}KB)")

    # 5. KEYWORDS — cleaned Schlagwortregister
    print("Exporting keywords...")
    c.execute("""
        SELECT keyword, GROUP_CONCAT(DISTINCT target_ekin) as ekins,
               COUNT(*) as cnt
        FROM keywords
        WHERE target_ekin IS NOT NULL AND target_ekin != ''
        GROUP BY keyword
        HAVING cnt >= 2
        ORDER BY cnt DESC
    """)
    keywords = []
    for row in c.fetchall():
        kw = clean_keyword(row[0])
        if kw and len(kw) > 2:
            ekins = [e for e in row[1].split(',') if e]
            keywords.append({"keyword": kw, "cards": ekins, "count": row[2]})
    # Deduplicate
    seen = set()
    unique_kw = []
    for kw in keywords:
        if kw["keyword"].lower() not in seen:
            seen.add(kw["keyword"].lower())
            unique_kw.append(kw)
    with open(WEB_DATA / "keywords.json", "w") as f:
        json.dump(unique_kw[:500], f, ensure_ascii=False)
    print(f"  {len(unique_kw[:500])} keywords")

    # 6. SECTIONS overview
    print("Exporting sections...")
    c.execute("""
        SELECT section_number, zk, COUNT(*) as cnt,
               MAX(in_degree) as max_in,
               SUM(CASE WHEN has_text = 1 THEN 1 ELSE 0 END) as with_text
        FROM cards
        WHERE card_type = 'Notizzettel' AND section_number != ''
        GROUP BY section_number, zk
        ORDER BY zk, CAST(section_number AS INTEGER)
    """)
    sections = []
    for row in c.fetchall():
        key = f"{row[1]}:{row[0]}"
        sections.append({
            "section": row[0], "zk": row[1], "count": row[2],
            "max_in_degree": row[3], "with_text": row[4],
            "name": SECTION_NAMES.get(key, "")
        })
    with open(WEB_DATA / "sections.json", "w") as f:
        json.dump(sections, f, ensure_ascii=False)
    print(f"  {len(sections)} sections")

    # 7. NETWORK STATS
    print("Exporting stats...")
    stats = {}
    for label, query in [
        ("total_cards", "SELECT COUNT(*) FROM cards"),
        ("cards_with_text", "SELECT COUNT(*) FROM cards WHERE has_text = 1"),
        ("total_refs", "SELECT COUNT(*) FROM refs"),
        ("resolved_refs", "SELECT COUNT(*) FROM refs WHERE target_ekin != ''"),
        ("fernverweise", "SELECT COUNT(*) FROM refs WHERE ref_type = 'fernverweis'"),
        ("cross_zk", "SELECT COUNT(*) FROM refs WHERE ref_type = 'cross_zk'"),
        ("folgezettel", "SELECT COUNT(*) FROM refs WHERE ref_type = 'folgezettel'"),
        ("same_section", "SELECT COUNT(*) FROM refs WHERE ref_type = 'same_section'"),
        ("keywords", "SELECT COUNT(DISTINCT keyword) FROM keywords"),
        ("zk1_cards", "SELECT COUNT(*) FROM cards WHERE zk = 1"),
        ("zk2_cards", "SELECT COUNT(*) FROM cards WHERE zk = 2"),
    ]:
        c.execute(query)
        stats[label] = c.fetchone()[0]

    # Top 50 hubs
    c.execute("""
        SELECT ekin, luhmann_number, title, in_degree, out_degree, zk, section_number,
               SUBSTR(text, 1, 200)
        FROM cards
        WHERE card_type = 'Notizzettel'
        ORDER BY in_degree DESC LIMIT 50
    """)
    stats["top_50_hubs"] = [{
        "ekin": r[0], "number": r[1], "title": r[2],
        "in_degree": r[3], "out_degree": r[4], "zk": r[5],
        "section": r[6], "preview": r[7]
    } for r in c.fetchall()]

    with open(WEB_DATA / "network_stats.json", "w") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    conn.close()

    print("\n" + "="*60)
    print("ALL WEB DATA EXPORTED")
    print("="*60)
    for f in sorted(WEB_DATA.glob("*.json")):
        size = f.stat().st_size
        unit = "KB" if size < 1024*1024 else "MB"
        val = size // 1024 if unit == "KB" else size // (1024*1024)
        print(f"  {f.name}: {val} {unit}")


def clean_keyword(kw):
    """Clean messy keyword strings from the Schlagwortregister."""
    if not kw:
        return ""
    # Remove page-range headers like "Ga – Gel (1)" or "Mu – My"
    kw = re.sub(r'^[A-Z][a-z]?\s*[–—-]\s*[A-Z][a-z].*?\)', '', kw).strip()
    kw = re.sub(r'^[A-Z][a-z]?\s*[–—-]\s*[A-Z][a-z]+\s*', '', kw).strip()
    # Remove leading numbers/bullets
    kw = re.sub(r'^\d+[\.\)]\s*', '', kw).strip()
    # Remove trailing colons, semicolons
    kw = kw.rstrip(':;,. ')
    return kw


def get_parent_norm(luhmann_number):
    """Get normalized parent number for folgezettel tree."""
    ln = luhmann_number
    if not ln:
        return ""
    norm = ln.replace('/', '-').replace(',', '-')
    # Remove trailing letter: 1-1a → 1-1
    if re.search(r'[a-z]$', norm):
        return norm[:-1]
    # Remove trailing digit after letter: 1-1a2 → 1-1a
    m = re.search(r'^(.+[a-z])\d+$', norm)
    if m:
        return m.group(1)
    # Remove trailing number: 1-2 → 1 (but only the last segment)
    m = re.match(r'^(.+-)(\d+)$', norm)
    if m:
        base, digit = m.group(1), int(m.group(2))
        if digit > 1:
            return f"{base}{digit - 1}"
        return base.rstrip('-')
    return ""


def assign_positions(nodes_dict):
    """Assign initial positions based on ZK and section for better layout."""
    # Group by ZK and section
    groups = defaultdict(list)
    for ekin, node in nodes_dict.items():
        key = f"{node['zk']}:{node['section']}"
        groups[key].append(ekin)

    # Sort groups by size
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))

    # Arrange in a circle, ZK I on left, ZK II on right
    zk1_groups = [(k, v) for k, v in sorted_groups if k.startswith("1:")]
    zk2_groups = [(k, v) for k, v in sorted_groups if k.startswith("2:")]

    # ZK I: left hemisphere
    arrange_hemisphere(nodes_dict, zk1_groups, center_x=-800, center_y=0, radius=600)
    # ZK II: right hemisphere
    arrange_hemisphere(nodes_dict, zk2_groups, center_x=800, center_y=0, radius=700)


def arrange_hemisphere(nodes_dict, groups, center_x, center_y, radius):
    """Arrange groups in a semicircle, nodes within each group in a small cluster."""
    if not groups:
        return
    n_groups = len(groups)
    for i, (key, ekins) in enumerate(groups):
        # Group center on the circle
        angle = (i / n_groups) * 2 * math.pi
        gx = center_x + radius * math.cos(angle)
        gy = center_y + radius * math.sin(angle)

        # Spread nodes within group
        n = len(ekins)
        group_radius = min(150, 20 + n * 2)
        for j, ekin in enumerate(ekins):
            a2 = (j / max(n, 1)) * 2 * math.pi
            nodes_dict[ekin]["x"] = gx + group_radius * math.cos(a2) + (hash(ekin) % 20 - 10)
            nodes_dict[ekin]["y"] = gy + group_radius * math.sin(a2) + (hash(ekin) % 20 - 10)


if __name__ == "__main__":
    export_all()
