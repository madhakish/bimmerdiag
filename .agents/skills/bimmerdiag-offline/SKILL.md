---
name: bimmerdiag-offline
description: Test bimmerdiag sensor reads and diagnostic presentation with synthetic ECU fixtures, without loading a backend or connecting to a vehicle.
---

Read `AGENTS.md` and the affected CLI function. Check open issues/PRs before
editing baseline/config, platform loading or pressure formatting.

Use `tests/offline_ecu.py` to load `bin/diag.py` under a temporary `ediabas`
stub. Its constructor and `run_job` reject execution; `FixtureECU.read_value`
accepts only explicitly supplied `(sgbd, job, result_name)` keys. Missing keys
fail the test instead of connecting or silently becoming no data. Explicit
`None` is no data; an `EdiabasError` fixture models a backend failure.

Test the real function, not a reimplementation. Cover zero, missing data,
backend errors and unexpected exceptions; verify exact job/result arguments.
Preserve raw units and distinguish hPa/bar from atmospheres. Keep diesel/M62
contracts separate. Never import or replay recorded vehicle CSVs by default.

Run `python -m unittest discover -s tests -v`. Report the tested SHA and what
was not exercised: DLL/.NET loading, serial timing, ECU compatibility, resets,
and live safety. Do not turn a green offline suite into a hardware claim.
