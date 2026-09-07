---
title: Deployment
---

The dashboard is deployed as a container. `.github/workflows/cd.yml` builds the image,
pushes it to the GitHub Container Registry (GHCR) and points an Azure App Service web app
at the new tag. Everything below the workflow — the web app, its registry credentials, its
app settings and the Google OAuth client — is created once, by hand.

The workflow runs when a **release is published**. Cutting a release is the deploy:

```bash
gh release create v1.2.3 --generate-notes
```

Merging to `main` therefore changes nothing that is running — it only makes a commit
eligible to be released. Draft releases do not fire the workflow; publishing one does. A
**prerelease** is built and pushed to GHCR but not deployed, so a release candidate can sit
in the registry until someone deploys it deliberately.

Before it builds, the workflow checks that the tag's commit is an ancestor of `main`.
Everything on `main` has passed CI, so that check is what keeps an untested commit — a tag
cut on a branch, say — off the deployment.

## What the image is

`Dockerfile` installs the locked dependencies with `uv` into a virtualenv, then copies
that venv and `src/` into a `python:3.12-slim` runtime that runs as a non-root user. It
listens on `$PORT` (8501 by default) and answers `/_stcore/health`.

Authentication is unchanged: the app still gates on Google OIDC through `st.login`.
`st.login` reads its client configuration from `[auth]` in `.streamlit/secrets.toml` and
has no environment equivalent, so `docker/entrypoint.sh` writes that file at start-up from
`AUTH_*` app settings, mode 600, inside the running container. No secret is ever baked
into a layer.

Run it locally exactly as the deployment does. The settings go in a gitignored
`.env.local` rather than a wall of `-e` flags: the names are the same ones the Azure app
settings use, they stay out of your shell history, and there are no line continuations to
break on a paste.

```bash
cat > .env.local <<'EOF'
# The API is on the host, and inside a container `localhost` is the container itself.
STEWARDS_API_BASE_URL=http://host.docker.internal:5268
# The interim admin API takes /admin/<...>?token=; drop both once it speaks the contract.
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

Add `localhost:8501/oauth2callback` to the OAuth client's authorised redirect URIs, or
sign-in will fail. To skip the gate entirely while working on a page, leave the `AUTH_*`
lines out and add `STEWARDS_ENV=dev` and `STEWARDS_DISABLE_AUTH=true`.

Do not quote values in an env file — Docker takes the line verbatim, so `KEY="value"`
passes the quotes through as part of the value.

## Manual set-up, once

Fill these in and keep them to hand; every command below uses them.

```bash
# The existing group that already holds the OpenActive APIs — the dashboard lives beside
# what it reads. A dedicated group would isolate nothing: same team, same lifecycle.
RG=openactive
LOCATION=uksouth
PLAN=openactive-admin-dashboard-plan
APP=openactive-admin-dashboard          # must match AZURE_WEBAPP_NAME in cd.yml
GH_ORG=openactive-contrib               # the GitHub org or user
GH_REPO=openactive-admin-dashboard
IMAGE=ghcr.io/$GH_ORG/$GH_REPO
```

### 1. Create the web app

The resource group already exists — it holds the APIs the dashboard reads — so this only
adds a plan and the app to it. `az group create` is idempotent if you are starting fresh.

```bash
az login

# B1 is the smallest tier that keeps the container warm. Streamlit holds a websocket per
# session, so a plan that cold-starts drops sessions.
az appservice plan create --name "$PLAN" --resource-group "$RG" \
  --is-linux --sku B1

az webapp create --name "$APP" --resource-group "$RG" --plan "$PLAN" \
  --deployment-container-image-name "$IMAGE:latest"
```

It must be a **container** web app: an App Service created to run code rather than a
container will not take an image from `azure/webapps-deploy`, and is easier to recreate
than to convert.

Check the app's **Default domain** on its Overview blade. App Service does not always issue
the bare `<name>.azurewebsites.net` — where it appends a regional hash, the health poll and
the environment link in `cd.yml` need that real hostname instead.

### 2. Let App Service pull from GHCR

GHCR packages are private by default. Create a **classic** personal access token with the
`read:packages` scope only (Settings → Developer settings → Tokens (classic)), ideally on a
machine account rather than a person, then:

```bash
az webapp config appsettings set --name "$APP" --resource-group "$RG" --settings \
  DOCKER_REGISTRY_SERVER_URL=https://ghcr.io \
  DOCKER_REGISTRY_SERVER_USERNAME=<github-username> \
  DOCKER_REGISTRY_SERVER_PASSWORD=<the-read:packages-token>
```

The alternative is making the package public (the package's page → Package settings →
Change visibility), which removes the need for the token entirely. The image contains no
secrets, but it does contain the source, so this is a judgement call for the team.

A package cannot be made public before it exists, and it does not exist until something is
pushed to it — so on a public-package deployment, publish a **prerelease** first:

```
Releases → Draft a new release → tag v0.1.0-rc1 → Set as a pre-release → Publish
```

CD builds and pushes it but skips the deploy, so the package is created without a
deployment being attempted. Change its visibility, then cut the real release. Creating it
that way also means Actions owns it: a package pushed by hand from a laptop is not linked
to the repository, and the workflow's `GITHUB_TOKEN` is then refused with `denied` until
the repo is added under the package's "Manage Actions access".

Getting the order wrong costs nothing. The deploy fails at the image pull, the app keeps
serving whatever it was serving, and re-running CD with the same tag picks it up.

### 3. Container and platform settings

```bash
az webapp config appsettings set --name "$APP" --resource-group "$RG" --settings \
  WEBSITES_PORT=8501 \
  WEBSITES_CONTAINER_START_TIME_LIMIT=300

# Streamlit is a websocket app: it needs websockets on, and session affinity so a
# reconnect lands on the instance holding the session.
az webapp config set --name "$APP" --resource-group "$RG" --web-sockets-enabled true
az webapp update --name "$APP" --resource-group "$RG" --client-affinity-enabled true

# Container stdout/stderr into the log stream, so `az webapp log tail` shows tracebacks.
az webapp log config --name "$APP" --resource-group "$RG" \
  --docker-container-logging filesystem
```

### 4. The Google OAuth client

In Google Cloud console → APIs & Services → Credentials, either reuse the existing client
or create an OAuth 2.0 Client ID of type *Web application*, and add the deployed callback
as an authorised redirect URI:

```
https://<APP>.azurewebsites.net/oauth2callback
```

Keep the localhost URI alongside it for local runs. Behind a custom domain, add that
origin's callback too and set `AUTH_REDIRECT_URI` explicitly (below); otherwise the
entrypoint derives it from the App Service hostname.

### 5. App settings the app reads

```bash
az webapp config appsettings set --name "$APP" --resource-group "$RG" --settings \
  STEWARDS_API_BASE_URL="https://stewards-api.internal.theodi.org" \
  STEWARDS_API_TOKEN="<api token>" \
  STEWARDS_API_STYLE="admin" \
  STEWARDS_API_TOKEN_PARAM="token" \
  STEWARDS_ENV="prod" \
  STEWARDS_ALLOWED_DOMAIN="theodi.org" \
  STEWARDS_CONTACT_THRESHOLD_DAYS="7" \
  AUTH_CLIENT_ID="<client id>.apps.googleusercontent.com" \
  AUTH_CLIENT_SECRET="<client secret>" \
  AUTH_COOKIE_SECRET="$(openssl rand -hex 32)"
```

`STEWARDS_API_STYLE` / `STEWARDS_API_TOKEN_PARAM` above are set for the interim admin API;
drop both once the deployment speaks the versioned contract. `AUTH_REDIRECT_URI` and
`AUTH_SERVER_METADATA_URL` are optional — the first is derived from the App Service
hostname, the second defaults to Google's discovery document.

Never set `STEWARDS_DISABLE_AUTH` on a deployment. It is honoured only when
`STEWARDS_ENV=dev`, and the app prints a standing warning when it is on.

### 6. The publish profile for the workflow

The deploy authenticates with a **publish profile** — an app-level credential Azure issues
for one web app. The alternative, an Entra app with a federated identity credential, is the
better credential in the abstract (short-lived tokens, nothing stored) but it needs an
Azure **role assignment**, and granting one takes Owner or User Access Administrator on the
resource. Contributor cannot, and the IAM blade greys out "Add role assignment" when you
lack it. A publish profile needs no RBAC at all.

The cost is honest: this is a long-lived secret in GitHub, rotated by hand. It is scoped to
this one web app, so it grants strictly less than Contributor on the resource — but it does
not expire on its own, and anyone with the file can deploy to the app. Rotate it from the
web app's Overview blade (**Reset publish profile**) if it is ever exposed, and re-paste the
new one into the GitHub secret.

**In Azure.** App Service → the web app:

1. **Settings → Environment variables → App settings** → add `WEBSITE_WEBDEPLOY_USE_SCM` =
   `true`, then **Apply**. Linux container apps need it before the profile will deploy.
2. **Overview** → **Get publish profile** in the top bar. A `.PublishSettings` file
   downloads; open it and check it starts with `<publishData>`.

If the download or the deploy is refused, check **Configuration → General settings → SCM
Basic Auth Publishing Credentials** is **On**. Some tenants disable it by policy, and with
it off this route is closed — the federated-credential route is then the only one, and
someone with Owner has to make the role assignment.

**In GitHub.** Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Value |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE` | the entire contents of the `.PublishSettings` file |

Paste the whole XML document, not a fragment of it. That is the only secret the deploy
needs — the GHCR push uses the run's own `GITHUB_TOKEN`.

The deploy job declares a `production` environment, which GitHub creates on first use. It
is worth visiting once anyway — Settings → Environments → `production` — because that is
where a deploy is put behind **required reviewers**, and where the deployment history for
the app is recorded.

This credential has no bearing on whether App Service can **pull** the image: the platform
does that itself, authorised by the package's visibility or the `DOCKER_REGISTRY_SERVER_*`
settings from step 2. A deploy can succeed and the container still fail to start on an
unauthorised pull, which surfaces as the health check timing out — `az webapp log tail`, or
**Monitoring → Log stream** in the portal, shows the real `unauthorized`.

### 7. First deploy

Publish a release. The deploy job polls `/_stcore/health` and fails the run if the app does
not answer within five minutes.

```bash
gh release create v0.1.0 --generate-notes
```

Without the `gh` CLI, the same thing from the web UI: Releases → Draft a new release →
choose a tag → Publish release.

## Operating it

```bash
az webapp log tail --name "$APP" --resource-group "$RG"    # live container logs
az webapp restart --name "$APP" --resource-group "$RG"
```

**Releasing.** Every deploy is a published release, and the release name is the image tag,
so what the Azure portal shows as the running image reads back as a release in GitHub. Each
build is also pushed as `sha-<commit>` — the immutable reference, if a release is ever
re-cut over a name that has already shipped.

**Rollback.** Re-run **CD** from the Actions tab with the earlier release tag as the input.
That goes through the same health check as an automatic deploy and leaves the same record:

```
Actions -> CD -> Run workflow -> tag: v1.2.2
```

The direct route, if the workflow itself is the problem:

```bash
az webapp config container set --name "$APP" --resource-group "$RG" \
  --container-image-name "$IMAGE:1.2.2"
```

Either way, follow it with a revert on `main` and a new release, or the next deploy puts
the bad image back.

**Common failures.**

- Container never starts, log shows a port timeout — `WEBSITES_PORT` is not 8501.
- `unauthorized` pulling the image — the `read:packages` token has expired or the package
  is private with no `DOCKER_REGISTRY_SERVER_*` settings.
- The page loads but "Please wait…" never resolves — websockets or session affinity are
  off (step 3).
- Sign-in loops back to the login card — the redirect URI registered with Google does not
  match the site's hostname exactly, scheme included.
