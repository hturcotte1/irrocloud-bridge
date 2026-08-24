#!/usr/bin/env bash
# Copy the settings in .env up to GitHub Actions secrets, so the scheduled
# morning run can use them. Prints ONLY the names of what it set — never the
# values. Needs the GitHub command-line tool `gh`, already signed in
# (`gh auth login`). If gh is not available, the README has the click-by-click
# path for adding each secret on the GitHub website instead.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "There is no .env file here yet. Copy .env.example to .env and fill it in first."
  exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "The GitHub command-line tool 'gh' is not installed (or not on PATH)."
  echo "Use the website instead: repository → Settings → Secrets and variables"
  echo "→ Actions → New repository secret. The README lists every name to add."
  exit 1
fi

# The three mode switches are pinned inside the workflow file on purpose and
# must NOT become secrets. SEND_TO_JACOB *is* pushed: it is the one switch
# Henry flips on the website without touching code.
SKIP="FETCHER HELIOS_MODE NOTIFY_MODE"

count=0
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|\#*) continue ;;
  esac
  case "$line" in
    *=*) ;;
    *) continue ;;
  esac
  key=${line%%=*}
  value=${line#*=}
  # Trim a trailing same-line comment (only after a space, so passwords
  # containing '#' survive) and surrounding quotes.
  value=$(printf '%s' "$value" | sed -e 's/ #.*$//' -e 's/^ *//' -e 's/ *$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")
  for skip_key in $SKIP; do
    if [ "$key" = "$skip_key" ]; then
      continue 2
    fi
  done
  if [ -z "$value" ] || [ "$value" = "REPLACE_ME" ]; then
    echo "skipping $key (still empty or REPLACE_ME)"
    continue
  fi
  printf '%s' "$value" | gh secret set "$key" --body-file -
  echo "set secret: $key"
  count=$((count + 1))
done < .env

echo "Done: $count secrets set. (Values were not printed on purpose.)"
