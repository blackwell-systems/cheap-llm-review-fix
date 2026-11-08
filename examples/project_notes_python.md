Project: a Python 3.10 web service, standard library plus Flask. Tests run with `pytest`.

- `tests/unit/` has plain functions and classes with no app, database or network: the right place
  for pure logic (pricing, parsing, validation). A test there builds its inputs inline.
- `tests/integration/` uses the `client` fixture (a Flask test client over an in-memory SQLite
  database, defined in `tests/conftest.py`). Use it when the code needs a request, a session or
  the database.
- `tests/e2e/` starts the whole app and is slow; a finding should not need it.
- Tests are `test_` functions with plain `assert`. Shared helpers are in `tests/helpers.py`.
- A finding whose test_file is under `tests/e2e/` is probably misfiled: suggest the unit or
  integration file instead.
