#!/bin/bash
# Store API keys in macOS Keychain for adaptive/cli.py provider fallback credentials
# Usage: ./store-api-keys.sh [store <key-name> <value> | --list | --get <key-name>]
# Keys are stored under service 'love-unlimited' in macOS Keychain

set -euo pipefail

SERVICE="love-unlimited"
KNOWN_KEYS=("OPENAI_API_KEY" "ANTHROPIC_API_KEY" "GROQ_API_KEY" "TOGETHER_API_KEY" "OLLAMA_CLOUD_API_KEY")

usage() {
    echo "Usage: $0 [store <key-name> <value> | --list | --get <key-name>]"
    echo "Known keys: ${KNOWN_KEYS[*]}"
    exit 1
}

validate_key() {
    local key="$1"
    for known_key in "${KNOWN_KEYS[@]}"; do
        if [[ "$known_key" == "$key" ]]; then
            return 0
        fi
    done
    echo "Error: Unknown key '$key'" >&2
    return 1
}

case "${1:-}" in
    store)
        if [[ $# -ne 3 ]]; then
            usage
        fi
        validate_key "$2" || exit 1
        security add-generic-password -s "$SERVICE" -a "$2" -w "$3" -U
        echo "Stored $2"
        ;;
    --list)
        security find-generic-password -s "$SERVICE" -ga "" 2>&1 | grep "acct\"" | sed -E 's/.*"acct"<blob>="(.*?)".*/\1/'
        ;;
    --get)
        if [[ $# -ne 2 ]]; then
            usage
        fi
        validate_key "$2" || exit 1
        security find-generic-password -s "$SERVICE" -a "$2" -w
        ;;
    *)
        usage
        ;;
esac