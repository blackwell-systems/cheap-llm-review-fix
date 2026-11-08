"""Find a named symbol (function, method, class, constant) in a source file and
return its line range. Used by merge_reviews.py (does the named symbol exist
and contain the evidence?) and model_fix.py (show the model exactly that code).

It is a line-based heuristic, not a parser: good enough to cut out the code a
reviewer talked about, and it returns None rather than guess when it can't
find the name or doesn't know the language.
"""
import os
import re

INDENT_EXTS = {".py", ".gd"}
BRACE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cc", ".cpp",
              ".h", ".hpp", ".cs", ".php"}

DEF_LINE = re.compile(r"^(\s*)(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?(?:async\s+)?"
                      r"(?:func|def|class|class_name|const|var|enum|signal)\s+([A-Za-z_]\w*)")
PY_ASSIGN = re.compile(r"^(\s*)([A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)")

KEYWORDS = ("function|func|fn|fun|def|class|struct|interface|enum|impl|type|trait|namespace|module|mod|"
            "object|union|record|protocol|extension")
CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "return", "else", "new", "throw", "await", "yield",
                 "case", "do", "try", "with", "typeof", "delete", "sizeof", "elif", "using", "match"}


def symbol_name(symbol: str) -> str:
    """The last segment of `Class.method`, `Class::method` or `path:Symbol`,
    without a call's `()`."""
    return re.split(r"[.:]", symbol.strip().rstrip("()"))[-1].strip()


def _lead_above(lines: list[str], index: int, prefixes: tuple[str, ...], brackets: bool = False) -> int:
    """0-based index of the first comment, doc comment or decorator line
    directly above line `index` (or `index` itself when there is none)."""
    start = index
    while start > 0:
        above = lines[start - 1].strip()
        if above.startswith(prefixes) or (brackets and re.fullmatch(r"\[[^\]]*\]", above)):
            start -= 1
        else:
            break
    return start


def _indent_range(lines: list[str], name: str, assignments: bool) -> tuple[int, int] | None:
    found = None
    for index, line in enumerate(lines):
        match = DEF_LINE.match(line)
        if match and match.group(2) == name:
            found = (index, match.group(1))
            break
    if found is None and assignments:
        for index, line in enumerate(lines):
            match = PY_ASSIGN.match(line)
            if match and match.group(2) == name:
                found = (index, match.group(1))
                break
    if found is None:
        return None
    index, lead = found
    if lines[index].lstrip().startswith("class_name"):
        return 1, len(lines)
    indent = len(lead.expandtabs(4))
    end = len(lines)
    for later in range(index + 1, len(lines)):
        text = lines[later]
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        leading = text[:len(text) - len(text.lstrip())]
        if len(leading.expandtabs(4)) <= indent:
            # A closing bracket ends a multi-line const or var: keep it.
            end = later + 1 if stripped[0] in ")]}" else later
            break
    # Blank lines and the next definition's doc comments aren't part of it.
    while end > index + 1 and (not lines[end - 1].strip() or lines[end - 1].strip().startswith("#")):
        end -= 1
    return _lead_above(lines, index, ("#", "@")) + 1, end


def code_only(line: str, state: dict | None = None) -> str:
    """`line` with string literals and comments removed, roughly: enough to
    count braces. `state` carries block comments and template strings across
    lines (pass the same dict for consecutive lines)."""
    state = state if state is not None else {}
    out: list[str] = []
    i = 0
    while i < len(line):
        two, char = line[i:i + 2], line[i]
        if state.get("block"):
            if two == "*/":
                state["block"] = False
                i += 2
            else:
                i += 1
        elif state.get("template"):
            if char == "\\":
                i += 2
            else:
                if char == "`":
                    state["template"] = False
                i += 1
        elif two == "//":
            break
        elif two == "/*":
            state["block"] = True
            i += 2
        elif char == "`":
            state["template"] = True
            i += 1
        elif char in "\"'":
            j = i + 1
            while j < len(line) and line[j] != char:
                j += 2 if line[j] == "\\" else 1
            if j >= len(line):  # no closing quote on this line (a Rust lifetime, say): keep the character
                out.append(char)
                i += 1
            else:
                i = j + 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _brace_patterns(name: str) -> list[re.Pattern]:
    n = re.escape(name)
    return [
        re.compile(rf"\b(?:{KEYWORDS})\s*\*?\s*(?:<[^>]*>\s*)?{n}\b"),
        re.compile(rf"\bfunc\s*\([^)]*\)\s*{n}\b"),                           # Go method
        re.compile(rf"\bimpl\b[^{{;]*\b{n}\b"),                                # Rust impl block
        re.compile(rf"\b(?:const|let|var|val|static|final)\s+(?:mut\s+)?{n}\b"),
        re.compile(rf"(?:^|[\s,{{(]){n}\s*[:=]\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|\w+\s*=>)"),
    ]


def _method_start(lines: list[str], index: int, name: str) -> bool:
    """Whether line `index` defines a method `name(...) {`: only
    declaration-like text before the name, and a `{` (not a `;`) after it
    within a few lines."""
    stripped = lines[index].strip()
    if stripped.startswith(("//", "*", "/*", "#")):
        return False
    match = re.search(rf"(?:^|[^\w.])({re.escape(name)})\s*\(", stripped)
    if not match:
        return False
    before = stripped[:match.start(1)]
    if re.search(r"[=(){};]", before) or CONTROL_WORDS & set(re.findall(r"\w+", before)):
        return False
    state: dict = {}
    for later in range(index, min(index + 12, len(lines))):
        text = code_only(lines[later], state)
        if later == index:
            text = text[max(0, text.find(name)):]
        for char in text:
            if char == "{":
                return True
            if char == ";":
                return False
    return False


def _brace_end(lines: list[str], index: int) -> int:
    """0-based index of the line where the definition starting at `index`
    ends: the closing brace of its body, or the end of the statement for a
    constant or a body-less declaration."""
    state: dict = {}
    depth = parens = 0
    body = False
    for number in range(index, len(lines)):
        text = code_only(lines[number], state)
        for char in text:
            if char in "([":
                parens += 1
            elif char in ")]":
                parens -= 1
            elif char == "{":
                if parens <= 0:
                    body = True
                depth += 1
            elif char == "}":
                depth -= 1
        if body and depth <= 0 and parens <= 0:
            return number
        if not body and depth <= 0 and parens <= 0:
            code = text.rstrip()
            if code.endswith(";"):
                return number
            if code and not re.search(r"[,+\-*/&|=(\[{.:?<]$", code):
                following = next((lines[k].strip() for k in range(number + 1, len(lines)) if lines[k].strip()), "")
                if not following.startswith(("{", ".", ")", "?", ":", "+", "-", "*", "/", "&", "|")):
                    return number
    return len(lines) - 1


def _brace_range(lines: list[str], name: str) -> tuple[int, int] | None:
    patterns = _brace_patterns(name)
    start = None
    state: dict = {}
    for index, line in enumerate(lines):
        text = code_only(line, state)
        if any(pattern.search(text) for pattern in patterns):
            start = index
            break
    if start is None:  # second pass: `name(...) {` method definitions
        start = next((i for i in range(len(lines)) if _method_start(lines, i, name)), None)
    if start is None:
        return None
    end = _brace_end(lines, start)
    return _lead_above(lines, start, ("//", "/*", "*", "#", "@"), brackets=True) + 1, end + 1


def symbol_range(lines: list[str], symbol: str, path: str = "") -> tuple[int, int] | None:
    """The 1-based line range of `symbol` (for example `Skater._land`,
    `Cart.add`, `add`) in the file `path`, or None when it isn't found or the
    language is unsupported. The file extension picks the strategy:

    - .py and .gd (indentation): a `func`, `def`, `class`, `const`, `var`,
      `enum` or `signal` of that name (for .py also a plain assignment such as
      `LIMIT = 5`). The range ends before the next line indented no deeper than
      the definition, keeping a closing bracket line. A GDScript file's own
      `class_name` covers the whole file. Without a path these rules apply too.
    - brace languages (BRACE_EXTS): a line that defines the name with
      `function`, `func`, `fn`, `class`, `struct`, `interface`, `enum`, `impl`,
      `type`, `def` and similar, or `const|let|var name =`, `name: function`,
      an arrow function, or a `name(...) {` method. The range runs to the
      matching closing brace (braces in string literals and line comments are
      ignored, roughly).

    Comment lines (`//`, `///`, `/**`, `#`, `##`) and decorators or annotations
    directly above belong to the symbol."""
    name = symbol_name(symbol)
    if not name:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in BRACE_EXTS:
        return _brace_range(lines, name)
    if ext in INDENT_EXTS or not path:
        return _indent_range(lines, name, assignments=ext == ".py")
    return None
