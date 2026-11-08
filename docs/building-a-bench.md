# Building a fix benchmark

`fix_bench.py` scores a model as a fixer on a fixed set of findings, so you can compare models, prompts and rules on your own code instead of trusting someone else's leaderboard. The score is only as good as the set. This is how to build one that is worth trusting.

## What goes in

A bench is a folder:

```
bench/
  findings.json   review-format findings, shuffled (what fixers see)
  key.json        what each one really is (never shown to a fixer)
  readiness.md    the readiness check of the set (keep it for reference)
```

`fix_bench.py` adds `results/<label>/run<N>/` (prompts, answers, result records, each fix's diff, `score.json`) and `scoreboard.md`.

`key.json`:

```json
{
  "base_commit": "3f2a9c1...",
  "items": [
    {"n": 1, "kind": "real", "difficulty": "easy", "latent": false, "title": "Empty cart total divides by zero"},
    {"n": 2, "kind": "fake", "difficulty": "fake", "title": "Discount applied twice"},
    {"n": 3, "kind": "real", "difficulty": "hard", "latent": false, "title": "Refund half-commits on failure"},
    {"n": 4, "kind": "debatable", "difficulty": "medium", "title": "Rounding mode"}
  ]
}
```

`n` is the finding's number (from 1) in `findings.json`. `kind` is `real`, `fake` or `debatable`; `difficulty` is any label you like (`easy`, `medium`, `hard`; scores are grouped by it); `latent` marks hardening against misuse. `base_commit` is the commit the worktrees start from.

## Scoring

- **Definite bugs fixed:** a `real`, non-latent finding whose fix passes its tests. Grouped by difficulty.
- **Latent fixed:** a `real` finding with `"latent": true`, reported apart. Code that cannot misbehave in practice but could under misuse is a fair thing to decline, so declining is not held against the model.
- **Fakes refused:** a `fake` finding answered "not a bug".
- **Fakes "fixed":** a `fake` finding where the model changed correct code and the tests still passed. This is the failure that matters most: a fixer that believes every report makes confident, tested, wrong changes.
- **Debatable:** listed in the results, never scored.
- **Not run:** no result file, or an `error` record (for example a missing context file). The scoreboard shows this count, so a broken set is visible.

## Building the set

1. **Pick the base commit** where the real bugs exist, with the test files the findings name. Fixes land in later commits; the bench always starts from this one. Pin it by sha.
2. **Real bugs, confirmed against the code.** Take findings from real reviews of this code (see `ask_model.py` and `merge_reviews.py`), then check each one yourself: read the code, reproduce or at least trace it. Only a bug you confirmed is `real`. Rate its difficulty by what the fixer must work out: `easy` is one function and an obvious fix, `medium` needs a second function or a decision, `hard` spans several sites or state across calls. Include a few hard ones; easy bugs do not separate models. Mark pure hardening `latent`.
3. **Fakes taken from wrong claims real reviewers made.** When a reviewer asserts something and the code disproves it, keep the finding exactly as written (evidence, situation, fix) and file it as `fake`. Do not write fakes yourself: invented fakes are usually too silly or too tidy, and models spot them. Real wrong claims are plausible, confident and subtly misread the code, which is exactly what a fixer faces. Aim for roughly one fake per two real bugs.
4. **Debatable:** findings that depend on a design choice. Include them if you want to read what the model does; they are not scored.
5. **Format** every item in the review format (`review_schema.json`): verbatim `evidence`, `symbol` containing it, `test_file` that exists at the base commit. Check the whole set against a checkout of the base commit with `merge_reviews.py ... --root <checkout>`: the evidence, symbol and test file flags should all be clean. Wrap the items as `{"summary": "...", "findings": [...], "checked_ok": []}`.
6. **Shuffle** the order, so neighbours do not give a kind away, then write `key.json` with the shuffled numbers. Keep titles neutral: a title that says "(fake)" or names the fix leaks the answer.
7. **Run the readiness check first** (`check_findings.py findings.json --root <checkout of the base commit> --out bench/readiness.md`). A finding the fixer cannot succeed on, because the test file is wrong, the bug is not shown by the code it gets, or the fix needs code it is not shown, is a bad task. Repair it (a better `test_file`, `related` code) or drop it, then check again. Otherwise you are scoring the set, not the model.

## Running it

```sh
python3 fix_bench.py --bench bench --repo ../myproj --label haiku-5.5 --runs 3 \
    --test-cmd "pytest {test_file}" --rules examples/rules_python.md --outline tests/helpers.py
python3 fix_bench.py --bench bench --repo ../myproj --label mimo-pro --runs 3 \
    --model xiaomi/mimo-v2.6-pro --provider deepinfra --strict-schema \
    --test-cmd "pytest {test_file}" --rules examples/rules_python.md --outline tests/helpers.py
```

- **Three runs per model.** One run is mostly luck: the same model scored 6, 6 and 7 of 9 on the same set, and its wrong-claim refusals ranged from 5 to 7 of 7. Report the range.
- Each run gets a fresh worktree at the base commit, so runs do not contaminate each other. Use the same flags (rules, outlines, test command) for every model you compare.
- **Read the diffs** in `results/<label>/run<N>/diffs/`. Passing tests do not prove a fix is right: a test can be written to pass, and a "fixed" fake is a failure however green it is. Look at the fakes that passed first.
- Fixing the key (a bug you mislabeled, a better difficulty) does not need a rerun: `fix_bench.py --bench bench --rescore` rebuilds `scoreboard.md` and every `score.json` from the saved results.

## Limits

A bench measures the code it was built from. A set in one language and one codebase says little about another, and a few dozen findings give wide error bars. Models improve and prices move, so keep the date on the scoreboard and rebuild the set when the code has changed enough that the bugs are gone.
