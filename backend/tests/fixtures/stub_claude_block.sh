#!/bin/bash
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"stub-session"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Found a bug.\n\nVERDICT: BLOCK missing null check on user input"}]}}
{"type":"result","subtype":"success","is_error":false}
EOF
exit 0
