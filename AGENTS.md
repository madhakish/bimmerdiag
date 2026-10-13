# bimmerdiag agent guide

Python CLI and terminal dashboards for BMW diagnostics. Read `README.md`,
`docs/SETUP.md`, and the relevant ECU reference before changing behavior.
Check current issues, PRs and their comments before editing; baseline/config,
platform backend and pressure-display work may have separate owners.

## Boundaries

- Default development validation is offline, using synthetic fixtures. Never
  run `bin/diag.py`, dashboards or sweeps as a smoke test: even no arguments
  starts vehicle work. `--job` is arbitrary ECU execution, not a read-only flag.
- Fault clearing, injector/transmission resets, adaptations, coding and any
  live connection require a separately authorized, bounded hardware task.
  A test request alone does not authorize them. Never bypass confirmation.
- Do not load EDIABAS DLLs, Mono or pythonnet for offline tests. Stub the
  `ediabas` import before loading a CLI module; make construction/job execution
  fail closed. Do not silently fall back to real hardware on a missing fixture.
- Use synthetic values, not committed recording CSVs, VINs or real baseline
  observations, for fixtures and public failure artifacts.
- Preserve raw measurements and units. Distinguish missing data from zero,
  absolute from relative pressure, and hPa/bar from standard atmospheres.
  Thresholds and vehicle-specific baselines are not generic engine truth.
- Keep diesel and M62 paths distinct unless evidence establishes a shared
  contract. Do not advertise pending cross-platform work as shipped support.

## Checks and handoff

Run `python -m unittest discover -s tests -v` from the root. The helper in
`tests/offline_ecu.py` supports bounded read fixtures and rejects unknown reads
or jobs. These tests establish host-side behavior only, never ECU compatibility
or mechanical safety. See [.agents/skills/bimmerdiag-offline/SKILL.md](.agents/skills/bimmerdiag-offline/SKILL.md).

Report source SHA, exact commands/results, synthetic coverage, and hardware
checks not run. Keep production changes, baseline changes and documentation
work separately reviewable. Never add CI that exposes a connected car, serial
ports, host signing keys, or privileged credentials to candidate code.
