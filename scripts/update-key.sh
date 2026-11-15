#!/bin/bash
# The key that signs Claude Sync updates. Its private half lives only in this Mac's keychain (item
# claude-sync-update-key) and never shows up on screen, in arguments or in files; the app carries the
# public half (AppInfo.updatePublicKey) and installs only updates it signed.
#   bash scripts/update-key.sh new           make the key (once; refuses when one exists) and print the public key
#   bash scripts/update-key.sh public        print the public key
#   bash scripts/update-key.sh sign <file>   write <file>.sig
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
item=${CLAUDE_SYNC_UPDATE_KEY_ITEM:-claude-sync-update-key}
math=("$root/scripts/update-key.swift")

private_key() { security find-generic-password -s "$item" -w; }

case "${1:-}" in
  new)
    if security find-generic-password -s "$item" >/dev/null 2>&1; then
      echo "✗ the key already exists ($item); a new one would stop installed apps from taking updates" >&2
      exit 1
    fi
    keys=$(swift "${math[@]}" new)
    private=${keys%%$'\n'*}
    public=${keys##*$'\n'}
    # security reads the command on stdin, so the key is never an argument of a running program.
    printf 'add-generic-password -s %s -a claude-sync -l "Claude Sync update key" -w %s\n' "$item" "$private" \
      | security -i >/dev/null 2>&1
    unset private keys
    [ "$(private_key | swift "${math[@]}" public)" = "$public" ] || { echo "✗ could not keep the key in the keychain" >&2; exit 1; }
    echo "$public"
    ;;
  public)
    private_key | swift "${math[@]}" public
    ;;
  sign)
    file=${2:?usage: update-key.sh sign <file>}
    private_key | swift "${math[@]}" sign "$file" > "$file.sig.tmp"
    mv "$file.sig.tmp" "$file.sig"
    ;;
  *)
    echo "usage: update-key.sh new | public | sign <file>" >&2
    exit 1
    ;;
esac
