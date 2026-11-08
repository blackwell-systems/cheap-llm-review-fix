- Python 3.10+, standard library only unless the file already imports something else.
- Indent with four spaces. Keep type hints that the surrounding code uses, and add them on new
  functions if the file uses them.
- Tests are `unittest` or `pytest` style, whichever the shown test file uses: `test_` functions or
  methods, plain `assert` for pytest and `self.assert*` for unittest. Do not add new dependencies
  or fixtures unless the file already has them.
- Raise the exception type the surrounding code raises; do not add broad `except Exception`.
- Do not change public function signatures unless the task says so.
