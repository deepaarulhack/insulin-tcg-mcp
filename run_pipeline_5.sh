#!/bin/bash
set -e

BASE_URL="http://127.0.0.1:8080"

echo "=== 🚀 HITL Pipeline Demo ==="
read -p "Enter requirement prompt: " PROMPT

# 1. Start pipeline (manager -> requirement)
echo -e "\n=== Starting pipeline with /manager ==="
REQ_JSON=$(curl -s -X POST "$BASE_URL/manager" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":\"$PROMPT\"}")
echo "$REQ_JSON" | jq .

REQ_ID=$(echo "$REQ_JSON" | jq -r '.req_id')

# interactive loop
STAGE=$(echo "$REQ_JSON" | jq -r '.next_stage')

while true; do
  if [[ "$STAGE" == "null" || "$STAGE" == "" ]]; then
    echo "=== Pipeline ended ==="
    break
  fi

  echo -e "\n=== Next stage: $STAGE ==="
  read -p "Type 'c' to continue, 's' to stop: " ACTION

  if [[ "$ACTION" == "s" ]]; then
    RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
      -H "Content-Type: application/json" \
      -d "{\"stage\":\"$STAGE\",\"req_id\":\"$REQ_ID\",\"user_action\":\"stop\"}")
    echo "$RESP" | jq .
    echo "=== Pipeline stopped ==="
    break
  fi

  if [[ "$STAGE" == "testcases" ]]; then
    RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
      -H "Content-Type: application/json" \
      -d "{\"stage\":\"testcases\",\"req_id\":\"$REQ_ID\",\"user_action\":\"continue\"}")
    echo "$RESP" | jq .
    TC_IDS=$(echo "$RESP" | jq -c '.test_case_ids')
    STAGE=$(echo "$RESP" | jq -r '.next_stage')

  elif [[ "$STAGE" == "samples_junit" ]]; then
    RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
      -H "Content-Type: application/json" \
      -d "{\"stage\":\"samples_junit\",\"req_id\":\"$REQ_ID\",\"test_case_ids\":$TC_IDS,\"user_action\":\"continue\"}")
    echo "$RESP" | jq .
    STAGE=$(echo "$RESP" | jq -r '.next_stage')

  elif [[ "$STAGE" == "test_results" ]]; then
    RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
      -H "Content-Type: application/json" \
      -d "{\"stage\":\"test_results\",\"req_id\":\"$REQ_ID\",\"user_action\":\"continue\"}")
    echo "$RESP" | jq .
    STAGE=$(echo "$RESP" | jq -r '.next_stage')

  elif [[ "$STAGE" == "jira" ]]; then
    RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
      -H "Content-Type: application/json" \
      -d "{\"stage\":\"jira\",\"req_id\":\"$REQ_ID\",\"test_case_ids\":$TC_IDS,\"user_action\":\"continue\"}")
    echo "$RESP" | jq .
    echo "=== Pipeline complete ✅ ==="
    break
  else
    echo "Unknown stage: $STAGE"
    break
  fi
done

