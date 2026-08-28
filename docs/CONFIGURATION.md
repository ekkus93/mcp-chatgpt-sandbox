# Configuration

`docs/SPEC.md` is the only configuration reference this project maintains:
§52 gives the full example file, and each block is documented alongside
the feature it configures — §7 (repositories), §9 (images), §11 (caches),
§19 (SSH keys), §18.9 (Git policy), §26 (resources), §53 (secrets). The
Pydantic models in `src/dev_workspace_mcp/config/models.py` are the actual
enforced schema; `docs/SPEC.md` §52's example is kept consistent with them
by hand (`load_config`, `src/dev_workspace_mcp/config/loader.py`, fails
closed on anything the models reject).

This file exists so a reader who lands on it directly is pointed
somewhere real, not left with a 404 — it is not a second copy of the
reference kept in parallel with `docs/SPEC.md`.
