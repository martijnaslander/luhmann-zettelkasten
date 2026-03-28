#!/bin/bash
# Download Niklas Luhmann's complete Zettelkasten (~73.700 kaarten)
# Bron: https://niklas-luhmann-archiv.de/
# API: https://v0.api.niklas-luhmann-archiv.de/ZK/search
#
# ZK I (1951-1962): ~22.079 kaarten
# ZK II (1963-1996): ~51.636 kaarten

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/data"
mkdir -p "$OUT/zk1" "$OUT/zk2"

CHUNK=10000
API="https://v0.api.niklas-luhmann-archiv.de/ZK/search"
DELAY=3  # seconden tussen requests, wees netjes

download_zk() {
    local zk_id="$1"
    local zk_dir="$OUT/zk${zk_id}"
    local page=1

    echo "=== Downloading ZK ${zk_id} ==="

    while true; do
        local outfile="$zk_dir/zk${zk_id}_page${page}.json"

        if [[ -f "$outfile" ]]; then
            echo "  Page $page already exists, skipping"
            page=$((page + 1))
            continue
        fi

        # Build the q parameter as JSON
        local q="{\"page\":${page},\"rows\":${CHUNK},\"fulltext\":\"\",\"fuzzy\":false,\"FTSearchMode\":\"and\",\"zettelnummer\":\"\",\"zettelnummerSearchMode\":\"starts-with\",\"areas\":[],\"ref\":\"\",\"zks\":[\"${zk_id}\"]}"

        local encoded_q
        encoded_q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$q'))")

        echo "  Fetching page $page (rows $((($page - 1) * $CHUNK + 1))-$(($page * $CHUNK)))..."

        local http_code
        http_code=$(curl -s -w "%{http_code}" -o "$outfile.tmp" "${API}?q=${encoded_q}")

        if [[ "$http_code" != "200" ]]; then
            echo "  ERROR: HTTP $http_code on page $page"
            rm -f "$outfile.tmp"
            return 1
        fi

        # Check if we got results
        local count
        count=$(python3 -c "import json; d=json.load(open('$outfile.tmp')); print(len(d.get('results', [])))")
        local total
        total=$(python3 -c "import json; d=json.load(open('$outfile.tmp')); print(d.get('numberOfResults', 0))")

        mv "$outfile.tmp" "$outfile"
        echo "  Page $page: $count results (total in ZK $zk_id: $total)"

        if [[ "$count" -eq 0 ]] || [[ $(( page * CHUNK )) -ge "$total" ]]; then
            echo "  ZK $zk_id complete!"
            break
        fi

        page=$((page + 1))
        echo "  Waiting ${DELAY}s..."
        sleep "$DELAY"
    done
}

# Download both Zettelkasten
download_zk 1
download_zk 2

echo ""
echo "=== Merging into single files ==="

# Merge all pages per ZK into one file
for zk_id in 1 2; do
    python3 - "$OUT/zk${zk_id}" "$OUT/luhmann_zk${zk_id}_complete.json" <<'PYEOF'
import json, sys, glob, os

indir = sys.argv[1]
outfile = sys.argv[2]

all_results = []
total = 0

for f in sorted(glob.glob(os.path.join(indir, "*.json"))):
    with open(f) as fh:
        data = json.load(fh)
        total = data.get("numberOfResults", total)
        all_results.extend(data.get("results", []))

merged = {
    "numberOfResults": total,
    "downloadedResults": len(all_results),
    "results": all_results
}

with open(outfile, "w", encoding="utf-8") as fh:
    json.dump(merged, fh, ensure_ascii=False, indent=2)

print(f"  ZK {os.path.basename(indir)}: {len(all_results)} kaarten -> {outfile}")
PYEOF
done

# Grand total
python3 - "$OUT" <<'PYEOF'
import json, sys, os

total = 0
for f in ["luhmann_zk1_complete.json", "luhmann_zk2_complete.json"]:
    path = os.path.join(sys.argv[1], f)
    if os.path.exists(path):
        with open(path) as fh:
            data = json.load(fh)
            n = len(data.get("results", []))
            total += n

print(f"\n=== TOTAAL: {total} kaarten gedownload ===")
PYEOF

echo ""
echo "Bestanden in: $OUT/"
ls -lh "$OUT"/luhmann_zk*_complete.json 2>/dev/null
