#!/usr/bin/env bash
set -euo pipefail

# ---- config ----
BASE_DIR="$HOME/code/nitter-bot/check_me_in"
VENV_DIR="$BASE_DIR/venv"
ENV_FILE="$BASE_DIR/.env"
SESSIONS_OUT="$BASE_DIR/sessions.jsonl"
PY_SCRIPT="$BASE_DIR/session.py"
REQS="$BASE_DIR/requirements.txt"

# ---- helpers ----
die() { echo "ERROR: $*" >&2; exit 1; }

# Minimal .env loader that supports KEY=VALUE lines (no export needed)
load_env_file() {
  local file="$1"
  [[ -f "$file" ]] || die "Missing env file: $file"

  # shellcheck disable=SC2163
  while IFS='=' read -r key value; do
    # skip blanks/comments
    [[ -z "${key// /}" ]] && continue
    [[ "$key" =~ ^[[:space:]]*# ]] && continue

    # trim spaces
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"

    # strip surrounding quotes (simple)
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%$'\r'}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"

    export "$key=$value"
  done < "$file"
}

# ---- main ----
cd "$BASE_DIR" || die "Failed to cd to $BASE_DIR"

# venv bootstrap
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

# activate venv
source "$VENV_DIR/bin/activate"

# if first time install
# python -m pip install -U pip >/dev/null
# python -m pip install -r "$REQS"

load_env_file "$ENV_FILE"

# Discover account indices from USER<N>=... keys
# Example env file:
#   USER1=alice
#   PASS1=...
#   TOTP1=...
#   USER2=bob
#   PASS2=...
#   # TOTP2 optional
mapfile -t IDX < <(env | sed -n 's/^USER\([0-9]\+\)=.*/\1/p' | sort -n)

[[ "${#IDX[@]}" -gt 0 ]] || die "No USER<N> entries found in $ENV_FILE"

echo "Writing sessions to: $SESSIONS_OUT"
echo "Accounts found: ${#IDX[@]}"

for i in "${IDX[@]}"; do
  user_var="USER${i}"
  pass_var="PASS${i}"
  totp_var="TOTP${i}"

  username="${!user_var:-}"
  password="${!pass_var:-}"
  totp="${!totp_var:-}"

  [[ -n "$username" ]] || die "Missing $user_var in $ENV_FILE"
  [[ -n "$password" ]] || die "Missing $pass_var in $ENV_FILE"

  echo "----"
  echo "Running account #$i: $username"

  # Run your script. TOTP is optional.
  if [[ -n "$totp" ]]; then
    python3 "$PY_SCRIPT" "$username" "$password" "$totp" --append "$SESSIONS_OUT"
  else
    python3 "$PY_SCRIPT" "$username" "$password" --append "$SESSIONS_OUT"
  fi
done

echo "transferring sessions to server..."
scp sessions.jsonl codabool@192.168.0.25:/mnt/volumes/sessions.jsonl


echo "creating new .rsshub.env file and copying over"
AUTH_TOKEN="$(jq -r '.auth_token' sessions.jsonl | grep -v null | head -n 1)"
# Replace auth token
sed -i "s|^TWITTER_AUTH_TOKEN=.*$|TWITTER_AUTH_TOKEN=${AUTH_TOKEN}|" .rsshub.env
scp .rsshub.env codabool@192.168.0.25:/mnt/volumes/.rsshub.env


echo "Done."
