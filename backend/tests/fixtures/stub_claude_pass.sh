#!/bin/bash
# Stub for claude -p --output-format stream-json
# Emits a minimal stream-json transcript ending with VERDICT: PASS.
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"stub-session"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Checking evidence...\n\nVERDICT: PASS"}]}}
{"type":"result","subtype":"success","is_error":false}
EOF
exit 0
