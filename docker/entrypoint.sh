#!/bin/sh
# Container start-up: render Streamlit's secrets file from the environment, then run.
#
# The app keeps its Google OIDC gate in the container, and `st.login` reads its client
# configuration from `[auth]` in .streamlit/secrets.toml only — there is no environment
# equivalent. So the deployment supplies the values as app settings and this script writes
# the file at start-up, inside the container's own filesystem, mode 600, never in a layer.
#
# Everything the app itself reads (STEWARDS_*) is plain environment and needs no file.
set -eu
umask 077

PORT="${PORT:-8501}"
SECRETS_DIR=/app/.streamlit
SECRETS_FILE="$SECRETS_DIR/secrets.toml"
ALLOWED_DOMAIN="${STEWARDS_ALLOWED_DOMAIN:-theodi.org}"

# App Service publishes the site's hostname; the OAuth callback is derived from it so the
# redirect URI does not have to be restated as a setting. Override with AUTH_REDIRECT_URI
# behind a custom domain.
if [ -z "${AUTH_REDIRECT_URI:-}" ] && [ -n "${WEBSITE_HOSTNAME:-}" ]; then
    AUTH_REDIRECT_URI="https://${WEBSITE_HOSTNAME}/oauth2callback"
fi

if [ -n "${AUTH_CLIENT_ID:-}" ]; then
    : "${AUTH_CLIENT_SECRET:?AUTH_CLIENT_SECRET is required when AUTH_CLIENT_ID is set}"
    : "${AUTH_COOKIE_SECRET:?AUTH_COOKIE_SECRET is required when AUTH_CLIENT_ID is set}"
    : "${AUTH_REDIRECT_URI:?AUTH_REDIRECT_URI is required and could not be derived}"

    mkdir -p "$SECRETS_DIR"
    # Each value is interpolated into a TOML string, so a double quote in one would
    # corrupt the file. None of these can contain one: they are a URL, a hex secret and
    # the two credentials Google issues from its own alphabet.
    cat > "$SECRETS_FILE" <<EOF
[auth]
redirect_uri = "${AUTH_REDIRECT_URI}"
cookie_secret = "${AUTH_COOKIE_SECRET}"
client_id = "${AUTH_CLIENT_ID}"
client_secret = "${AUTH_CLIENT_SECRET}"
server_metadata_url = "${AUTH_SERVER_METADATA_URL:-https://accounts.google.com/.well-known/openid-configuration}"

[auth.client_kwargs]
prompt = "select_account"
hd = "${ALLOWED_DOMAIN}"
EOF
    chmod 600 "$SECRETS_FILE"
elif [ "${STEWARDS_ENV:-prod}" = "dev" ] && [ -n "${STEWARDS_DISABLE_AUTH:-}" ]; then
    echo "entrypoint: auth is disabled for this dev run; no secrets file written." >&2
else
    echo "entrypoint: AUTH_CLIENT_ID is not set — the sign-in button will fail." >&2
fi

exec streamlit run src/stewards/app.py \
    --server.port="$PORT" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
