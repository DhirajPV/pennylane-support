#!/usr/bin/env bash
# The API end to end, asserted rather than printed: every step compares real status
# codes and response fields to what the lifecycle rules say they should be, and stops
# at the first one that disagrees.
#
#   make demo                          against the running stack
#   bash scripts/walkthrough.sh URL    against something else
#
# Re-runnable on a database that has already been used: the handles it creates carry
# $RANDOM, and the one number that depends on a fresh seed (which CONV_ id comes next)
# is checked relative to the first conversation this run opens, not against CONV_0801.
# Needs curl and jq.
set -u

BASE="${1:-http://localhost:8000}"
J='Content-Type: application/json'

ASKER="demo_asker_$RANDOM"
NEWCOMER="demo_novice_$RANDOM"
HELPER='gpu_user'             # seeded learner
STAFF='pennylane_support'     # seeded staff
MOD='community_mod'           # seeded support

STEP='setup'
CHECKS=0

step() { STEP="$1"; printf '\n== %s\n' "$1"; }

fail() {
    printf '\nwalkthrough FAILED at [%s]\n  %s\n' "$STEP" "$*" >&2
    exit 1
}

# expect <label> <expected> <actual>
expect() {
    CHECKS=$((CHECKS + 1))
    [ "$2" = "$3" ] || fail "$1: expected '$2', got '$3'"
    printf '  ok  %-44s %s\n' "$1" "$3"
}

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
field() { printf '%s' "$1" | jq -r "$2"; }

# 'CONV_0801' -> 'CONV_0802'. 10# because the padding would otherwise read as octal,
# and %04d because the id formatter pads to a minimum of four, not a fixed four.
next_external_id() {
    printf 'CONV_%04d' "$((10#${1#CONV_} + 1))"
}

step '0. two fresh handles, so the run does not collide with the last one'
expect 'create asker' 201 "$(code -X POST "$BASE/users" -H "$J" -d "{\"handle\":\"$ASKER\"}")"

step '1. the asker opens a conversation on a published challenge'
CREATED=$(curl -s -X POST "$BASE/conversations" -H "X-User: $ASKER" -H "$J" -d '{
  "challenge_id": 1,
  "topic": "Zero gradients in a two-qubit ansatz",
  "body": "My cost function is flat and qml.grad returns all zeros.",
  "category": "PennyLane Help"}')
ID=$(field "$CREATED" .id)
FIRST_EXTERNAL=$(field "$CREATED" .external_id)
[ "$ID" != 'null' ] || fail "create returned no id: $CREATED"
expect 'status'         'open' "$(field "$CREATED" .status)"
expect 'message_count'  1      "$(field "$CREATED" .message_count)"
expect 'author'         "$ASKER" "$(field "$CREATED" .author.handle)"
# The identifier rule itself: CONV_ plus the bigint id, padded to at least four.
expect 'external_id matches id' "$(printf 'CONV_%04d' "$ID")" "$FIRST_EXTERNAL"

step '2. a second learner replies; the reply answers with itself'
HEADERS=$(mktemp)
REPLY=$(curl -s -D "$HEADERS" -X POST "$BASE/conversations/$ID/messages" \
  -H "X-User: $HELPER" -H "$J" -d '{"body":"Your first layer has no trainable parameters."}')
LOCATION=$(grep -i '^location:' "$HEADERS" | tr -d '\r' | sed 's/^[Ll]ocation: *//')
rm -f "$HEADERS"
ANSWER=$(field "$REPLY" .id)
expect 'sequence_no'    2       "$(field "$REPLY" .sequence_no)"
expect 'is_internal'    'false' "$(field "$REPLY" .is_internal)"
expect 'Location'       "/conversations/$ID/messages?after_sequence=1&limit=1" "$LOCATION"
AT_LOCATION=$(curl -s "$BASE$LOCATION")
expect 'Location holds the reply' "$ANSWER" "$(field "$AT_LOCATION" '.items[0].id')"
expect 'and only the reply'       1         "$(field "$AT_LOCATION" '.items | length')"
expect 'thread message_count'     2         "$(field "$(curl -s "$BASE/conversations/$ID")" .message_count)"

step '3. an internal note: staff see it, nobody else can read, accept or react to it'
NOTE=$(field "$(curl -s -X POST "$BASE/conversations/$ID/messages" -H "X-User: $STAFF" -H "$J" \
  -d '{"body":"Internal: third report of this today.","is_internal":true}')" .id)
expect 'staff message_count'     3 "$(field "$(curl -s "$BASE/conversations/$ID" -H "X-User: $STAFF")" .message_count)"
expect 'anonymous message_count' 2 "$(field "$(curl -s "$BASE/conversations/$ID")" .message_count)"
expect 'learner posts internal'  403 "$(code -X POST "$BASE/conversations/$ID/messages" -H "X-User: $HELPER" -H "$J" -d '{"body":"x","is_internal":true}')"
expect 'accepting the note'      422 "$(code -X POST "$BASE/conversations/$ID/messages/$NOTE/accept" -H "X-User: $STAFF")"
expect 'learner reacts to note'  404 "$(code -X POST "$BASE/conversations/$ID/messages/$NOTE/reactions" -H "X-User: $HELPER" -H "$J" -d '{"type":"helpful"}')"

step '4. reacting twice is one reaction'
for _ in 1 2; do
    REACTED=$(curl -s -X POST "$BASE/conversations/$ID/messages/$ANSWER/reactions" \
      -H "X-User: $ASKER" -H "$J" -d '{"type":"helpful"}')
    expect 'helpful_count' 1           "$(field "$REACTED" .helpful_count)"
    expect 'my_reactions'  'helpful'     "$(field "$REACTED" '.my_reactions | join(",")')"
done

step '5. the asker accepts the reply: open -> answered, not resolved'
ACCEPTED=$(curl -s -X POST "$BASE/conversations/$ID/messages/$ANSWER/accept" -H "X-User: $ASKER")
expect 'status'              'answered' "$(field "$ACCEPTED" .status)"
expect 'accepted_message_id' "$ANSWER"  "$(field "$ACCEPTED" .accepted_message_id)"
expect 'resolved_at'         'null'     "$(field "$ACCEPTED" .resolved_at)"

step '6. staff assign, resolve, then close; the resolution time is the first one'
MOD_ID=$(field "$(curl -s "$BASE/users?role=support" -H "X-User: $MOD")" ".items[] | select(.handle==\"$MOD\") | .id")
ASSIGNED=$(curl -s -X PATCH "$BASE/conversations/$ID" -H "X-User: $MOD" -H "$J" -d "{\"assignee_id\":$MOD_ID}")
expect 'assignee' "$MOD" "$(field "$ASSIGNED" .assignee.handle)"
RESOLVED=$(curl -s -X PATCH "$BASE/conversations/$ID" -H "X-User: $MOD" -H "$J" -d '{"status":"resolved"}')
RESOLVED_AT=$(field "$RESOLVED" .resolved_at)
expect 'status'           'resolved' "$(field "$RESOLVED" .status)"
expect 'resolved_at set'  'true'     "$([ "$RESOLVED_AT" != 'null' ] && echo true || echo false)"
CLOSED=$(curl -s -X PATCH "$BASE/conversations/$ID" -H "X-User: $MOD" -H "$J" -d '{"status":"closed"}')
expect 'status'                 'closed'       "$(field "$CLOSED" .status)"
expect 'resolved_at preserved'  "$RESOLVED_AT" "$(field "$CLOSED" .resolved_at)"
# The queue and the backlog count the same threads two different ways: resolved_at IS NULL
# and no assignee here, non-terminal status and no assignee in insights. They must agree.
QUEUE=$(curl -s "$BASE/conversations?sort=priority&unassigned=true&unresolved=true&limit=1" -H "X-User: $MOD")
INSIGHTS=$(curl -s "$BASE/insights" -H "X-User: $MOD")
expect 'queue total = backlog.unassigned' "$(field "$INSIGHTS" .backlog.unassigned)" "$(field "$QUEUE" .total)"

step '7. the author cannot reopen what staff closed'
expect 'author reopens' 403 "$(code -X PATCH "$BASE/conversations/$ID" -H "X-User: $ASKER" -H "$J" -d '{"status":"open"}')"

step '8. deleting a message is idempotent, and staff only'
expect 'staff deletes note'    204 "$(code -X DELETE "$BASE/conversations/$ID/messages/$NOTE" -H "X-User: $STAFF")"
expect 'and again'             204 "$(code -X DELETE "$BASE/conversations/$ID/messages/$NOTE" -H "X-User: $STAFF")"
expect 'learner deletes reply' 403 "$(code -X DELETE "$BASE/conversations/$ID/messages/$ANSWER" -H "X-User: $HELPER")"

step '9. soft delete hides the thread from everyone but staff, and restore brings it back'
expect 'staff deletes'          204 "$(code -X DELETE "$BASE/conversations/$ID" -H "X-User: $MOD")"
expect 'and again'              204 "$(code -X DELETE "$BASE/conversations/$ID" -H "X-User: $MOD")"
expect 'anonymous read'         404 "$(code "$BASE/conversations/$ID")"
expect 'staff include_deleted'  200 "$(code "$BASE/conversations/$ID?include_deleted=true" -H "X-User: $MOD")"
expect 'write to a deleted one' 409 "$(code -X POST "$BASE/conversations/$ID/messages" -H "X-User: $MOD" -H "$J" -d '{"body":"x"}')"
RESTORED=$(curl -s -X POST "$BASE/conversations/$ID/restore" -H "X-User: $MOD")
expect 'restored status'           'closed'  "$(field "$RESTORED" .status)"
expect 'accepted answer survives'  "$ANSWER" "$(field "$RESTORED" .accepted_message_id)"
expect 'anonymous read'            200       "$(code "$BASE/conversations/$ID")"

step '10. signup is open, handles are unique, and the next conversation takes the next id'
expect 'create newcomer'    201 "$(code -X POST "$BASE/users" -H "$J" -d "{\"handle\":\"$NEWCOMER\"}")"
expect 'the same handle'    409 "$(code -X POST "$BASE/users" -H "$J" -d "{\"handle\":\"$NEWCOMER\"}")"
SECOND=$(curl -s -X POST "$BASE/conversations" -H "X-User: $NEWCOMER" -H "$J" -d '{
  "challenge_id": 4,
  "topic": "Which device should I start on",
  "body": "Fresh account, first question.",
  "category": "PennyLane Help"}')
expect 'external_id is the next one' "$(next_external_id "$FIRST_EXTERNAL")" "$(field "$SECOND" .external_id)"

step '11. the refusals'
expect 'unknown handle on a read'  401 "$(code "$BASE/conversations" -H 'X-User: nobody_here')"
expect 'no header on a write'      401 "$(code -X POST "$BASE/conversations" -H "$J" -d '{"challenge_id":1,"topic":"t","body":"b","category":"Demos"}')"
expect 'blank body'                422 "$(code -X POST "$BASE/conversations/$ID/messages" -H "X-User: $HELPER" -H "$J" -d '{"body":"   "}')"
expect 'message from another thread' 404 "$(code -X POST "$BASE/conversations/1/messages/$ANSWER/accept" -H "X-User: $STAFF")"
expect 'learner assigns'           403 "$(code -X PATCH "$BASE/conversations/$ID" -H "X-User: $ASKER" -H "$J" -d "{\"assignee_id\":$MOD_ID}")"

printf '\nwalkthrough: %d checks passed\n' "$CHECKS"
