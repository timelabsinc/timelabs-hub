#!/bin/bash
# Describe undescribed Drop photos for search. Safe to re-run; the endpoint only
# ever returns files that aren't indexed yet. A zero-added batch is a transient
# model hiccup, not "done" — retry a few times before giving up, and only stop
# on remaining==0 or repeated empties.
LOG=/tmp/dropindex.log
empties=0
for i in $(seq 1 60); do
  r=$(curl -s --max-time 400 -X POST http://127.0.0.1:8903/search/index \
      -H "Content-Type: application/json" -d '{"batch":4}')
  echo "$(date +%H:%M:%S) $r" >> "$LOG"
  echo "$r" | grep -q '"remaining": 0' && { echo "$(date +%H:%M:%S) DONE" >> "$LOG"; break; }
  if echo "$r" | grep -q '"added": 0'; then
    empties=$((empties+1))
    [ "$empties" -ge 4 ] && { echo "$(date +%H:%M:%S) GAVE UP after 4 empty batches" >> "$LOG"; break; }
  else
    empties=0
  fi
done
