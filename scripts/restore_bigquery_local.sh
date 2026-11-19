#!/usr/bin/env bash
# Restore a local backup made by scripts/export_bigquery_local.sh into a (new) BigQuery project.
#   CLOUDSDK_CONFIG=$PWD/.gcloud PROJECT=<new-project> bash scripts/restore_bigquery_local.sh data/backup/<stamp> [--dry-run]
# 1) creates each dataset (US) if missing, 2) loads every table from its gzip JSONL parts with the saved schema
# (first part --replace, the rest appended; bq accepts one local file per load), 3) checks row counts against
# MANIFEST.tsv, 4) recreates views with the old project id rewritten (OLD_PROJECT, default the hackathon project),
# retrying so views that depend on other views succeed. Models are NOT restored here: retrain them (docs/LOCAL_BACKUP_RECOVERY.md).
set -uo pipefail
BACKUP="${1:?usage: $0 data/backup/<stamp> [--dry-run]}"; DRY="${2:-}"
PROJECT="${PROJECT:?set PROJECT=<target project id>}"
LOCATION="${BQ_LOCATION:-US}"; OLD_PROJECT="${OLD_PROJECT:-qwiklabs-gcp-04-3e58e01275b8}"
MANIFEST="$BACKUP/MANIFEST.tsv"; [ -f "$MANIFEST" ] || { echo "no MANIFEST.tsv in $BACKUP" >&2; exit 1; }
run() { if [ "$DRY" = "--dry-run" ]; then printf 'DRY: %s\n' "$*"; else "$@"; fi; }
bqx() { run bq --project_id="$PROJECT" --location="$LOCATION" "$@"; }
FAIL=0

for DSDIR in "$BACKUP"/*/; do
  DS="$(basename "$DSDIR")"
  bq --project_id="$PROJECT" show "$PROJECT:$DS" >/dev/null 2>&1 || bqx mk --dataset --location="$LOCATION" "$PROJECT:$DS"
  for SCHEMA in "$DSDIR"schemas/*.json; do
    [ -e "$SCHEMA" ] || continue
    T="$(basename "$SCHEMA" .json)"; FIRST=1
    for PART in "$DSDIR$T"/*.json.gz; do
      [ -e "$PART" ] || continue
      if [ $FIRST = 1 ]; then MODE=--replace; FIRST=0; else MODE=--noreplace; fi
      if [ "$DRY" = "--dry-run" ]; then bqx load $MODE --source_format=NEWLINE_DELIMITED_JSON "$DS.$T" "$PART" "$SCHEMA"
      else bqx load $MODE --source_format=NEWLINE_DELIMITED_JSON "$DS.$T" "$PART" "$SCHEMA" >/dev/null || { echo "LOAD FAILED $DS.$T ($PART)"; FAIL=1; }; fi
    done
    WANT="$(awk -F'\t' -v d="$DS" -v t="$T" '$1==d && $2==t && $3=="table"{print $4}' "$MANIFEST")"
    if [ "$DRY" = "--dry-run" ]; then echo "check $DS.$T rows == $WANT"; continue; fi
    GOT="$(bq --project_id="$PROJECT" show --format=json "$DS.$T" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("numRows","0"))')"
    [ "$GOT" = "$WANT" ] && echo "ok   $DS.$T $GOT rows" || { echo "MISMATCH $DS.$T got $GOT want $WANT"; FAIL=1; }
  done
done

# Views last (they read the tables above); some read other views, so retry up to 3 passes.
PENDING="$(ls "$BACKUP"/*/views/*.sql 2>/dev/null)"
for PASS in 1 2 3; do
  NEXT=""
  for V in $PENDING; do
    DS="$(basename "$(dirname "$(dirname "$V")")")"; NAME="$(basename "$V" .sql)"
    SQL="CREATE OR REPLACE VIEW \`$PROJECT.$DS.$NAME\` AS $(sed "s/$OLD_PROJECT/$PROJECT/g" "$V")"
    if [ "$DRY" = "--dry-run" ]; then echo "DRY: create view $DS.$NAME"; continue; fi
    if printf '%s' "$SQL" | bq --project_id="$PROJECT" --location="$LOCATION" query --nouse_legacy_sql >/dev/null 2>&1; then echo "ok   view $DS.$NAME"; else NEXT="$NEXT $V"; fi
  done
  PENDING="$NEXT"; [ -z "$PENDING" ] && break
done
for V in $PENDING; do echo "VIEW FAILED $V"; FAIL=1; done
[ $FAIL = 0 ] && echo ">> restore complete into $PROJECT" || { echo ">> restore finished WITH ERRORS"; exit 1; }
