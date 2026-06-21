#!/bin/bash
# Stub: stream-json transcript with a result event carrying cost + usage.
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"stub-session","model":"claude-haiku-4-5"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Checking...\n\nVERDICT: PASS"}]}}
{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.0123,"duration_ms":4200,"session_id":"stub-session","usage":{"input_tokens":1200,"output_tokens":340,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}
EOF
exit 0
