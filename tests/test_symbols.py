import unittest

from symbols import symbol_range

PY = '''import os

LIMIT = 5


class Cart:
    """A cart."""

    # Adds an item.
    @staticmethod
    def add(item):
        if item:
            return 1
        return 0

    def total(self):
        return 2


def helper():
    return [
        1,
        2,
    ]
'''

GD = '''extends Node

## How fast.
const SPEED: float = 3.0
const TABLE := {
\t"a": 1,
\t"b": 2,
}

## Lands the cart.
@export var foo: int = 1

func _land(x: int) -> void:
\tif x:
\t\tpass
\tprint(x)

func other() -> void:
\tpass
'''

TS = '''// Utilities.
import { x } from "y";

export const LIMIT = 10;

/**
 * Adds numbers.
 */
export function add(a: number, b: number): number {
  const s = "}"; // a } in a comment
  if (a > b) {
    return a;
  }
  return a + b;
}

export class Cart {
  private items: string[] = [];

  /** Adds. */
  public addItem(item: string): void {
    this.items.push(item);
  }

  total(): number {
    return this.items.length;
  }
}

const double = (n: number) => {
  return n * 2;
};

const handlers = {
  save: function (x) {
    return x;
  },
};
'''

GO = '''package main

// Max is the limit.
const Max = 10

type Cart struct {
\tItems []string
}

// Add appends an item.
func (c *Cart) Add(item string) {
\tc.Items = append(c.Items, "{")
}

func Total(
\tc *Cart,
) int {
\treturn len(c.Items)
}
'''


def span(text, symbol, path):
    return symbol_range(text.splitlines(), symbol, path)


class PythonTest(unittest.TestCase):
    def test_function_with_end_before_next_def(self):
        self.assertEqual(span(PY, "helper", "a.py"), (20, 24))

    def test_method_in_class_includes_decorator_and_comment(self):
        self.assertEqual(span(PY, "Cart.add", "a.py"), (9, 14))

    def test_class_covers_its_methods(self):
        self.assertEqual(span(PY, "Cart", "a.py"), (6, 17))

    def test_constant_assignment(self):
        self.assertEqual(span(PY, "LIMIT", "a.py"), (3, 3))

    def test_missing(self):
        self.assertIsNone(span(PY, "nope", "a.py"))

    def test_call_parens_and_dots_in_symbol(self):
        self.assertEqual(span(PY, "Cart.total()", "a.py"), (16, 17))


class GDScriptTest(unittest.TestCase):
    def test_function(self):
        self.assertEqual(span(GD, "Skater._land", "a.gd"), (13, 16))

    def test_annotation_and_doc_comment_belong_to_var(self):
        self.assertEqual(span(GD, "foo", "a.gd"), (10, 11))

    def test_const_with_closing_bracket_kept(self):
        self.assertEqual(span(GD, "TABLE", "a.gd"), (5, 8))

    def test_const_with_doc_comment(self):
        self.assertEqual(span(GD, "SPEED", "a.gd"), (3, 4))

    def test_class_name_covers_file(self):
        text = "class_name Foo\nextends Node\nfunc a():\n\tpass\n"
        self.assertEqual(span(text, "Foo", "foo.gd"), (1, 4))


class BraceLanguageTest(unittest.TestCase):
    def test_ts_function_with_doc_comment_and_braces_in_strings(self):
        self.assertEqual(span(TS, "add", "a.ts"), (6, 15))

    def test_ts_method_in_class(self):
        self.assertEqual(span(TS, "Cart.addItem", "a.ts"), (20, 23))

    def test_ts_method_without_keyword(self):
        self.assertEqual(span(TS, "total", "a.ts"), (25, 27))

    def test_ts_class(self):
        self.assertEqual(span(TS, "Cart", "a.tsx"), (17, 28))

    def test_ts_const_statement(self):
        self.assertEqual(span(TS, "LIMIT", "a.ts"), (4, 4))

    def test_ts_arrow_function(self):
        self.assertEqual(span(TS, "double", "a.js"), (30, 32))

    def test_ts_name_colon_function(self):
        self.assertEqual(span(TS, "save", "a.js"), (35, 37))

    def test_go_method_with_comment(self):
        self.assertEqual(span(GO, "Cart.Add", "a.go"), (10, 13))

    def test_go_struct_type(self):
        self.assertEqual(span(GO, "Cart", "a.go"), (6, 8))

    def test_go_const_without_semicolon(self):
        self.assertEqual(span(GO, "Max", "a.go"), (3, 4))

    def test_go_multiline_signature(self):
        self.assertEqual(span(GO, "Total", "a.go"), (15, 19))

    def test_rust_fn_and_impl(self):
        rs = "struct A;\n\nimpl A {\n    pub fn go(&self) -> u8 {\n        let c = '{';\n        1\n    }\n}\n"
        self.assertEqual(span(rs, "go", "a.rs"), (4, 7))
        self.assertEqual(span(rs, "A", "a.rs"), (1, 1))

    def test_calls_are_not_definitions(self):
        text = "function run() {\n  add(1, 2);\n}\n"
        self.assertIsNone(span(text, "add", "a.js"))

    def test_unknown_language_is_none(self):
        self.assertIsNone(span(PY, "helper", "a.rb"))


if __name__ == "__main__":
    unittest.main()
