#!/bin/bash
set -e

BASE_URL="http://127.0.0.1:8080"

echo "=== 🚀 HITL Pipeline Chatbot ==="

while true; do
  echo ""
  read -p "💬 Enter your prompt (or type 'exit' to quit): " PROMPT
  if [[ "$PROMPT" == "exit" ]]; then
    echo "👋 Goodbye!"
    break
  fi

  # 1. Start pipeline (manager handles general vs requirement)
  echo -e "\n=== Sending prompt to /manager ==="
  RESP=$(curl -s -X POST "$BASE_URL/manager" \
    -H "Content-Type: application/json" \
    -d "{\"prompt\":\"$PROMPT\"}")
  echo "$RESP" | jq .

  REQ_ID=$(echo "$RESP" | jq -r '.req_id // empty')
  STAGE=$(echo "$RESP" | jq -r '.next_stage // empty')
  STATUS=$(echo "$RESP" | jq -r '.status // empty')

  # If this was a general question (not a pipeline run)
  if [[ "$REQ_ID" == "" && "$STATUS" == "" ]]; then
    echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "Do you want to know anything else? How can I help?"')"
    continue
  fi

  # If pipeline started
  while true; do
    if [[ "$STAGE" == "null" || "$STAGE" == "" ]]; then
      echo "=== Pipeline ended unexpectedly ==="
      break
    fi

    case $STAGE in
      testcases)
        echo -e "\n➡️  Do you want to continue creating test cases and validate them against ISO? (y/n)"
        read -p "> " ACTION
        if [[ "$ACTION" != "y" ]]; then
          RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
            -H "Content-Type: application/json" \
            -d "{\"stage\":\"$STAGE\",\"req_id\":\"$REQ_ID\",\"user_action\":\"stop\"}")
          echo "$RESP" | jq .
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "Hi there 👋, how can I help?"')"
          break
        fi
        RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
          -H "Content-Type: application/json" \
          -d "{\"stage\":\"testcases\",\"req_id\":\"$REQ_ID\",\"user_action\":\"continue\"}")
        echo "$RESP" | jq .
        STATUS=$(echo "$RESP" | jq -r '.status // empty')
        if [[ "$STATUS" == "STOPPED" || "$STATUS" == "COMPLETE" ]]; then
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message')"
          break
        fi
        TC_IDS=$(echo "$RESP" | jq -c '.test_case_ids')
        STAGE=$(echo "$RESP" | jq -r '.next_stage')
        ;;

      samples_junit)
        echo -e "\n➡️  Do you want to continue generating samples and Java JUnit files? (y/n)"
        read -p "> " ACTION
        if [[ "$ACTION" != "y" ]]; then
          RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
            -H "Content-Type: application/json" \
            -d "{\"stage\":\"$STAGE\",\"req_id\":\"$REQ_ID\",\"user_action\":\"stop\"}")
          echo "$RESP" | jq .
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "Hi there 👋, how can I help?"')"
          break
        fi
        RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
          -H "Content-Type: application/json" \
          -d "{\"stage\":\"samples_junit\",\"req_id\":\"$REQ_ID\",\"test_case_ids\":$TC_IDS,\"user_action\":\"continue\"}")
        echo "$RESP" | jq .
        STATUS=$(echo "$RESP" | jq -r '.status // empty')
        if [[ "$STATUS" == "STOPPED" || "$STATUS" == "COMPLETE" ]]; then
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message')"
          break
        fi
        STAGE=$(echo "$RESP" | jq -r '.next_stage')
        ;;

      test_results)
        echo -e "\n➡️  Do you want to continue collecting test execution results? (y/n)"
        read -p "> " ACTION
        if [[ "$ACTION" != "y" ]]; then
          RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
            -H "Content-Type: application/json" \
            -d "{\"stage\":\"$STAGE\",\"req_id\":\"$REQ_ID\",\"user_action\":\"stop\"}")
          echo "$RESP" | jq .
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "Hi there 👋, how can I help?"')"
          break
        fi
        RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
          -H "Content-Type: application/json" \
          -d "{\"stage\":\"test_results\",\"req_id\":\"$REQ_ID\",\"user_action\":\"continue\"}")
        echo "$RESP" | jq .
        STATUS=$(echo "$RESP" | jq -r '.status // empty')
        if [[ "$STATUS" == "STOPPED" || "$STATUS" == "COMPLETE" ]]; then
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message')"
          break
        fi
        STAGE=$(echo "$RESP" | jq -r '.next_stage')
        ;;

      jira)
        echo -e "\n➡️  Do you want to move all test cases and results into Jira? (y/n)"
        read -p "> " ACTION
        if [[ "$ACTION" != "y" ]]; then
          RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
            -H "Content-Type: application/json" \
            -d "{\"stage\":\"$STAGE\",\"req_id\":\"$REQ_ID\",\"user_action\":\"stop\"}")
          echo "$RESP" | jq .
          echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "Hi there 👋, how can I help?"')"
          break
        fi
        RESP=$(curl -s -X POST "$BASE_URL/pipeline/continue" \
          -H "Content-Type: application/json" \
          -d "{\"stage\":\"jira\",\"req_id\":\"$REQ_ID\",\"test_case_ids\":$TC_IDS,\"user_action\":\"continue\"}")
        echo "$RESP" | jq .
        STATUS=$(echo "$RESP" | jq -r '.status // empty')
        echo -e "\n🤖 $(echo "$RESP" | jq -r '.message // "✅ Pipeline finished. Do you want to start a new request?"')"
        break
        ;;

      *)
        echo "Unknown stage: $STAGE"
        break
        ;;
    esac
  done
done

