#!/usr/bin/env bash
# Download BigQuery datasets to THIS machine (the lab project, its datasets and buckets disappear after the event).
#   CLOUDSDK_CONFIG=$PWD/.gcloud PROJECT=<id> bash scripts/export_bigquery_local.sh [dataset ...]
# Default datasets: a4i_climate (original challenge tables first) a4i_climate_work_v2 a4i_scratch_chicago.
# Per table: gzip JSONL (bq extract via the project's bucket) + schema JSON + row-count check. Views: their SQL.
# Models: EXPORT MODEL files (best effort; every model is also rebuildable from sql/ + these tables).
# Output: data/backup/<UTC stamp>/<dataset>/...  plus MANIFEST.tsv. Uses the bq/gcloud CLIs only (no ADC needed).
set -uo pipefail
. "$(dirname "$0")/_env.sh"
STAMP="$(date -u +%Y%m%dT%H%M)"
BUCKET="${EXPORT_BUCKET:-gs://a4i-$PROJECT}"
OUT="$ROOT/data/backup/$STAMP"; GCS="$BUCKET/local-export/$STAMP"
DATASETS=("${@:-a4i_climate a4i_climate_work_v2 a4i_scratch_chicago}"); DATASETS=(${DATASETS[*]})
ORIGINAL="heat_observations station_daily_temps tract_demographics"
mkdir -p "$OUT"; MANIFEST="$OUT/MANIFEST.tsv"
printf 'dataset\tobject\tkind\tbq_rows\tlocal_rows\tstatus\n' > "$MANIFEST"
bqj() { bq --project_id="$PROJECT" --location="$BQ_LOCATION" "$@"; }

for DS in "${DATASETS[@]}"; do
  mkdir -p "$OUT/$DS/schemas" "$OUT/$DS/views" "$OUT/$DS/models"
  LIST="$(bqj ls --max_results 1000 --format=json "$DS")"
  TABLES="$(printf '%s' "$LIST" | "$PY" -c 'import sys,json;[print(t["tableReference"]["tableId"]) for t in json.load(sys.stdin) if t["type"]=="TABLE"]')"
  VIEWS="$(printf '%s' "$LIST" | "$PY" -c 'import sys,json;[print(t["tableReference"]["tableId"]) for t in json.load(sys.stdin) if t["type"]=="VIEW"]')"
  # original challenge tables first
  ORDERED="$( (for t in $ORIGINAL; do printf '%s\n' "$TABLES" | grep -x "$t"; done; printf '%s\n' "$TABLES" | grep -v -x -E "$(echo $ORIGINAL | tr ' ' '|')") | sed '/^$/d')"
  for T in $ORDERED; do
    bqj show --schema --format=prettyjson "$DS.$T" > "$OUT/$DS/schemas/$T.json"
    ROWS="$(bqj show --format=json "$DS.$T" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("numRows","0"))')"
    mkdir -p "$OUT/$DS/$T"
    if bqj extract --destination_format=NEWLINE_DELIMITED_JSON --compression=GZIP "$DS.$T" "$GCS/$DS/$T/part-*.json.gz" >/dev/null 2>&1 \
       && gcloud storage cp -q "$GCS/$DS/$T/*" "$OUT/$DS/$T/" >/dev/null 2>&1; then
      LOCAL="$(cat "$OUT/$DS/$T"/*.json.gz 2>/dev/null | gunzip -c | wc -l | tr -d ' ')"
      [ "$LOCAL" = "$ROWS" ] && S=ok || S=ROWCOUNT_MISMATCH
    else LOCAL=0; S=EXTRACT_FAILED; fi
    printf '%s\t%s\ttable\t%s\t%s\t%s\n' "$DS" "$T" "$ROWS" "$LOCAL" "$S" | tee -a "$MANIFEST"
  done
  for V in $VIEWS; do
    bqj show --format=json "$DS.$V" | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["view"]["query"])' > "$OUT/$DS/views/$V.sql"
    printf '%s\t%s\tview\t-\t-\tsql_saved\n' "$DS" "$V" | tee -a "$MANIFEST"
  done
  for M in $(bqj ls -m --format=json "$DS" 2>/dev/null | "$PY" -c 'import sys,json;[print(m["modelReference"]["modelId"]) for m in json.load(sys.stdin)]' 2>/dev/null); do
    bqj show --model --format=prettyjson "$DS.$M" > "$OUT/$DS/models/$M.metadata.json"
    if printf "EXPORT MODEL \`%s.%s.%s\` OPTIONS(URI='%s/%s/models/%s/')" "$PROJECT" "$DS" "$M" "$GCS" "$DS" "$M" \
         | bqj query --nouse_legacy_sql >/dev/null 2>&1 \
       && gcloud storage cp -q -r "$GCS/$DS/models/$M" "$OUT/$DS/models/" >/dev/null 2>&1; then S=exported; else S=metadata_only; fi
    printf '%s\t%s\tmodel\t-\t-\t%s\n' "$DS" "$M" "$S" | tee -a "$MANIFEST"
  done
done
echo ">> backup: $OUT  ($(du -sh "$OUT" | cut -f1)); problems: $(grep -c -E 'FAILED|MISMATCH' "$MANIFEST")"
