---
title: Deployment
---

The dashboard runs as a container on Azure App Service. `.github/workflows/cd.yml` builds
the image, pushes it to the GitHub Container Registry (GHCR), and points the web app at the
new tag.

**Publishing a release is the deploy.** Merging to `main` only makes a commit eligible.
Draft releases do nothing; a prerelease is built and pushed but not deployed. Before
building, the workflow refuses any tag whose commit is not an ancestor of `main`, since
only `main` has passed CI.

| | |
|---|---|
| Resource group | `openactive`, shared with the APIs the dashboard reads |
| Web app | `openactive-admin-dashboard` |
| Image | `ghcr.io/openactive-contrib/openactive-admin-dashboard` |
| Deploy auth | the web app's publish profile |

## The image

`Dockerfile` installs the locked dependencies with `uv` into a virtualenv, then copies that
venv and `src/` into a `python:3.12-slim` runtime running as a non-root user. It listens on
`$PORT` (8501 by default) and answers `/_stcore/health`.

Each build is pushed as the release name (`0.1.2`), as `sha-<commit>`, and — for full
releases only — as `latest`. The deploy names the release tag.

Authentication is the same Google OIDC gate as local development. `st.login` reads its
client config from `[auth]` in `.streamlit/secrets.toml` and has no environment equivalent,
so `docker/entrypoint.sh` writes that file at start-up from the `AUTH_*` app settings, mode
600. No secret is baked into a layer.

## Run it locally

Settings go in a gitignored `.env.local`, using the same names as the app settings below.
Do not quote the values — Docker takes each line verbatim.

```bash
cat > .env.local <<'EOF'
# Inside a container `localhost` is the container itself.
STEWARDS_API_BASE_URL=http://host.docker.internal:5268
STEWARDS_API_STYLE=admin
STEWARDS_API_TOKEN_PARAM=token
STEWARDS_API_TOKEN=<api token>
AUTH_CLIENT_ID=<client id>.apps.googleusercontent.com
AUTH_CLIENT_SECRET=<client secret>
AUTH_COOKIE_SECRET=<openssl rand -hex 32>
AUTH_REDIRECT_URI=http://localhost:8501/oauth2callback
EOF

docker build -t stewards:local .
docker run --rm -p 8501:8501 --env-file .env.local stewards:local
```

`http://localhost:8501/oauth2callback` must be a registered redirect URI on the OAuth
client. To skip the gate, drop the `AUTH_*` lines and add `STEWARDS_ENV=dev` and
`STEWARDS_DISABLE_AUTH=true`.

## One-time setup

### 1. The web app

App Service → **Create** → Web App, into resource group `openactive`:

- Publish **Container**, Operating System **Linux**
- Name `openactive-admin-dashboard`
- Plan **B1** or above. Streamlit holds a websocket per session, so a plan that cold-starts
  drops sessions.
- Container tab: source **Other container registries**, image
  `ghcr.io/openactive-contrib/openactive-admin-dashboard:latest`

It must be a container app; one created to run code will not accept an image from
`azure/webapps-deploy`. Check the **Default domain** on the Overview blade — where App
Service appends a regional hash instead of issuing the bare
`openactive-admin-dashboard.azurewebsites.net`, the health poll and environment link in
`cd.yml` need that real hostname.

### 2. The GHCR pull token

The package is private and the org disables public packages, so App Service needs a
credential. GHCR requires a **classic** token; fine-grained tokens are not accepted for
container pulls.

GitHub → Settings → Developer settings → Tokens (classic) → **Generate new token
(classic)**, scope **`read:packages` only**. Put it on a machine account rather than a
person, and record its expiry: an expired token leaves a running app untouched, then fails
its next pull.

Verify it before pasting it into Azure:

```bash
echo '<token>' | docker login ghcr.io -u <username> --password-stdin
docker pull ghcr.io/openactive-contrib/openactive-admin-dashboard:latest
```

### 3. App settings

App Service → the web app → **Settings → Environment variables → App settings**. **Apply**
restarts the app and re-pulls the image.

| Name | Value |
|---|---|
| `WEBSITES_PORT` | `8501` |
| `WEBSITES_CONTAINER_START_TIME_LIMIT` | `300` |
| `WEBSITE_WEBDEPLOY_USE_SCM` | `true`, required before a publish profile will deploy |
| `DOCKER_REGISTRY_SERVER_URL` | `https://ghcr.io` |
| `DOCKER_REGISTRY_SERVER_USERNAME` | the GitHub account holding the token |
| `DOCKER_REGISTRY_SERVER_PASSWORD` | the `read:packages` token |
| `STEWARDS_API_BASE_URL` | the stewards API base URL |
| `STEWARDS_API_TOKEN` | the API token, not a user identity |
| `STEWARDS_API_STYLE` | `admin`; drop once the API speaks the versioned contract |
| `STEWARDS_API_TOKEN_PARAM` | `token`; drop with the line above |
| `STEWARDS_ENV` | `prod` |
| `STEWARDS_ALLOWED_DOMAIN` | `theodi.org` |
| `STEWARDS_CONTACT_THRESHOLD_DAYS` | `7` |
| `AUTH_CLIENT_ID` | `<client id>.apps.googleusercontent.com` |
| `AUTH_CLIENT_SECRET` | the OAuth client secret |
| `AUTH_COOKIE_SECRET` | `openssl rand -hex 32` |

`AUTH_REDIRECT_URI` is optional — the entrypoint derives it from the App Service hostname;
set it explicitly behind a custom domain. `AUTH_SERVER_METADATA_URL` defaults to Google's
discovery document.

Never set `STEWARDS_DISABLE_AUTH` on a deployment. It is honoured only when
`STEWARDS_ENV=dev`, and the app shows a standing warning while it is on.

Then under **Configuration → General settings**:

- **Web sockets** on
- **Session affinity** on, so a reconnect lands on the instance holding the session
- **SCM Basic Auth Publishing Credentials** on, or the publish profile cannot deploy

And under **Monitoring → App Service logs**, enable container logging so **Log stream**
shows tracebacks.

### 4. The Google OAuth client

Google Cloud console → APIs & Services → Credentials → the OAuth 2.0 Client ID (type *Web
application*). Add the deployed callback as an authorised redirect URI, keeping the
localhost one alongside it:

```
https://openactive-admin-dashboard.azurewebsites.net/oauth2callback
```

### 5. The publish profile secret

An app-level credential, so the deploy needs no Entra app and no Azure role assignment —
granting a role takes Owner or User Access Administrator, which this project does not have.

App Service → the web app → Overview → **Get publish profile**. The downloaded
`.PublishSettings` file should start with `<publishData>`.

In GitHub: Settings → Secrets and variables → Actions → **New repository secret** named
`AZURE_WEBAPP_PUBLISH_PROFILE`, with the **entire** XML document as its value. It is the
only secret the deploy needs; the GHCR push uses the run's own `GITHUB_TOKEN`.

It is long-lived, and rotated by hand from the same blade (**Reset publish profile**), after
which the GitHub secret must be re-pasted.

The workflow declares a `production` environment, which GitHub creates on first use. Visit
Settings → Environments → `production` to put deploys behind required reviewers.

### Google OAuth client

Google Cloud Console → APIs & Services → Credentials → the OAuth 2.0 Client ID (type *Web application*). Add the deployed callback as an authorised redirect URI, keeping the localhost one alongside it:

```
https://openactive-admin-dashboard-hdb3fpcvcmgygydn.ukwest-01.azurewebsites.net/oauth2callback
```

## Releasing

Releases → **Draft a new release** → choose a tag → **Publish release**. Or:

```bash
gh release create v0.1.3 --generate-notes
```

The deploy job polls `/_stcore/health` and fails the run if the app does not answer within
five minutes.

## Rollback

Actions → **CD** → **Run workflow**, with an earlier release tag. Same health check, same
record as an automatic deploy. Follow it with a revert on `main` and a new release, or the
next deploy restores the bad image.

The direct route, if the workflow itself is the problem:

```bash
az webapp config container set --name openactive-admin-dashboard \
  --resource-group openactive --container-image-name \
  ghcr.io/openactive-contrib/openactive-admin-dashboard:0.1.2
```

## Troubleshooting

App Service pulls the image on its own account, not the workflow's, so a green deploy can
still end in a stopped site. **Monitoring → Log stream** carries the real reason.

| Symptom | Cause |
|---|---|
| `ImagePullUnauthorizedFailure` | `DOCKER_REGISTRY_SERVER_*` missing or wrong, or the token expired |
| Container start times out | `WEBSITES_PORT` is not `8501` |
| Deploy step rejected | `WEBSITE_WEBDEPLOY_USE_SCM` unset, or SCM basic auth off |
| Page loads, "Please wait…" never resolves | websockets or session affinity off |
| Sign-in returns to the login card | the registered redirect URI does not match the site's hostname exactly, scheme included |
| Health poll times out after a green deploy | the container never started; read Log stream |

```bash
az webapp log tail --name openactive-admin-dashboard --resource-group openactive
az webapp restart --name openactive-admin-dashboard --resource-group openactive
```
