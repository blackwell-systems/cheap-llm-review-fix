Project: a Godot 4.7.2 game in GDScript. Tests extend a `TestSuite` (tests/support/test_suite.gd)
and run through `./run_tests.sh <filter>`.

- `tests/unit/` has no scene tree and no physics world: pure logic only (rule classes, math,
  parsing). A node-dependent test there cannot run.
- `tests/scenarios/` drives a real level with scripted input (`tests/support/scenario.gd`): use
  it when the bug needs nodes, physics or several ticks of play.
- `tests/levels/` tests level building and validation.
- Test methods start with `test_`; use the assertions the shown file already uses.
- Instances of one loaded scene share sub-resources, so a test that edits a resource in place
  must edit a `duplicate(true)` copy.
