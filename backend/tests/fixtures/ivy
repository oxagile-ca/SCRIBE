#!/usr/bin/env bash
# Stub for `deploycli` CLI used in quartermaster integration tests.
# Reads QM_STUB_ENV_EXISTS env var to decide whether env appears to exist.

subcommand="$1"
shift

case "$subcommand $1" in
  "deploy ls")
    if [ "$QM_STUB_ENV_EXISTS" = "1" ]; then
      echo "[{\"name\":\"$3\"}]"
    else
      echo "[]"
    fi
    ;;
  "deploy create")
    echo "Created env"
    ;;
  "deploy renew")
    echo "Renewed env"
    ;;
  "deploy deploy")
    echo "Deployed"
    ;;
  "deploy build")
    echo "Build queued"
    ;;
  *)
    echo "Unknown stub command: $subcommand $@" >&2
    exit 1
    ;;
esac
exit 0
