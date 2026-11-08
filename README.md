# cheap-llm-review-fix

Companion code for a blog post (BLOG_POST_URL). It is example code to copy and adapt, not a product: read the scripts, change what does not fit your project, keep what does.

It lets cheap LLMs on OpenRouter (reviewers such as MiMo v2.6 Pro and GLM 5.3 Flash, a fixer such as Claude Haiku 5.5) do code review and small fixes, while a stronger "lead" (you, or a strong model such as Claude) only confirms findings and reads diffs.

1. Reviewers answer in a JSON schema, so findings are data, not prose.
2. `merge_reviews.py` groups findings across models and mechanically checks each quote and symbol against the real file.
3. The lead confirms; confirmed findings become a batch.
4. `check_findings.py` checks each finding is a good task: a model sees exactly what the fixer will see and says whether the bug is visible, the test file fits and nothing needed is missing. Repair or drop the ones that are not ready.
5. `model_fix.py` hands a model exactly the named code (plus an outline of the files it only saw part of), gets exact find/replace edits, applies them only if they match once, runs your tests in a git worktree, retries once, and commits on success.
6. `fix_bench.py` scores fixer models on a set of confirmed bugs and wrong claims, so the choice of fixer rests on a measurement (see `docs/building-a-bench.md`).

## Requirements

- Python 3.10+ (standard library only, no packages to install)
- git
- An OpenRouter API key, in `OPENROUTER_API_KEY` or (macOS) a keychain item with service `openrouter`. The key is never printed.

Requests set `data_collection: deny`, so providers that collect data are refused. `ask_model.py` (the reviewers) defaults to `xiaomi/mimo-v2.6-pro`, with providers tried in order `deepinfra`, then `xiaomi`; change them with `--model` and `--provider`.

The default fixer (`model_fix.py`, `fix_batch.py`, `fix_bench.py`) is Claude Haiku 5.5, `anthropic/claude-haiku-5.5`. With no `--provider` it uses `google-vertex`, then `anthropic`, with the provider-enforced (strict) patch schema, because Bedrock does not support strict mode. MiMo and GLM still work through flags: `--model xiaomi/mimo-v2.6-pro --provider deepinfra --strict-schema` or `--model z-ai/glm-5.3-flash --provider z-ai`.

## Quick start

Clone the repo next to your project (or anywhere) and run the scripts by path. The examples assume you are in your project's root and the scripts are in `../cheap-llm-review-fix`; set `T=../cheap-llm-review-fix`.

```sh
git clone https://github.com/blackwell-systems/cheap-llm-review-fix ../cheap-llm-review-fix
T=../cheap-llm-review-fix
export OPENROUTER_API_KEY=sk-or-...
mkdir -p reviews

# 1. Two reviewers from different families, same prompt and files.
python3 $T/ask_model.py --prompt-file $T/examples/review_prompt.md \
    --file src/cart.py --file src/pricing.py --file tests/test_cart.py \
    --json-schema $T/review_schema.json --strict-schema --effort low \
    --model xiaomi/mimo-v2.6-pro --provider deepinfra --out reviews/mimo.json
python3 $T/ask_model.py --prompt-file $T/examples/review_prompt.md \
    --file src/cart.py --file src/pricing.py --file tests/test_cart.py \
    --json-schema $T/review_schema.json --effort low \
    --model z-ai/glm-5.3-flash --provider z-ai --out reviews/glm.json

# 2. Merge: a table, and a batch of the findings two models agree on.
python3 $T/merge_reviews.py reviews/mimo.json reviews/glm.json --root . \
    --out reviews/merged.md --batch-out reviews/batch.json

# 3. Read reviews/merged.md, check the claims yourself, then confirm the ones
#    only one model reported (group number like 3, or one finding like 3b).
python3 $T/merge_reviews.py reviews/mimo.json reviews/glm.json --root . \
    --out reviews/merged.md --batch-out reviews/batch.json --confirm 2,3b

# 4. Check each finding is a good fix task: a model sees exactly what the fixer will
#    see. Fix or drop what it says is not ready (wrong test file, missing context).
#    --project-notes says what your test folders mean; --outline files are shown to
#    the fixer too (test helpers), so pass the same ones to both steps.
python3 $T/check_findings.py reviews/batch.json --root . \
    --project-notes $T/examples/project_notes_python.md --outline tests/helpers.py \
    --out reviews/readiness.md

# 5. Fix in a separate worktree, tested with your own test command. The default
#    fixer is Claude Haiku 5.5. After the named code each fixer gets an outline of
#    the files it saw part of, plus the --outline files.
git worktree add -b review-fixes ../myproj-fixes
python3 $T/fix_batch.py reviews/batch.json --worktree ../myproj-fixes \
    --test-cmd "pytest {test_file}" --rules $T/examples/rules_python.md \
    --outline tests/helpers.py --out-dir reviews/fixes

# 6. Watch progress (in another terminal while it runs), then read every diff.
python3 $T/fix_status.py reviews/fixes
git -C ../myproj-fixes log -p
```

Test command templates: `{test_file}` is the finding's `test_file` path, `{tests}` is a filter (default: the test file's stem). Examples: `pytest {test_file}`, `npm test -- {tests}`, `python3 -m unittest`. The default is `./run_tests.sh {tests}`. A failing run is reverted (`git checkout`, `git clean`) in the worktree, so keep it clean.

To fix one finding or a free-form task: `python3 $T/model_fix.py --finding reviews/batch.json:1 ...`, or `--task task.md --context src/cart.py:Cart.add_item --test-file tests/test_cart.py ...`. If a file a fixer needs does not exist, `model_fix.py` writes a result with outcome `error` and the reason (so `fix_status.py` and the benchmark show it) and exits 1.

**Outlines.** A fixer shown one function does not know the constants, fields and helpers around it. After the named code, `model_fix.py` adds an outline of every file it showed only part of: declarations only, with line numbers, at the top level and one level into a class, marked "not for `find`", capped at about 200 lines. Python and GDScript are read by indentation (classes, `def`/`func`, constants, variables, enums, signals); the brace languages (JS/TS, Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP) by brace depth with one regex family per language group, so it is approximate. `--outline FILE` (repeatable; also on `fix_batch.py`, `check_findings.py` and `fix_bench.py`) adds a file, for example your test helpers, so a fixer writes tests against the real API.

**Benchmark a fixer.** `python3 $T/fix_bench.py --bench bench --repo . --label haiku-5.5 --runs 3 --test-cmd "pytest {test_file}"` runs a findings file and a key file in fresh worktrees and appends to `bench/scoreboard.md`; `--rescore` rebuilds it from saved results. See `docs/building-a-bench.md` for building a bench worth trusting.

## Files

| File | What it does |
|---|---|
| `ask_model.py` | Asks a model on OpenRouter with files and images attached. `--json-schema` puts the schema in the prompt, parses and validates the answer (prose around it, an echoed schema and unescaped quotes in code strings are tolerated); `--strict-schema` also sends it as the provider's strict `response_format`. Streams, with a progress line in `<out>.progress` (reasoning is counted, not saved). |
| `review_schema.json` | Reviewer answer: title, severity, confidence, kind, file, symbol, verbatim evidence, situation, fix, test_file, related. |
| `merge_reviews.py` | Groups findings by file and symbol, checks each evidence quote exists in the file and that the symbol exists and contains the evidence, writes a Markdown table, and with `--batch-out` the confirmed batch. `--no-symbols` skips the symbol check. |
| `symbols.py` | The symbol finder and the outline builder: indentation languages (.py, .gd) and brace languages (.js .jsx .ts .tsx .go .rs .java .kt .swift .c .cc .cpp .h .hpp .cs .php). It is line-based and approximate. Other languages return "symbol not found": pass `--no-symbols` to `merge_reviews.py` (auto-confirm then needs only the evidence to be found). `model_fix.py` shows such files whole. `outline()` lists a file's declarations for Python, GDScript and the same brace languages. |
| `check_findings.py` | Readiness check: for each finding, a model sees the fixer's exact context (built by `model_fix.py`) and the test file list, and answers in `readiness_schema.json` whether it is a good task. `--project-notes FILE` appends project specifics to the prompt. |
| `readiness_schema.json` | The readiness answer: ready, bug visible, missing context, test file ok, better test file, fix sound, problems. |
| `model_fix.py` | One fix: exact code in, find/replace edits out (`patch_schema.json`, decision "fix" or "not a bug"), apply only if every find matches once, run tests, retry with the failure output (`--tries`), commit. `--rules FILE` appends project rules, `--test-cmd` sets the tests, `--outline FILE` adds an outline. Default model Claude Haiku 5.5. |
| `patch_schema.json` | The fixer's answer format. |
| `fix_batch.py` | Runs a whole batch through `model_fix.py` (`--test-cmd`, `--rules`, `--tries`, `--outline`, `--only` pass through). |
| `fix_bench.py` | Scores a fixer on a findings file plus a key file (real, fake, debatable), in fresh worktrees; saves diffs, appends to `scoreboard.md`; `--rescore`. |
| `fix_status.py` | One line per fix: what it is doing now, or how it ended, with attempts, time and cost. |
| `docs/building-a-bench.md` | How to build a fix benchmark: key format, real bugs, fakes, the readiness check, three runs. |
| `examples/` | A reviewer prompt, rules files and project notes (for the readiness check) for a Godot and a Python project, a sample review JSON. |
| `tests/` | `python3 -m unittest` (no network). |

## The confirmation rule

Only confirmed findings go to the fixer: two models reporting the same symbol with both mechanical checks passing, or findings the lead checked and passed with `--confirm`. A fixer told there is a bug tends to believe it. In a trial, GLM 5.3 Flash "fixed" a planted wrong claim and wrote a passing test for it; MiMo Pro declined it with decision "not a bug". In the benchmark below, Claude Haiku 5.5 refused 5 to 7 of 7 wrong claims across three runs, but it "fixed" one in two of them: a refusal rate is not a guarantee. The fix prompt asks the model to check the claim first, but do not rely on that; the lead's confirmation is the guard. The mechanical checks catch invented quotes and findings filed under the wrong function, not wrong reasoning.

## Tips

- Run MiMo with `--strict-schema` on DeepInfra. Z.ai rejects strict mode for GLM (404): leave it off there.
- MiMo reviews take 10 to 20 minutes even at low effort. Run them in the background and watch `<out>.progress`.
- Send whole subsystems, not halves: a reviewer that sees only half reports "missing" code that exists in the other half.
- Use two reviewers from different families. Agreement between them is the cheapest signal there is.
- Batches only, and read every diff. A commit's message and a passing test do not prove the change is right.
- Rules files (`--rules`) are where your project's conventions go: see `examples/rules_python.md` and `examples/rules_godot.md`. Project notes (`--project-notes`, readiness check only) say what your test folders mean: see `examples/project_notes_python.md` and `examples/project_notes_godot.md`.
- Run the readiness check before a batch or a benchmark. A finding whose test file is wrong or whose code is not shown fails for reasons that have nothing to do with the fixer. `merge_reviews.py` also flags a `test_file` that does not exist ("test file X not found" in the symbol column) and keeps it out of the auto-confirmed batch.

## Results

Prices on OpenRouter as of 2026-11-08, per million tokens: MiMo v2.6 Pro $0.435 in / $0.87 out; MiMo v2.6 Flash $0.14 / $0.28; check current prices. One full review of three areas by two reviewers cost about $0.30 (MiMo reviews take 10 to 20 minutes each).

**Fix benchmark, Claude Haiku 5.5 (the default fixer).** 20 findings from a private Godot game (GDScript, a niche language): 9 definite bugs (5 easy, 2 medium, 2 hard), 3 latent (hardening), 7 wrong claims real reviewers made, 1 debatable. Three runs, each in a fresh worktree, 2026-11-08:

| Run | Definite bugs fixed | Wrong claims refused | Wrong claims "fixed" | Latent fixed | Time | Cost |
|---|---|---|---|---|---|---|
| 1 | 6 of 9 (easy 3/5, medium 1/2, hard 2/2) | 7 of 7 | 0 | 0 of 3 | 339 s | $0.057 |
| 2 | 6 of 9 (easy 3/5, medium 1/2, hard 2/2) | 5 of 7 | 1 | 3 of 3 | 391 s | $0.062 |
| 3 | 7 of 9 (easy 5/5, medium 1/2, hard 1/2) | 6 of 7 | 1 | 1 of 3 | 365 s | $0.059 |

So 6 to 7 of 9 definite bugs including both hard ones (each hard bug was fixed in at least two of the three runs), 5 to 7 of 7 wrong claims refused, and about 6 cents and 6 minutes per 20-finding run.

**Trial table, five models.** These are not one experiment: the first two rows are the earlier six-finding trial (five real bugs and one planted wrong claim, before outlines existed); the last three are the 20-finding benchmark, where MiMo Flash and Qwen 3.8 Flash had one run each and did not finish (some findings have no result, shown as "not run"). Compare within a row group, not across.

| Model | Test | Definite bugs fixed | Wrong claims | Time | Cost |
|---|---|---|---|---|---|
| MiMo v2.6 Pro (DeepInfra) | six findings | 5 of 5 | declined the planted one | 57 to 207 s per fix | about $0.034 total |
| GLM 5.3 Flash (Z.ai) | six findings | 4 of 5 | "fixed" the planted one | 10 to 31 s per fix | about $0.012 total |
| Claude Haiku 5.5 | 20 findings, 3 runs | 6 to 7 of 9, both hard ones | refused 5 to 7 of 7 | about 6 min per run | about $0.06 per run |
| MiMo v2.6 Flash | 20 findings, 1 run | 2 of 9 (10 findings not run) | refused 3 of 7, "fixed" 2 | 63 min | $0.088 |
| Qwen 3.8 Flash | 20 findings, 1 run | 4 of 9 (7 findings not run) | refused 2 of 7, "fixed" 4 | 56 min | $0.171 |

Limits: one codebase and language; the benchmark findings were formatted by GLM 5.3 Flash from the author's descriptions of real bugs and wrong claims, not produced by a live review; passing tests do not prove a fix is right, so the diffs were read. Treat these as one data point and build a bench on your own code.

## License

MIT, see `LICENSE`. Copyright 2026 Dayna Blackwell.
