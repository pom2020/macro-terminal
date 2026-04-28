#!/usr/bin/env bash
# bootstrap.sh — one-shot go-live script for macro-terminal.
#
# Run from inside the macro-terminal/ folder. Prerequisites:
#   * git installed
#   * gh CLI installed and authenticated (`gh auth login`)
#   * (recommended) a free FRED API key from
#     https://fredaccount.stlouisfed.org/apikeys
#
# Idempotent: safe to re-run; it skips steps that have already happened.

set -euo pipefail

cd "$(dirname "$0")"

bold()  { printf '\033[1m%s\033[0m\n' "$1"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$1"; }
err()   { printf '\033[31m✗\033[0m %s\n' "$1"; }
fatal() { err "$1"; exit 1; }

# ---------------------------------------------------------------------------
# 0. Prerequisites
# ---------------------------------------------------------------------------
bold "[0/8] Checking prerequisites"

command -v git >/dev/null  || fatal "git not installed"
command -v gh  >/dev/null  || fatal "gh CLI not installed (brew install gh)"
gh auth status >/dev/null 2>&1 || fatal "gh not authenticated — run: gh auth login"
command -v python3 >/dev/null || fatal "python3 not installed"

ok "git, gh (authenticated), python3 OK"

# ---------------------------------------------------------------------------
# 1. Inputs
# ---------------------------------------------------------------------------
bold "[1/8] Configuration"

# Repo name
default_repo="macro-terminal"
read -rp "GitHub repo name [$default_repo]: " REPO
REPO="${REPO:-$default_repo}"

# Visibility
read -rp "Visibility (public|private) [public]: " VIS
VIS="${VIS:-public}"
[[ "$VIS" == "public" || "$VIS" == "private" ]] || fatal "visibility must be public or private"

# FRED key
if [[ -z "${FRED_KEY:-}" ]]; then
  read -rsp "FRED API key (https://fredaccount.stlouisfed.org/apikeys, blank = skip): " FRED_KEY
  echo
fi

# GitHub user
GH_USER="$(gh api user -q .login)"
ok "Will create $GH_USER/$REPO ($VIS)"

# ---------------------------------------------------------------------------
# 2. Smoke test (skippable)
# ---------------------------------------------------------------------------
bold "[2/8] Local smoke test"

if [[ -n "$FRED_KEY" ]]; then
  read -rp "Run smoke test now? (y/N): " RUN_SMOKE
  if [[ "$RUN_SMOKE" =~ ^[Yy] ]]; then
    FRED_KEY="$FRED_KEY" bash smoke_test.sh \
      || fatal "smoke test failed — fix before pushing"
  else
    warn "skipped smoke test"
  fi
else
  warn "no FRED_KEY set — skipping smoke test (ETL will use keyless fallback)"
fi

# ---------------------------------------------------------------------------
# 3. git init + first commit
# ---------------------------------------------------------------------------
bold "[3/8] Initializing git"

if [[ ! -d .git ]]; then
  git init -b main
  ok "git repo initialized on main"
else
  ok "git repo already initialized"
fi

git add -A
if git diff --cached --quiet; then
  ok "no changes to commit"
else
  git commit -m "chore: initial commit (macro-terminal scaffold + seed data)"
  ok "initial commit created"
fi

# ---------------------------------------------------------------------------
# 4. Create GitHub repo + push
# ---------------------------------------------------------------------------
bold "[4/8] Creating GitHub repo"

if gh repo view "$GH_USER/$REPO" >/dev/null 2>&1; then
  ok "$GH_USER/$REPO already exists; ensuring origin is set"
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$GH_USER/$REPO.git"
  fi
  git push -u origin main || warn "push had nothing new"
else
  gh repo create "$REPO" "--$VIS" --source=. --push
  ok "repo created and pushed"
fi

# ---------------------------------------------------------------------------
# 5. Set FRED_KEY secret
# ---------------------------------------------------------------------------
bold "[5/8] Setting GitHub Actions secrets"

if [[ -n "$FRED_KEY" ]]; then
  gh secret set FRED_KEY --body "$FRED_KEY" --repo "$GH_USER/$REPO"
  ok "FRED_KEY stored as repo secret"
else
  warn "FRED_KEY not provided — ETL will use keyless CSV fallback"
fi

# ---------------------------------------------------------------------------
# 6. Enable Pages with GitHub Actions source
# ---------------------------------------------------------------------------
bold "[6/8] Enabling GitHub Pages"

# Try the modern API: build_type=workflow tells Pages to expect a workflow
# upload artifact (which is what .github/workflows/pages.yml provides).
if gh api -X POST "repos/$GH_USER/$REPO/pages" \
     -f build_type=workflow >/dev/null 2>&1; then
  ok "Pages enabled (Actions source)"
elif gh api -X PUT "repos/$GH_USER/$REPO/pages" \
     -f build_type=workflow >/dev/null 2>&1; then
  ok "Pages updated to Actions source"
else
  warn "Could not enable Pages via API. Do it manually:"
  warn "  https://github.com/$GH_USER/$REPO/settings/pages"
  warn "  Under 'Build and deployment' → Source → 'GitHub Actions'"
fi

# ---------------------------------------------------------------------------
# 7. Trigger first ETL run
# ---------------------------------------------------------------------------
bold "[7/8] Triggering first ETL run"

# The macro-etl workflow has workflow_dispatch enabled.
gh workflow run macro-etl --repo "$GH_USER/$REPO" 2>/dev/null \
  && ok "macro-etl run dispatched" \
  || warn "could not dispatch — first cron tick will run within an hour"

# ---------------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------------
bold "[8/8] All done"

URL_REPO="https://github.com/$GH_USER/$REPO"
URL_ACT="$URL_REPO/actions"
URL_PAGES="https://${GH_USER}.github.io/${REPO}/"

echo
ok "Repo:      $URL_REPO"
ok "Actions:   $URL_ACT     ← watch the deploy here"
ok "Live URL:  $URL_PAGES   ← available in 2–4 minutes"
echo
echo "Tail the workflow run live:"
echo "  gh run watch --repo $GH_USER/$REPO"
echo
echo "Force a manual refresh any time:"
echo "  gh workflow run macro-etl --repo $GH_USER/$REPO"
