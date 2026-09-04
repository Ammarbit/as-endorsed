#!/usr/bin/env sh
# Push the current main branch to a Hugging Face Space, swapping in the Space README.
#   deploy/push-hf-space.sh <user>/<space>
set -eu
SPACE="${1:?usage: deploy/push-hf-space.sh <user>/<space>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
git -C "$ROOT" worktree add --detach "$TMP/space" main >/dev/null
cp "$ROOT/deploy/hf-space-README.md" "$TMP/space/README.md"
git -C "$TMP/space" add README.md
git -C "$TMP/space" -c user.name=deploy -c user.email=deploy@local commit -q -m "Space README"
git -C "$TMP/space" push --force "https://huggingface.co/spaces/$SPACE" HEAD:main
git -C "$ROOT" worktree remove --force "$TMP/space"
echo "pushed to https://huggingface.co/spaces/$SPACE — build logs on the Space page; app at https://$(echo "$SPACE" | tr '/' '-').hf.space"
