# Local setup on Windows

Zetaris runs through Docker Desktop's WSL 2 backend. A separate Ubuntu
installation is not required for the PowerShell commands below.

## Start Zetaris

Open Docker Desktop and wait for its engine to start. From PowerShell:

```powershell
Set-Location C:\Users\DELL\Documents\genAi\zetaris-platform
docker compose up -d
docker compose ps -a
```

The `postgres`, `driver`, `api`, and `ui` services should become healthy.
The `init` and `bootstrap` services should finish with exit code zero.
First startup includes sample-data registration and takes several minutes.

Open <http://localhost:3000>. The local administrator account is configured
in `zetaris-platform/.env`. Keep that file private.

## Configure Pipeless

The project's `.env` is separate from the platform's `.env`.
The application API URL is `http://localhost:8888/api/v1.0`.
The local organisation ID was verified as `1` on this installation.
API keys must belong to this local instance; a key from another instance
or a placeholder will not work.

Create the application API token in the Zetaris UI under Management > API
Tokens and save it privately as `ZETARIS_BUILDER_API_KEY` in the project's
`.env`. Use a separate non-administrator account/token for reader access.
Never copy real keys into `.env.example`, source code, or submission logs.

The official data recipes are checked out at
`C:\Users\DELL\Documents\getting-started`.

## Use the application environment

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m scripts.probe_zetaris
.\.venv\Scripts\python.exe run.py
```

Nightshift is a separate prerequisite. A successful Zetaris installation
does not make the agent pipeline ready until Nightshift also serves real
model completions.

## Stop without deleting data

From `zetaris-platform`, run `docker compose stop`.
Avoid `docker compose down -v`: it deletes the database and encryption keys.
