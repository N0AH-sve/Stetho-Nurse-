# Technology choices

Status values:

- **In place**: already configured in this repository.
- **Required**: set by the specification.
- **Approved**: decided, not set up yet. Explained in [decisions explained](#decisions-explained).

## Development environment

| Need | Choice | Status | Why |
| --- | --- | --- | --- |
| Language | Python 3.12 | In place | One language for both services, strong standard library |
| Dependencies | uv, with a committed lockfile | In place | Fast, reproducible installs; can ignore very recent releases |
| Lint and format | Ruff | In place | One tool for both, includes security and bug rules |
| Type checking | mypy, strict mode | In place | Catches type errors before runtime |
| Tests | pytest, pytest-cov | In place | De facto standard, with coverage |
| Pre-commit checks | pre-commit, gitleaks | In place | Secrets and errors are caught before a commit |
| Dependency audit | pip-audit | In place | Reports known vulnerabilities |
| Task runner | Make | In place | Already available, one entry point for all commands |

## Application

| Need | Choice | Status | Why |
| --- | --- | --- | --- |
| Packaging | Docker, with a Compose project separate from Hermes | Required | Hermes stays untouched; the whole stack can be removed safely |
| Base image | Slim Python image | Required | Small attack surface |
| Diagnosis | Fixed rules, no LLM | Required | Predictable, reviewable, no token cost |
| Hermes data access | Read-only mount; SQLite opened with `mode=ro` | Required | Monitoring must never modify Hermes data |
| API contract | OpenAPI 3.1, written by hand | Required | A single reference for the page, scripts and tests |
| Configuration | YAML files and environment variables | Required | Nothing specific to an installation lives in the code |
| Notifications | Discord or Telegram | Approved | Discord first, Telegram possible later; send only, no reading of messages |
| NURSE dependencies | Standard library only | Required | The privileged code stays short and reviewable |
| NURSE action catalogue | TOML (`actions.toml`) | Approved | The specified YAML cannot be read without a dependency |
| Docker access | Proxy filtering by method and path, such as `wollomatic/socket-proxy` | Required | NURSE never mounts the Docker socket |
| STETHO web server | Starlette and Uvicorn | Approved | Lightweight. FastAPI generates OpenAPI from code, while our contract is written first |
| Page rendering | Server-side Jinja2 templates with a little plain JavaScript | Approved | No front-end build step |
| STETHO YAML parser | PyYAML (safe loader only) | Approved | Mature and widely used |
| Incident state | A small JSON file, written atomically | Approved | The specification asks for a small file; no database needed |
| Notification client | `urllib` from the standard library | Approved | One simple request does not justify a dependency |

## Decisions explained

These choices are approved but not set up yet. STETHO is the service that watches Hermes and shows the page.
NURSE is the service that runs the repair actions.

### 1. Web server of STETHO

**What it is for.** STETHO must answer the browser: send the page and the state of Hermes, and
receive a click on an action button. Python needs a library to do this properly.

**Decision.** Two libraries that work together:

- **Uvicorn** is the server: it listens for requests.
- **Starlette** is a small framework: it decides which code answers each address.

**Why.** Both are small. We only need a page and a few addresses.

**Alternatives.**

- **FastAPI**, built on Starlette. Its main strength is generating the API description from the
  code. The specification asks for the opposite: the description is written by hand first.
- **Standard library only** (`http.server`). No dependency, but we would have to write routing,
  error handling and several protections ourselves.

### 2. How the page is built

**What it is for.** Producing what the user sees: the coloured banner, the cards and the buttons.

**Decision.** STETHO builds the HTML page itself from **Jinja2** templates, which are HTML files
with blanks to fill in. A little plain JavaScript refreshes the page every 30 seconds and shows
the confirmation window before an action.

**Why.** There is one language to maintain for almost everything, and no front-end build step.

**Alternative.** A separate web application, for example in React. It suits rich interfaces, but
adds Node.js, a build step and many more dependencies.

### 3. Three small needs of STETHO

| Need | Example | Decision | Why | Alternative |
| --- | --- | --- | --- | --- |
| Read the settings | Which modules are on, which thresholds apply | **PyYAML**, in safe mode so that a file cannot run code | The specification asks for YAML files, which Python cannot read on its own | Another YAML library |
| Remember incidents | After a restart, do not send the same alert again | A small **JSON file**. It is replaced in one step, so a crash cannot leave it half written | There is little to store | A SQLite database, useful if we later want a long history |
| Send alerts | Post "Hermes is down" to Discord or Telegram | **`urllib`**, included in Python. Both services accept a simple web request | Sending one simple request needs no extra library | A library such as `httpx`, more comfortable to use |

### 4. Format of the NURSE action catalogue

**What it is for.** A file lists the actions NURSE may run, with their settings, such as the
maximum duration.

**The problem.** The initial specification asked for two things that conflicted:

- the file is written in **YAML** (`actions.yaml`);
- NURSE uses **only what comes with Python**, to keep the privileged code easy to review.

Python cannot read YAML without an extra library.

**Decision.** TOML. The other configuration files stay in YAML.

| Option | Effect | Drawback |
| --- | --- | --- |
| **TOML** (`actions.toml`), chosen | Python reads it on its own (`tomllib`); comments are allowed | Differs from the initial specification; the other settings files stay in YAML |
| JSON | Python reads it on its own | No comments, less pleasant to edit by hand |
| Keep YAML and add PyYAML | Follows the initially specified format | NURSE gets a dependency, against the specification |

The same action in both formats:

```yaml
# actions.yaml
RERUN_JOB:
  label: "Rerun the job"
  timeout_seconds: 30
```

```toml
# actions.toml
[RERUN_JOB]
label = "Rerun the job"
timeout_seconds = 30
```

Once set up, a choice moves from **Approved** to **In place**.
