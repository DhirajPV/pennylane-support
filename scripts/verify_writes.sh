#!/usr/bin/env bash
# Write-path verification for the PennyLane support API.
# Run against a FRESHLY seeded stack:  docker compose down -v && docker compose up --build -d && sleep 20
# Usage: bash verify_writes.sh [base_url]
set -u
BASE="${1:-http://localhost:8000}"
J='Content-Type: application/json'
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; echo; }
say()  { printf '\n== %s\n' "$*"; }

say "1. create as a learner  (expect CONV_0801 on a once-seeded db, open, message_count 1)"
C=$(curl -s -X POST "$BASE/conversations" -H 'X-User: quantum_learner42' -H "$J" \
  -d '{"challenge_id":1,"topic":"Zero gradients in a two-qubit ansatz","body":"My gradients come out zero.","category":"PennyLane Help"}')
echo "$C" | jq -c '{external_id, status, message_count}'
ID=$(echo "$C" | jq -r .id)
[ -n "$ID" ] && [ "$ID" != "null" ] || { echo "create failed: $C"; exit 1; }

say "2. public reply from another learner  (expect message_count 2)"
R=$(curl -s -X POST "$BASE/conversations/$ID/messages" -H 'X-User: gpu_user' -H "$J" -d '{"body":"Your first layer has no trainable parameters."}')
echo "$R" | jq -c '{message_count, last_activity_at}'
MID=$(echo "$R" | jq -r '.messages[-1].id')

say "3. internal note: staff -> message_count 3; learner posting internal -> 403; anonymous sees 2; accepting the note -> 422"
N=$(curl -s -X POST "$BASE/conversations/$ID/messages" -H 'X-User: pennylane_support' -H "$J" -d '{"body":"Internal: third report this week.","is_internal":true}')
echo "$N" | jq .message_count
NOTE=$(echo "$N" | jq -r '.messages[] | select(.is_internal) | .id')
code -X POST "$BASE/conversations/$ID/messages" -H 'X-User: gpu_user' -H "$J" -d '{"body":"x","is_internal":true}'
curl -s "$BASE/conversations/$ID" | jq .message_count
code -X POST "$BASE/conversations/$ID/messages/$NOTE/accept" -H 'X-User: pennylane_support'
code -X POST "$BASE/conversations/$ID/messages/$NOTE/reactions" -H 'X-User: gpu_user' -H "$J" -d '{"type":"helpful"}'   # learner can't see it: 404

say "4. react helpful twice  (expect helpful_count 1 both times, my_reactions [\"helpful\"])"
for i in 1 2; do
  curl -s -X POST "$BASE/conversations/$ID/messages/$MID/reactions" -H 'X-User: quantum_learner42' -H "$J" -d '{"type":"helpful"}' \
    | jq -c ".messages[] | select(.id==$MID) | {helpful_count, my_reactions}"
done

say "5. accept  (expect answered, accepted_message_id == $MID)"
curl -s -X POST "$BASE/conversations/$ID/messages/$MID/accept" -H 'X-User: quantum_learner42' | jq -c '{status, accepted_message_id}'

U=$(curl -s "$BASE/users?role=support" -H 'X-User: community_mod')
MOD=$(echo "$U" | jq -r 'if type=="array" then .[] else .items[] end | select(.handle=="community_mod") | .id')
echo "community_mod id: $MOD"
curl -s -X PATCH "$BASE/conversations/$ID" -H 'X-User: community_mod' -H "$J" -d "{\"assignee_id\":$MOD}" | jq -c '{assignee}'
curl -s -X PATCH "$BASE/conversations/$ID" -H 'X-User: community_mod' -H "$J" -d '{"status":"resolved"}' | jq -c '{status, resolved_at}'
curl -s -X PATCH "$BASE/conversations/$ID" -H 'X-User: community_mod' -H "$J" -d '{"status":"closed"}'   | jq -c '{status, resolved_at}'

say "7. author cannot reopen a closed thread  (expect 403)"
code -X PATCH "$BASE/conversations/$ID" -H 'X-User: quantum_learner42' -H "$J" -d '{"status":"open"}'

say "8. delete the internal note twice  (expect 204, 204); learner deleting a message (expect 403)"
code -X DELETE "$BASE/conversations/$ID/messages/$NOTE" -H 'X-User: pennylane_support'
code -X DELETE "$BASE/conversations/$ID/messages/$NOTE" -H 'X-User: pennylane_support'
code -X DELETE "$BASE/conversations/$ID/messages/$MID"  -H 'X-User: gpu_user'

say "9. delete the conversation twice (204, 204); anonymous 404; staff+include_deleted 200; write 409; restore 200; anonymous 200"
code -X DELETE "$BASE/conversations/$ID" -H 'X-User: community_mod'
code -X DELETE "$BASE/conversations/$ID" -H 'X-User: community_mod'
code "$BASE/conversations/$ID"
code "$BASE/conversations/$ID?include_deleted=true" -H 'X-User: community_mod'
code -X POST "$BASE/conversations/$ID/messages" -H 'X-User: community_mod' -H "$J" -d '{"body":"x"}'
curl -s -X POST "$BASE/conversations/$ID/restore" -H 'X-User: community_mod' | jq -c '{status, accepted_message_id}'
code "$BASE/conversations/$ID"

say "10. new user (photon_novice), duplicate (409), create as them (next external_id)"
curl -s -X POST "$BASE/users" -H "$J" -d '{"handle":"photon_novice"}' | jq .handle
code -X POST "$BASE/users" -H "$J" -d '{"handle":"photon_novice"}'
curl -s -X POST "$BASE/conversations" -H 'X-User: photon_novice' -H "$J" \
  -d '{"challenge_id":4,"topic":"Which device to start on","body":"Fresh account, first question.","category":"PennyLane Help"}' | jq .external_id

say "11. negatives: unknown handle 401; no header on a write 401; message from another conversation 404; learner PATCH assignee 403"
code "$BASE/conversations" -H 'X-User: nobody_here'
code -X POST "$BASE/conversations" -H "$J" -d '{"challenge_id":1,"topic":"t","body":"b","category":"Demos"}'
code -X POST "$BASE/conversations/1/messages/$MID/accept" -H 'X-User: pennylane_support'
code -X PATCH "$BASE/conversations/$ID" -H 'X-User: quantum_learner42' -H "$J" -d "{\"assignee_id\":$MOD}"