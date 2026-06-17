#!/bin/bash
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"stub-session"}
{"type":"assistant","message":{"content":[{"type":"text","text":"I forgot the verdict line."}]}}
{"type":"result","subtype":"success","is_error":false}
EOF
exit 0
