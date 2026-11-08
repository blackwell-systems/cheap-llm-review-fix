- Godot 4.7 GDScript with static typing everywhere: typed variables, parameters and return types.
  Untyped declarations are errors in this project.
- Indent with tabs.
- Use the project's shared id constants (for example `Tricks`, `InputActions`) rather than string
  literals; feel numbers live in the tuning resource, not in code.
- Tests are GDScript files under `tests/`: methods start with `test_`, use the assertions the shown
  file already uses. Run through `./run_tests.sh <filter>`.
- Gameplay rules live in pure classes with no node or scene calls; keep engine calls out of them.
- Comments: short and plain.
