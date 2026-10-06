# Provenance: baseline results

These files come from one run of the offline rules baseline over the evaluation set.  No model was called.

| Item | Value |
|---|---|
| Command | `intake-agent eval` (the default arm is `baseline`) |
| Run at | 2026-10-06 10:08:00 UTC |
| Code revision | `8ccdabe`, "Add a keyword and rules baseline that runs offline", with a clean working tree |
| Data | `intake-eval-v1`, `src/intake_agent/data/eval/enquiries.jsonl`, 30 enquiries |
| Data SHA-256 | `45754e21b32faef1e147a3fe94369ef7b09666c1bcf5d88d07389609d2d972be` |
| Conflict register | `src/intake_agent/data/conflict_register.json`, version 2026-10-01, fictional |
| Intake policy | `src/intake_agent/data/policy.toml`, version 2026-10-01, fictional |
| Python | 3.11.17 |

| File | Contents |
|---|---|
| `report.md` | The summary table and every case with a wrong field |
| `results.json` | The provenance block, the summary, and the per-case expected and predicted fields |
| `records.jsonl` | The 30 triage records the baseline produced, one per line |

## How to read the score

The baseline scored 30 of 30 on every field.  That number says more about the evaluation set than about the rules.

- The same person wrote the 30 enquiries, their labels, and the baseline rules, and wrote the rules with the enquiries in view.  A perfect score shows that someone who knows the set can solve it with keywords and date rules.  It says nothing about how the same rules would do on enquiries they were not written alongside.
- This was the first and only scored run.  No rule was changed after it.
- The baseline is an optimistic reference, not a fair competitor.  A Claude run that matches it has matched rules fitted to the data by their author.  A Claude run that falls short may still be the better triage on real enquiries, which a set of 30 synthetic cases cannot show.

To reproduce the run, check out revision `8ccdabe`, install with `pip install -e .`, and run `intake-agent eval --out <dir>`.  The run date and the code revision in the new files will differ.  Every other field should match.
