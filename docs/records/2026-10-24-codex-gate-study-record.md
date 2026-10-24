# Codex gate study record, 2026-10-22 to 2026-10-24

How the registered Codex study ran. The results are in `lab/CODEX_REPORT.md` and
`docs/findings/2026-10-24-codex-gate-study-findings.md`; `tests/test_codex_study_integrity.py` checks every number
here against the report and the pre-registration.

## Timeline

- 2026-10-22: the pre-registration was committed (52abf20) after the 14-run calibration.
- 2026-10-23: the study started with `python3 -m patton_lab.codex.runner study`, which checked the
  pre-registration, the trace marker, the pinned Codex binaries and the login before the first run.
- **Stop 1, upstream 502.** The attempt `X-R1S-f06-r1.a0` met 502 replies and did not finish. The host had lost DNS:
  the recorder could not resolve the ChatGPT backend, and it answered 25 calls with 502 that never reached it (3
  calls of the attempt had succeeded). The registered rule stops the study on an unfinished attempt that met an
  upstream 502 and redoes the run on resume. The failed attempt is kept in `lab/codex_study/attempts/`, and the run
  was redone and finished.
- **Stop 2, the 5-hour window.** With 11 runs left, the 5-hour reading reached 90 %, the registered stop, and the
  study held until the window's recorded reset. The weekly reading was 14 % at that point.
- **Stop 3, the login.** On 2026-10-24 the resume refused before any run started: the ChatGPT access token had
  expired. The harness reads the host login and never refreshes it, so the person signed in again with
  `codex login`, and the next resume ran the remaining 11 runs.
- 2026-10-24: `study complete: every registered run is stored`.

None of the stops changed a rule, an arm or the run order: each is a branch the pre-registration names, and the same
command resumed the study each time.

## Quota

The weekly reading rose from 0 % to 15 % over the 240 runs, under the registered stop of 60 %. The 5-hour reading
reached its stop of 90 % once.

## Checks after the last run

```
cd lab
python3 -m patton_lab.codex.prereg --check                 # the pre-registration still matches the frozen inputs
python3 -m patton_lab.codex.runner replay --out codex_study  # replay OK: 240 run(s) reproduce
python3 -m patton_lab.codex.report --check                 # CODEX_REPORT.md matches the records
```

An independent recompute from the raw records, written without the lab's analysis code, reproduced the report's
tables and verdicts.
