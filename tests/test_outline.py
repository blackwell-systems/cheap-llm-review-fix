import os
import tempfile
import unittest

from model_fix import build_context, outline_block, resolve_provider
from symbols import outline

PY = '''"""Module doc.

def not_a_function():
"""
import os

LIMIT = 5
NAME: str = "x"


@decorator
class Cart:
    """A cart.

    def inside_docstring(self):
    """

    MAX = 3
    items: list = []

    def add(self, item):
        total = 1
        def nested():
            pass
        return total

    async def load(self):
        pass


def helper():
    local = 1
    return local


if True:
    flag = 1
'''

GD = '''extends Node
class_name Skater

signal landed(speed: float)
enum State { GROUND, AIR }
const SPEED: float = 3.0
@export var weight: float = 1.0
var _state: State = State.GROUND


class Inner:
	var depth: int = 0

	func inner_method() -> void:
		var local: int = 1


func _ready() -> void:
	var local: int = 2
	if local:
		pass


static func make() -> Skater:
	return null
'''

JS = '''import x from "y";
export class Cart extends Base {
  count = 0;
  static LIMIT = 3;
  constructor(items) {
    this.items = items;
    const hidden = 1;
  }
  async add(item) {
    return this.items.push(item);
  }
  get total() {
    return 1;
  }
}

export function helper(a) {
  const inner = 2;
  return a;
}

const arrow = (x) => {
  return x;
};
interface Shape {
  area(): number;
  name: string;
}
'''

GO = '''package main

type Cart struct {
	Items []string
	count int
}

type Shape interface {
	Area() float64
}

func (c *Cart) Add(item string) {
	total := 1
}

func helper() int {
	return 1
}

const Limit = 3
'''

RUST = '''use std::fmt;

pub struct Cart {
    pub items: Vec<String>,
    count: u32,
}

impl Cart {
    pub const LIMIT: u32 = 3;
    pub fn add(&mut self, item: String) {
        let total = 1;
    }
}

fn helper() -> u32 {
    let inner = 2;
    inner
}
'''

JAVA = '''package app;

public class Cart {
    private int count = 0;
    private static final int LIMIT = 3;

    @Override
    public void add(String item) {
        int total = 1;
        System.out.println(total);
    }

    public Cart(int n) {
        this.count = n;
    }

    interface Inner {
        void deep();
    }
}
'''

CSHARP = '''namespace App
{
    public class Cart
    {
        private int count = 0;

        public void Add(string item)
        {
            var total = 1;
            Console.WriteLine(total);
        }
    }
}
'''

C = '''#include <stdio.h>
#define MAX 3

struct cart {
    int count;
    char *name;
};

static int helper(int a) {
    int total = 1;
    printf("%d", total);
    return a;
}

int main(void)
{
    helper(1);
    return 0;
}
'''


def numbered(source, path):
    return outline(source.splitlines(), path)


def has(found, text):
    return any(text in line for line in found)


class OutlineTest(unittest.TestCase):
    def test_python(self):
        found = numbered(PY, "cart.py")
        for want in ("LIMIT = 5", 'NAME: str = "x"', "class Cart:", "MAX = 3", "items: list = []",
                     "def add(self, item):", "async def load(self):", "def helper():"):
            self.assertTrue(has(found, want), want)
        for skip in ("not_a_function", "inside_docstring", "def nested", "total = 1", "local = 1", "flag = 1",
                     "import os", "@decorator"):
            self.assertFalse(has(found, skip), skip)
        self.assertTrue(found[0].split(":")[0].isdigit())

    def test_python_two_space_indent(self):
        found = numbered("class A:\n  x = 1\n  def f(self):\n    y = 2\n", "a.py")
        self.assertEqual([line.split(":")[0] for line in found], ["1", "2", "3"])

    def test_gdscript_tabs(self):
        found = numbered(GD, "skater.gd")
        for want in ("extends Node", "class_name Skater", "signal landed", "enum State", "const SPEED",
                     "@export var weight", "var _state", "class Inner:", "var depth", "func inner_method",
                     "func _ready", "static func make"):
            self.assertTrue(has(found, want), want)
        self.assertFalse(has(found, "var local"))
        self.assertFalse(has(found, "if local"))

    def test_javascript_and_typescript(self):
        found = numbered(JS, "cart.ts")
        for want in ("export class Cart", "count = 0;", "static LIMIT", "constructor(items)", "async add(item)",
                     "get total()", "export function helper", "const arrow", "interface Shape", "area(): number;",
                     "name: string;"):
            self.assertTrue(has(found, want), want)
        for skip in ("this.items", "const hidden", "const inner", "return"):
            self.assertFalse(has(found, skip), skip)

    def test_go(self):
        found = numbered(GO, "cart.go")
        for want in ("type Cart struct", "Items []string", "count int", "type Shape interface", "Area() float64",
                     "func (c *Cart) Add", "func helper", "const Limit"):
            self.assertTrue(has(found, want), want)
        self.assertFalse(has(found, "total := 1"))

    def test_rust(self):
        found = numbered(RUST, "cart.rs")
        for want in ("pub struct Cart", "pub items: Vec<String>,", "count: u32,", "impl Cart", "pub const LIMIT",
                     "pub fn add", "fn helper"):
            self.assertTrue(has(found, want), want)
        for skip in ("let total", "let inner", "use std"):
            self.assertFalse(has(found, skip), skip)

    def test_java(self):
        found = numbered(JAVA, "Cart.java")
        for want in ("public class Cart", "private int count = 0;", "private static final int LIMIT",
                     "public void add(String item)", "public Cart(int n)", "interface Inner"):
            self.assertTrue(has(found, want), want)
        for skip in ("int total", "System.out", "this.count", "void deep"):
            self.assertFalse(has(found, skip), skip)

    def test_csharp_namespace_is_not_a_level(self):
        found = numbered(CSHARP, "Cart.cs")
        for want in ("namespace App", "public class Cart", "private int count = 0;", "public void Add("):
            self.assertTrue(has(found, want), want)
        for skip in ("var total", "Console"):
            self.assertFalse(has(found, skip), skip)

    def test_c(self):
        found = numbered(C, "cart.c")
        for want in ("struct cart", "int count;", "char *name;", "static int helper(int a)", "int main(void)"):
            self.assertTrue(has(found, want), want)
        for skip in ("int total", "printf", "helper(1);", "return", "#include", "#define"):
            self.assertFalse(has(found, skip), skip)

    def test_unsupported_language(self):
        self.assertIsNone(outline(["def x"], "a.rb"))

    def test_cap(self):
        source = "\n".join(f"def f{i}():\n    pass" for i in range(250))
        found = outline(source.splitlines(), "big.py")
        self.assertEqual(len(found), 201)
        self.assertEqual(found[-1], "... 50 more declarations")
        self.assertEqual(len(outline(source.splitlines(), "big.py", limit=10)), 11)


class BuildContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for name, text in (("cart.py", PY), ("helpers.py", "def make():\n    pass\n"), ("notes.txt", "hello\n")):
            with open(os.path.join(self.root, name), "w") as handle:
                handle.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outline_follows_named_symbols_and_marks_not_for_find(self):
        text = build_context(self.root, ["cart.py:Cart.add"], [])
        self.assertIn("===== FILE: cart.py (lines", text)
        self.assertIn("===== OUTLINE: cart.py (declarations only, with line numbers; not for `find`)", text)
        self.assertLess(text.index("FILE: cart.py"), text.index("OUTLINE: cart.py"))
        self.assertIn("class Cart:", text)

    def test_whole_file_gets_no_outline(self):
        text = build_context(self.root, ["cart.py"], [])
        self.assertNotIn("OUTLINE", text)

    def test_extra_outline_files(self):
        text = build_context(self.root, ["cart.py:helper"], ["helpers.py", "helpers.py", "missing.py"])
        self.assertEqual(text.count("===== OUTLINE: helpers.py"), 1)
        self.assertNotIn("missing.py", text)

    def test_unsupported_language_is_shown_whole_without_outline(self):
        text = build_context(self.root, ["notes.txt:anything"], [])
        self.assertIn("(whole file)", text)
        self.assertNotIn("OUTLINE", text)
        self.assertEqual(outline_block(self.root, "notes.txt"), "")


class ProviderDefaultsTest(unittest.TestCase):
    def test_anthropic_model_without_provider(self):
        self.assertEqual(resolve_provider("anthropic/claude-haiku-5.5", [], False),
                         (["google-vertex", "anthropic"], True))

    def test_explicit_provider_is_kept(self):
        self.assertEqual(resolve_provider("anthropic/claude-haiku-5.5", ["anthropic"], False), (["anthropic"], False))

    def test_other_models_unchanged(self):
        self.assertEqual(resolve_provider("xiaomi/mimo-v2.6-pro", ["deepinfra"], True), (["deepinfra"], True))
        self.assertEqual(resolve_provider("z-ai/glm-5.3-flash", [], False), ([], False))


if __name__ == "__main__":
    unittest.main()
