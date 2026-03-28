#!/usr/bin/env python3
"""Blok 2+3: Network analysis of Luhmann's Zettelkasten.

- Classify reference types (Folgezettel vs Fernverweis)
- Build directed graph
- Community detection
- Hub analysis, bridges, paths
- Export for visualization
"""

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).parent
DB_PATH = PROJECT / "luhmann.db"
OUTPUT = PROJECT / "output"
OUTPUT.mkdir(exist_ok=True)


def get_conn():
    return sqlite3.connect(str(DB_PATH))


def classify_references():
    """Classify refs as folgezettel (nearby) vs fernverweis (distant)."""
    print("Classifying references...")
    conn = get_conn()
    c = conn.cursor()

    # Add ref_type column if not exists
    try:
        c.execute("ALTER TABLE refs ADD COLUMN ref_type TEXT DEFAULT 'unknown'")
    except:
        pass

    # Get all resolved refs with source and target luhmann numbers
    c.execute("""
        SELECT refs.id, refs.source_ekin, refs.target_ekin,
               s.luhmann_number, t.luhmann_number, s.zk, t.zk
        FROM refs
        JOIN cards s ON refs.source_ekin = s.ekin
        JOIN cards t ON refs.target_ekin = t.ekin
        WHERE refs.target_ekin != ''
    """)

    folge = 0
    fern = 0
    cross_zk = 0
    same_section = 0

    for row in c.fetchall():
        ref_id, src_ekin, tgt_ekin, src_num, tgt_num, src_zk, tgt_zk = row

        if src_zk != tgt_zk:
            ref_type = "cross_zk"
            cross_zk += 1
        elif is_folgezettel(src_num, tgt_num):
            ref_type = "folgezettel"
            folge += 1
        elif same_section_check(src_num, tgt_num):
            ref_type = "same_section"
            same_section += 1
        else:
            ref_type = "fernverweis"
            fern += 1

        c.execute("UPDATE refs SET ref_type = ? WHERE id = ?", (ref_type, ref_id))

    conn.commit()
    conn.close()

    total = folge + fern + cross_zk + same_section
    print(f"  Folgezettel (nearby): {folge:,} ({folge*100//total}%)")
    print(f"  Same section: {same_section:,} ({same_section*100//total}%)")
    print(f"  Fernverweis (distant): {fern:,} ({fern*100//total}%)")
    print(f"  Cross-ZK: {cross_zk:,} ({cross_zk*100//total}%)")
    return {"folgezettel": folge, "same_section": same_section, "fernverweis": fern, "cross_zk": cross_zk}


def is_folgezettel(src, tgt):
    """Check if target is a direct neighbor in Folgezettel tree."""
    if not src or not tgt:
        return False
    # One is prefix of the other (parent-child)
    s = src.replace(',', '-').replace('/', '-')
    t = tgt.replace(',', '-').replace('/', '-')
    if s.startswith(t) or t.startswith(s):
        diff = abs(len(s) - len(t))
        if diff <= 2:
            return True
    return False


def same_section_check(src, tgt):
    """Check if both cards are in the same top-level section."""
    if not src or not tgt:
        return False
    s_sec = re.match(r'^(\d+)', src)
    t_sec = re.match(r'^(\d+)', tgt)
    if s_sec and t_sec:
        return s_sec.group(1) == t_sec.group(1)
    return False


def build_graph_data():
    """Build graph and compute metrics using pure Python (no networkx dependency)."""
    print("\nBuilding graph...")
    conn = get_conn()
    c = conn.cursor()

    # Only use Fernverweise and cross-ZK for the interesting network
    # (Folgezettel are tree structure, not creative links)
    c.execute("""
        SELECT source_ekin, target_ekin, ref_type
        FROM refs
        WHERE target_ekin != '' AND ref_type IN ('fernverweis', 'cross_zk', 'same_section')
    """)

    edges = []
    out_neighbors = defaultdict(set)
    in_neighbors = defaultdict(set)
    all_nodes = set()

    for row in c.fetchall():
        src, tgt, rtype = row
        edges.append((src, tgt, rtype))
        out_neighbors[src].add(tgt)
        in_neighbors[tgt].add(src)
        all_nodes.add(src)
        all_nodes.add(tgt)

    print(f"  Nodes: {len(all_nodes):,}")
    print(f"  Edges (non-Folgezettel): {len(edges):,}")

    # Degree analysis
    in_degrees = {n: len(in_neighbors.get(n, set())) for n in all_nodes}
    out_degrees = {n: len(out_neighbors.get(n, set())) for n in all_nodes}
    total_degrees = {n: in_degrees[n] + out_degrees[n] for n in all_nodes}

    # Top hubs by in-degree (most referenced)
    top_in = sorted(in_degrees.items(), key=lambda x: -x[1])[:50]
    # Top by out-degree (Sammelverweise)
    top_out = sorted(out_degrees.items(), key=lambda x: -x[1])[:50]
    # Top by total degree (most connected)
    top_total = sorted(total_degrees.items(), key=lambda x: -x[1])[:50]

    # Get card details for top nodes
    top_ekins = set(e for e, _ in top_in[:50]) | set(e for e, _ in top_out[:50]) | set(e for e, _ in top_total[:50])
    card_info = {}
    for ekin in top_ekins:
        c.execute("SELECT luhmann_number, title, zk, section_number, text FROM cards WHERE ekin = ?", (ekin,))
        row = c.fetchone()
        if row:
            card_info[ekin] = {
                "luhmann_number": row[0],
                "title": row[1],
                "zk": row[2],
                "section": row[3],
                "text_preview": (row[4] or "")[:200]
            }

    # Community detection via label propagation (simple, no dependencies)
    print("  Running community detection (label propagation)...")
    communities = detect_communities(all_nodes, out_neighbors, in_neighbors)

    # Community sizes
    comm_sizes = Counter(communities.values())
    print(f"  Found {len(comm_sizes)} communities")
    print(f"  Largest 10:")
    for comm_id, size in comm_sizes.most_common(10):
        # Get section distribution for this community
        comm_nodes = [n for n, cid in communities.items() if cid == comm_id]
        sections = []
        for n in comm_nodes[:100]:
            c.execute("SELECT section_number, zk FROM cards WHERE ekin = ?", (n,))
            r = c.fetchone()
            if r and r[0]:
                sections.append(f"ZK{r[1]}:{r[0]}")
        sec_counts = Counter(sections).most_common(3)
        sec_str = ", ".join(f"{s}({c})" for s, c in sec_counts)
        print(f"    Community {comm_id}: {size:,} nodes — top sections: {sec_str}")

    # Section-level analysis
    print("\nSection analysis...")
    c.execute("""
        SELECT section_number, zk, COUNT(*) as cnt
        FROM cards
        WHERE card_type = 'Notizzettel' AND section_number != ''
        GROUP BY section_number, zk
        ORDER BY cnt DESC
        LIMIT 20
    """)
    print("  Top 20 sections by size:")
    for row in c.fetchall():
        sec, zk, cnt = row
        print(f"    ZK {zk} Section {sec}: {cnt:,} cards")

    # Cross-ZK references
    c.execute("SELECT COUNT(*) FROM refs WHERE ref_type = 'cross_zk'")
    cross_count = c.fetchone()[0]
    print(f"\n  Cross-ZK references: {cross_count}")

    conn.close()

    # Export results
    print("\nExporting results...")

    results = {
        "summary": {
            "total_nodes": len(all_nodes),
            "total_edges_non_folge": len(edges),
            "communities": len(comm_sizes),
            "cross_zk_refs": cross_count,
        },
        "top_50_most_referenced": [
            {
                "ekin": ekin,
                "in_degree": deg,
                "out_degree": out_degrees.get(ekin, 0),
                **card_info.get(ekin, {})
            }
            for ekin, deg in top_in[:50]
        ],
        "top_50_most_outgoing": [
            {
                "ekin": ekin,
                "out_degree": deg,
                "in_degree": in_degrees.get(ekin, 0),
                **card_info.get(ekin, {})
            }
            for ekin, deg in top_out[:50]
        ],
        "community_sizes": [
            {"id": cid, "size": size}
            for cid, size in comm_sizes.most_common(50)
        ],
    }

    with open(OUTPUT / "network_stats.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Export graph for visualization (top 2000 most connected nodes + their edges)
    print("Exporting visualization data...")
    top_nodes = set(e for e, _ in sorted(total_degrees.items(), key=lambda x: -x[1])[:2000])

    vis_nodes = []
    for ekin in top_nodes:
        info = {}
        c2 = get_conn().cursor()
        c2.execute("SELECT luhmann_number, title, zk, section_number, card_type FROM cards WHERE ekin = ?", (ekin,))
        row = c2.fetchone()
        if row:
            info = {"luhmann_number": row[0], "title": row[1], "zk": row[2], "section": row[3], "type": row[4]}
        vis_nodes.append({
            "id": ekin,
            "in_degree": in_degrees.get(ekin, 0),
            "out_degree": out_degrees.get(ekin, 0),
            "community": communities.get(ekin, -1),
            **info
        })

    vis_edges = []
    for src, tgt, rtype in edges:
        if src in top_nodes and tgt in top_nodes:
            vis_edges.append({"source": src, "target": tgt, "type": rtype})

    with open(OUTPUT / "vis_nodes.json", "w") as f:
        json.dump(vis_nodes, f, ensure_ascii=False)
    with open(OUTPUT / "vis_edges.json", "w") as f:
        json.dump(vis_edges, f, ensure_ascii=False)

    print(f"  Exported {len(vis_nodes)} nodes and {len(vis_edges)} edges for visualization")

    # Print top 10 most referenced with details
    print("\n" + "="*70)
    print("THE BACKBONE OF LUHMANN'S THINKING")
    print("Top 20 most-referenced cards (Fernverweise only)")
    print("="*70)
    for i, item in enumerate(results["top_50_most_referenced"][:20], 1):
        num = item.get("luhmann_number", "?")
        title = item.get("title", "")
        in_d = item.get("in_degree", 0)
        out_d = item.get("out_degree", 0)
        zk = item.get("zk", "?")
        preview = item.get("text_preview", "")[:100]
        print(f"\n  {i:2}. [{zk}] {num} — in:{in_d} out:{out_d}")
        print(f"      {title}")
        if preview:
            print(f"      \"{preview}...\"")

    return results


def detect_communities(nodes, out_neighbors, in_neighbors, max_iter=20):
    """Simple label propagation community detection."""
    # Initialize: each node is its own community
    labels = {n: i for i, n in enumerate(nodes)}

    # Combine neighbors (undirected)
    all_neighbors = defaultdict(set)
    for n in nodes:
        all_neighbors[n] = out_neighbors.get(n, set()) | in_neighbors.get(n, set())

    for iteration in range(max_iter):
        changed = 0
        for node in nodes:
            neighbors = all_neighbors[node]
            if not neighbors:
                continue
            # Count neighbor labels
            label_counts = Counter(labels[n] for n in neighbors if n in labels)
            if not label_counts:
                continue
            # Pick most common
            best_label = label_counts.most_common(1)[0][0]
            if labels[node] != best_label:
                labels[node] = best_label
                changed += 1

        if changed == 0:
            break

    # Renumber communities sequentially
    unique_labels = {}
    counter = 0
    result = {}
    for node, label in labels.items():
        if label not in unique_labels:
            unique_labels[label] = counter
            counter += 1
        result[node] = unique_labels[label]

    return result


def fix_schlagwort():
    """Better Schlagwortregister parsing — look at actual card content."""
    print("\nRe-parsing Schlagwortregister...")
    conn = get_conn()
    c = conn.cursor()

    # Clear old keywords
    c.execute("DELETE FROM keywords")

    # Get all register cards with text
    c.execute("""
        SELECT ekin, text, abteilung FROM cards
        WHERE card_type = 'Registerzettel' AND has_text = 1
    """)

    ref_pattern = re.compile(r'(\d{1,3}[/,]\d{1,4}[a-z0-9/,]*)')
    kw_count = 0

    for row in c.fetchall():
        ekin, text, abt = row
        if not text:
            continue

        lines = text.split('\n')
        current_keyword = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Find references in this line
            refs_found = ref_pattern.findall(line)

            if refs_found:
                # Extract keyword: text before the first reference
                first_ref_pos = line.find(refs_found[0])
                keyword_part = line[:first_ref_pos].strip().rstrip(':').rstrip(',').strip()
                if keyword_part and len(keyword_part) > 1 and len(keyword_part) < 80:
                    current_keyword = keyword_part

                if current_keyword:
                    for ref in refs_found:
                        norm = ref.replace('/', '-').replace(',', '-')
                        c.execute("""
                            INSERT INTO keywords (keyword, target_raw, target_normalized, target_ekin, source_ekin)
                            VALUES (?, ?, ?, (SELECT ekin FROM cards WHERE normalized_number = ? LIMIT 1), ?)
                        """, (current_keyword, ref, norm, norm, ekin))
                        kw_count += 1
            elif line and not refs_found:
                # Might be a keyword without refs on this line
                if len(line) < 80 and not line[0].isdigit():
                    current_keyword = line.rstrip(':').strip()

    conn.commit()

    # Report
    c.execute("SELECT COUNT(*) FROM keywords")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT keyword) FROM keywords")
    unique = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM keywords WHERE target_ekin IS NOT NULL AND target_ekin != ''")
    resolved = c.fetchone()[0]

    print(f"  Total keyword entries: {total}")
    print(f"  Unique keywords: {unique}")
    print(f"  Resolved to cards: {resolved}")

    # Show sample
    c.execute("SELECT keyword, COUNT(*) as cnt FROM keywords GROUP BY keyword ORDER BY cnt DESC LIMIT 15")
    print("  Top 15 keywords:")
    for row in c.fetchall():
        print(f"    {row[0]}: {row[1]} references")

    conn.close()


if __name__ == "__main__":
    fix_schlagwort()
    ref_types = classify_references()
    results = build_graph_data()

    print("\n" + "="*70)
    print("BLOK 2+3 COMPLETE")
    print("="*70)
    print(f"Reference types: {json.dumps(ref_types, indent=2)}")
    print(f"Output files in: {OUTPUT}/")
    print(f"  network_stats.json — full analysis")
    print(f"  vis_nodes.json — top 2000 nodes for visualization")
    print(f"  vis_edges.json — edges between top nodes")
