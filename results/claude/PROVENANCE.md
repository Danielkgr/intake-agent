# Provenance: Claude arm results

These files come from one run of the Claude arm over the evaluation set, against the live Claude API.

| Item | Value |
|---|---|
| Command | `intake-agent eval --arm claude` |
| Run at | 2026-10-06, started about 12:03 UTC, results written 12:09:31 UTC |
| Code revision | `456bff7`, "Merge pull request #1 from Danielkgr/initial-build", with a clean working tree |
| Data | `intake-eval-v1`, `src/intake_agent/data/eval/enquiries.jsonl`, 30 enquiries |
| Data SHA-256 | `45754e21b32faef1e147a3fe94369ef7b09666c1bcf5d88d07389609d2d972be` |
| Conflict register | `src/intake_agent/data/conflict_register.json`, version 2026-10-01, fictional |
| Intake policy | `src/intake_agent/data/policy.toml`, version 2026-10-01, fictional |
| Model | `claude-opus-5-5` at `medium` effort, `max_tokens` of 16,000 per request, at most 8 turns per enquiry |
| Fallbacks | Off.  Every turn of every enquiry was answered by `claude-opus-5-5`. |
| SDK and Python | `anthropic` 1.11.0, Python 3.14.4 |

The data, the register, and the policy are byte for byte the files the baseline was scored on.  None of them changed between the baseline's revision `8ccdabe` and `456bff7`.

| File | Contents |
|---|---|
| `report.md` | The summary table and every case with a wrong field |
| `results.json` | The provenance block, the summary, the measured usage, and the per-case expected and predicted fields |
| `records.jsonl` | The 30 triage records, one per line, each with its audit trail of model turns, tool calls, and tool results, and its own usage and cost |

The console output of the run sits in `eval-logs/` at the top of the repository.

## Usage and cost

Every figure below is summed from the `usage` block the API returned for each request.

| Measure | Value |
|---|---|
| Requests | 64, of which 26 enquiries took 2 and 4 took 3 |
| Uncached input tokens | 188 |
| Cache write tokens | 37,077 |
| Cache read tokens | 316,697 |
| Output tokens | 32,666, about 1,090 per enquiry |
| Cost, all 30 enquiries | $0.9028 |
| Cost per enquiry | $0.0301 |

The cost is computed from those token counts at the list prices for Claude Opus 5.5 in `src/intake_agent/pricing.py`.  The invoice is the authority.  At the same prices the same tokens with no caching would have cost about $2.07, so caching cut the cost of this run by about 56%.  The estimate before the run was $2 to $7, because it assumed three or four requests per enquiry and longer outputs than the model wrote.

## How to read the score

The Claude arm scored 30 of 30 on every field, the same as the rules baseline.

- The set does not separate the two arms.  The same person wrote the 30 enquiries and their labels, and a keyword baseline written with the enquiries in view also scores 30 of 30.  A perfect score here shows that Claude handles an easy, synthetic set.  It says nothing about real enquiries.
- In both arms the conflict status comes from the conflict tool and the code re-check, and a potential conflict sets the route in code.  The conflict rows measure that code at least as much as the model.  The practice area and the urgency are the model's own, and the routing rules in code constrain every route.
- No case failed, no holding reply tripped the advice check, and no summary statement failed the grounding check, so no record went to a person because a check failed.  The six conflict cases had their replies replaced by the conflict template, as the code requires.
- This was the first and only scored run of the Claude arm.  Nothing in the prompts, the tools, or the data was changed after it.

To reproduce the run, check out revision `456bff7`, install with `pip install -e .`, set `ANTHROPIC_API_KEY`, and run `intake-agent eval --arm claude --out <dir>`.  The model's wording, the token counts, and the cost will differ from run to run, and so may the scores.
