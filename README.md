# cheap-llm-review-fix

Companion code for a blog post (BLOG_POST_URL). It is example code to copy and adapt, not a product: read the scripts, change what does not fit your project, keep what does.

It lets cheap LLMs on OpenRouter (for example MiMo v2.6 Pro, GLM 5.3 Flash) do code review and small fixes, while a stronger "lead" (you, or a strong model such as Claude) only confirms findings and reads diffs.

1. Reviewers answer in a JSON schema, so findings are data, not prose.
2. `merge_reviews.py` groups findings across models and mechanically checks each quote and symbol against the real file.
3. The lead confirms; confirmed findings become a batch.
4. `model_fix.py` hands a model exactly the named code, gets exact find/replace edits, applies them only if they match once, runs your tests in a git worktree, retries once, and commits on success.

## Requirements

- Python 3.10+ (standard library only, no packages to install)
- git
- An OpenRouter API key, in `OPENROUTER_API_KEY` or (macOS) a keychain item with service `openrouter`. The key is never printed.

Requests set `data_collection: deny`, so providers that collect data are refused. The default model is `xiaomi/mimo-v2.6-pro`, with providers tried in order `deepinfra`, then `xiaomi`; change them with `--model` and `--provider`.

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

# 4. Fix in a separate worktree, tested with your own test command.
git worktree add -b review-fixes ../myproj-fixes
python3 $T/fix_batch.py reviews/batch.json --worktree ../myproj-fixes \
    --model xiaomi/mimo-v2.6-pro --provider deepinfra --strict-schema \
    --test-cmd "pytest {test_file}" --rules $T/examples/rules_python.md --out-dir reviews/fixes

# 5. Watch progress (in another terminal while it runs), then read every diff.
python3 $T/fix_status.py reviews/fixes
git -C ../myproj-fixes log -p
```

Test command templates: `{test_file}` is the finding's `test_file` path, `{tests}` is a filter (default: the test file's stem). Examples: `pytest {test_file}`, `npm test -- {tests}`, `python3 -m unittest`. The default is `./run_tests.sh {tests}`. A failing run is reverted (`git checkout`, `git clean`) in the worktree, so keep it clean.

To fix one finding or a free-form task: `python3 $T/model_fix.py --finding reviews/batch.json:1 ...`, or `--task task.md --context src/cart.py:Cart.add_item --test-file tests/test_cart.py ...`.

## Files

| File | What it does |
|---|---|
| `ask_model.py` | Asks a model on OpenRouter with files and images attached. `--json-schema` puts the schema in the prompt, parses and validates the answer (prose around it, an echoed schema and unescaped quotes in code strings are tolerated); `--strict-schema` also sends it as the provider's strict `response_format`. Streams, with a progress line in `<out>.progress` (reasoning is counted, not saved). |
| `review_schema.json` | Reviewer answer: title, severity, confidence, kind, file, symbol, verbatim evidence, situation, fix, test_file, related. |
| `merge_reviews.py` | Groups findings by file and symbol, checks each evidence quote exists in the file and that the symbol exists and contains the evidence, writes a Markdown table, and with `--batch-out` the confirmed batch. `--no-symbols` skips the symbol check. |
| `symbols.py` | The symbol finder: indentation languages (.py, .gd) and brace languages (.js .jsx .ts .tsx .go .rs .java .kt .swift .c .cc .cpp .h .hpp .cs .php). It is line-based and approximate. Other languages return "symbol not found": pass `--no-symbols` to `merge_reviews.py` (auto-confirm then needs only the evidence to be found). `model_fix.py` shows such files whole. |
| `model_fix.py` | One fix: exact code in, find/replace edits out (`patch_schema.json`, decision "fix" or "not a bug"), apply only if every find matches once, run tests, retry with the failure output (`--tries`), commit. `--rules FILE` appends project rules, `--test-cmd` sets the tests. |
| `patch_schema.json` | The fixer's answer format. |
| `fix_batch.py` | Runs a whole batch through `model_fix.py` (`--test-cmd`, `--rules`, `--tries`, `--only` pass through). |
| `fix_status.py` | One line per fix: what it is doing now, or how it ended, with attempts, time and cost. |
| `examples/` | A reviewer prompt, rules files for a Godot and a Python project, a sample review JSON. |
| `tests/` | `python3 -m unittest` (no network). |

## The confirmation rule

Only confirmed findings go to the fixer: two models reporting the same symbol with both mechanical checks passing, or findings the lead checked and passed with `--confirm`. A fixer told there is a bug tends to believe it. In a trial, GLM 5.3 Flash "fixed" a planted wrong claim and wrote a passing test for it; MiMo Pro declined it with decision "not a bug". The fix prompt asks the model to check the claim first, but do not rely on that; the lead's confirmation is the guard. The mechanical checks catch invented quotes and findings filed under the wrong function, not wrong reasoning.

## Tips

- Run MiMo with `--strict-schema` on DeepInfra. Z.ai rejects strict mode for GLM (404): leave it off there.
- MiMo reviews take 10 to 20 minutes even at low effort. Run them in the background and watch `<out>.progress`.
- Send whole subsystems, not halves: a reviewer that sees only half reports "missing" code that exists in the other half.
- Use two reviewers from different families. Agreement between them is the cheapest signal there is.
- Batches only, and read every diff. A commit's message and a passing test do not prove the change is right.
- Rules files (`--rules`) are where your project's conventions go: see `examples/rules_python.md` and `examples/rules_godot.md`.

## Cost

From the trials (prices on OpenRouter as of 2026-11-08, per million tokens: MiMo v2.6 Pro $0.435 in / $0.87 out; MiMo v2.6 Flash $0.14 / $0.28; check current prices):

| Run | Result | Time | Cost |
|---|---|---|---|
| Full review of three areas (two reviewers) | | 10 to 20 min per MiMo review | about $0.30 |
| Fix trial, six findings, MiMo v2.6 Pro | 5 of 5 real fixes, declined the planted wrong claim | 57 to 207 s per fix | about $0.034 total |
| Fix trial, six findings, GLM 5.3 Flash | 4 of 5 real fixes, fixed the planted wrong claim | 10 to 31 s per fix | about $0.012 total |

## License

MIT, see `LICENSE`. Copyright 2026 Dayna Blackwell.
