---
title: Containers and standalone downloads
description: Run Cadre in a container (non-root, state in /data, keys from the environment) or as an unsigned standalone build with no Python.
---

# Containers and standalone downloads

> **Published with 1.0.0 and 1.0.1 (2026-09-19).** `ghcr.io/daemon-vi/cadre` has the tags `1.0.1`,
> `1.0.0`, `1.0` and `latest`, and pulls without logging in. The release workflow's smoke test ran it as user 10001
> and completed a demo run. The standalone builds for Windows, macOS (arm64) and Linux are on the
> [1.0.0 release](https://github.com/Daemon-VI/cadre/releases/tag/v1.0.0); the Windows one printed
> `cadre 1.0.0` and finished a demo run when downloaded and unpacked on a clean folder.

## Container

`ghcr.io/daemon-vi/cadre` runs `cadre serve` as a **non-root** user, keeps all state in **`/data`**
(`CADRE_HOME=/data`, on a volume), and reads keys **only from environment variables**
(`CADRE_NO_KEYRING=1`, because a container has no OS keychain). Inside the container the server
listens on `0.0.0.0`; publish the port on the host's **loopback only**.

With Compose, put the keys in a `.env` file next to
[`compose.yaml`](https://github.com/Daemon-VI/cadre/blob/main/compose.yaml) (never commit it):

```bash
# .env
GROQ_API_KEY=...
GEMINI_API_KEY=...
```

```bash
docker compose up -d
docker compose exec cadre cadre provider add groq     # picks the key up from the environment
docker compose exec cadre cadre ui --print            # dashboard link with the token in the fragment
```

The service in `compose.yaml`:

```yaml
services:
  cadre:
    image: ghcr.io/daemon-vi/cadre:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8765"   # loopback on the host
    volumes:
      - cadre-data:/data
      # project mode: mount a repository and run with --project /work/<name>
      # - ./my-repo:/work/my-repo
    environment:
      GROQ_API_KEY: ${GROQ_API_KEY:-}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
volumes:
  cadre-data:
```

Or with plain Docker:

```bash
docker run -d --name cadre -p 127.0.0.1:8765:8765 -v cadre-data:/data -e GROQ_API_KEY ghcr.io/daemon-vi/cadre:latest
docker exec cadre cadre provider add groq
docker exec cadre cadre ui --print
```

**Reaching it from another machine.** Use an SSH tunnel or Tailscale rather than opening the port.
Behind a Tailscale sidecar or a reverse proxy, name the host it will be reached as, exactly:
`serve --host 0.0.0.0 --allowed-host cadre.your-tailnet.ts.net`. Any other `Host` header is still
refused, and every API call still needs the token.

**Project mode.** Mount a repository (for example at `/work/my-repo`) and run with
`--project /work/my-repo`. The image includes `git`, because Cadre works in a git worktree on its
own branch.

## Standalone downloads

For machines without Python: one-folder builds for **Windows, macOS and Linux**, attached to each
GitHub Release. Unpack the archive and run `cadre` (`cadre.exe` on Windows) from the folder;
`cadre --version` checks that it works.

- **They are unsigned.** Code-signing certificates cost money, so Windows SmartScreen ("Windows
  protected your PC") and macOS Gatekeeper will warn the first time. Download only from the
  repository's Releases page. On Windows, *More info → Run anyway*. On macOS, allow it under
  *System Settings → Privacy & Security*.
- **Checks that use `{python}` need a Python on PATH.** The build carries no interpreter of its own
  for your project's code, so in a standalone build `{python}` means the first Python on PATH.
  Checks that call other programs (`npm test`, `cargo test`) need those programs too.
- Keys go to the OS credential store as usual, or come from environment variables.
