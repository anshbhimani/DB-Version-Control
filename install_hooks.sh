#!/bin/sh
# Installs a post-commit hook that triggers an immediate schema snapshot check
# on every commit, independent of watch.py's polling interval.
set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOK_PATH="$REPO_ROOT/.git/hooks/post-commit"

cat > "$HOOK_PATH" <<'EOF'
#!/bin/sh
cd "$(git rev-parse --show-toplevel)/cli"
python3 watch.py --once
EOF

chmod +x "$HOOK_PATH"
echo "Installed post-commit hook at $HOOK_PATH"
