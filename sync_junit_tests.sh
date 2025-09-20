#!/bin/bash
# Sync generated JUnit tests from GCS into repo test folder

# === CONFIG ===
REQ_ID=$1
REPO_PATH=~/insulin-repo/Personal_Insulin_Pump-Integrated_System
GCS_BUCKET=gs://insulin-tcg-mcp-tcg

if [ -z "$REQ_ID" ]; then
  echo "Usage: ./sync_junit_tests.sh <REQ_ID>"
  exit 1
fi

DEST_DIR="$REPO_PATH/src/test/java/com/insulinpump/tests"

echo "📂 Creating destination folder: $DEST_DIR"
mkdir -p "$DEST_DIR"

echo "⬇️ Downloading JUnit files for $REQ_ID from $GCS_BUCKET"
gsutil -m cp "$GCS_BUCKET/artifacts/junit/$REQ_ID/*.java" "$DEST_DIR/"

echo "✅ Synced tests to $DEST_DIR"
echo "👉 Now run: cd $REPO_PATH && mvn test"

