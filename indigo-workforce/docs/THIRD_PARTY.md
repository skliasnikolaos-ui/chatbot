# Reused code and third-party licences

## Reused code

* **From SECURITY CENTER: none.** Its source was not available in the
  development environment (see ARCHITECTURE.md → Origin). No code, credentials
  or customer data were copied.
* **From the host repository (`chatbot`, Apache-2.0 Streamlit template): none.**
  The template is unrelated (OpenAI chat demo) and is left untouched at the
  repository root.
* All INDIGO WORKFORCE code in `indigo-workforce/` was written for this product.
  The product's own licence must be decided by the owner before distribution;
  the host repository's `LICENSE` (Apache-2.0) is the template's licence and
  should **not** be assumed to apply to a commercial product.

## Runtime dependencies (licence from installed package metadata)

| Package | Version | Licence | Notes |
|---|---|---|---|
| fastapi | 0.155.0 | MIT | |
| starlette | 1.9.0 | BSD-3-Clause | |
| uvicorn | 0.54.0 | BSD-3-Clause | |
| SQLAlchemy | 2.1.5 | MIT | |
| Jinja2 / MarkupSafe | 3.1.6 / 3.0.4 | BSD-3-Clause | |
| python-multipart | 0.0.32 | Apache-2.0 | |
| argon2-cffi (+ bindings) | 25.1.0 / 26.1.0 | MIT | |
| ortools (Google OR-Tools, CP-SAT) | 9.14.6206 | Apache-2.0 | pulls numpy (BSD-3 and others), pandas (BSD-3), protobuf (BSD-3), absl-py (Apache-2.0), immutabledict (MIT) |
| pydantic / pydantic-core | 2.14.1 / 2.50.1 | MIT | via FastAPI |
| anyio | 4.15.1 | MIT | |
| psycopg[binary] | 3.2.13 | **LGPL-3.0-only** | only for PostgreSQL deployments; used unmodified as a dynamically loaded library. LGPL obligations (licence notice, allow replacement of the library) must be met when distributing images. |

Development only: pytest (MIT), httpx (BSD-3-Clause), Playwright (Apache-2.0, used ad hoc for screenshots).

Apache-2.0 components require preserving their NOTICE/licence texts when
redistributing (e.g. in a Docker image). Run `pip-licenses` (or equivalent) in
CI before each release to refresh this table.
