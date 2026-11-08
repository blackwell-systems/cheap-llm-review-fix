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


# ---------------------------------------------------------------------------
# Outlines: the declarations of a file, for a fixer shown only part of it.

OUTLINE_LIMIT = 200
MAX_OUTLINE_LINE = 200
C_EXTS = {".c", ".cc", ".cpp", ".h", ".hpp"}
OUTLINE_EXTS = INDENT_EXTS | BRACE_EXTS

GD_DECL = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?(?:extends|class_name|class|const|var|enum|signal|func)\b")
PY_DECL = re.compile(r"^(?:async\s+def|def|class)\s+\w+")
PY_FIELD = re.compile(r"^[A-Za-z_]\w*\s*(?::[^=]+)?=(?!=)")
PY_ANNOTATION = re.compile(r"^[A-Za-z_]\w*\s*:\s*[^=\s]")
PY_NOT_NAMES = {"else", "try", "finally", "except", "lambda", "case", "default", "if", "elif", "while", "for",
                "with", "match"}
CLASS_LINE = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s+)*class\b")

MODIFIERS = (r"(?:(?:export|default|declare|abstract|async|public|private|protected|internal|static|final|"
             r"override|open|sealed|partial|virtual|inline|readonly|unsafe|extern|data|inner|suspend|operator|"
             r"mutating|nonisolated|fileprivate|constexpr|volatile|synchronized|native|transient|companion|"
             r"lateinit|pub(?:\([^)]*\))?)\s+)*")
KEYWORD_DECL = re.compile(
    rf"^{MODIFIERS}(?:class|struct|interface|enum|trait|impl|type|object|protocol|extension|union|record|"
    r"namespace|module|mod|function\*?|func|fn|fun|def|const|let|var|val|static|typedef)\s+\S")
TOKEN = r"[\w$<>\[\],.?*&:~]+"
METHOD = re.compile(rf"^{MODIFIERS}((?:{TOKEN}\s+)*)([\w$~]+)\s*(?:<[^>()]*>)?\s*\(")
TYPED_FIELD = re.compile(rf"^{MODIFIERS}[A-Za-z_$][\w$]*[?!]?\s*:\s*[^:\s=][^;]*[;,=]?\s*$")
C_FIELD = re.compile(rf"^{MODIFIERS}{TOKEN}(?:\s+{TOKEN})*\s+[*&]*[\w$]+(?:\[[^\]]*\])?\s*(?:=[^=].*)?;\s*$")
ASSIGN_FIELD = re.compile(rf"^{MODIFIERS}[A-Za-z_$#][\w$]*\s*=[^=>]")
GO_FIELD = re.compile(r"^[A-Za-z_]\w*(?:\s*,\s*\w+)*\s+[\w\[\]*.{}]+\s*$")
CONTAINER_HEADER = re.compile(r"\b(?:class|struct|interface|enum|trait|impl|object|protocol|extension|union|record)\b")
TRANSPARENT_HEADER = re.compile(r"\b(?:namespace|module)\b")
LEADING_ANNOTATION = re.compile(r"^@[\w.]+(?:\([^)]*\))?\s*")


def _indent_unit(lines: list[str]) -> int:
    """Width of one indentation step made of spaces (4 when unclear)."""
    widths = []
    previous = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lead = line[:len(line) - len(line.lstrip())]
        if previous.endswith(":") and lead and set(lead) == {" "}:
            widths.append(len(lead))
        previous = stripped
    return min(widths) if widths else 4


def _level(line: str, unit: int) -> int:
    lead = line[:len(line) - len(line.lstrip())]
    return lead.count("\t") + lead.count(" ") // unit


def _indent_outline(lines: list[str], ext: str) -> list[int]:
    """Line numbers (1-based) of the declarations of a Python or GDScript
    file at the top level and one level into a class."""
    unit = _indent_unit(lines)
    kept: list[int] = []
    in_class = False
    quote = ""
    for number, line in enumerate(lines, 1):
        inside_string = bool(quote)
        for mark in ('"""', "'''"):
            if quote == mark:
                if mark in line:
                    quote = ""
            elif not quote and line.count(mark) % 2 == 1:
                quote = mark
        stripped = line.strip()
        if inside_string or not stripped or stripped.startswith("#"):
            continue
        level = _level(line, unit)
        if level > 1:
            continue
        if level == 1 and not in_class:
            continue
        if ext == ".py":
            first = re.match(r"\w+", stripped)
            hit = bool(PY_DECL.match(stripped) or PY_FIELD.match(stripped)
                       or (PY_ANNOTATION.match(stripped) and first and first.group() not in PY_NOT_NAMES))
        else:
            hit = bool(GD_DECL.match(stripped))
        if hit:
            kept.append(number)
        if level == 0 and not (stripped.startswith("@") and " " not in stripped):
            in_class = bool(CLASS_LINE.match(stripped))
    return kept


def _is_method_header(code: str, in_container: bool, depth: int, ext: str) -> bool:
    text = code
    while LEADING_ANNOTATION.match(text):
        text = LEADING_ANNOTATION.sub("", text, count=1)
    match = METHOD.match(text)
    if not match:
        return False
    types = match.group(1).split()
    name = match.group(2)
    if name in CONTROL_WORDS or CONTROL_WORDS & set(types):
        return False
    if types:
        # `Type name(...)`: a function at the top level only in C-like files, in a class body anywhere.
        return in_container or (depth == 0 and ext in C_EXTS)
    # A bare `name(...)`: a constructor or an interface method, only inside a type body.
    if not in_container:
        return False
    rest = text[match.end():]
    return not text.rstrip().endswith(";") or "):" in text or ") :" in text or "->" in rest


def _brace_outline(lines: list[str], ext: str) -> list[int]:
    """Line numbers (1-based) of declarations at the top level and one level
    into a type body (class, struct, interface, enum, trait, impl, object ...).
    Braces are counted with `code_only`; a `namespace` or `module` block does
    not count as a level."""
    kept: list[int] = []
    stack: list[tuple[bool, bool]] = []  # (transparent, container)
    state: dict = {}
    statement = ""
    for number, line in enumerate(lines, 1):
        code = code_only(line, state).strip()
        if not code:
            continue
        levels = [entry for entry in stack if not entry[0]]
        depth = len(levels)
        in_container = depth == 1 and levels[0][1]
        if depth == 0 or in_container:
            plain = code.lstrip()
            while LEADING_ANNOTATION.match(plain):
                plain = LEADING_ANNOTATION.sub("", plain, count=1)
            hit = False
            if plain and not plain.startswith(("#", "}", ")")):
                hit = bool(KEYWORD_DECL.match(plain)) or _is_method_header(plain, in_container, depth, ext)
                if not hit and in_container:
                    hit = bool(TYPED_FIELD.match(plain) or C_FIELD.match(plain) or ASSIGN_FIELD.match(plain)
                               or (ext == ".go" and GO_FIELD.match(plain)))
            if hit:
                kept.append(number)
        segment = 0
        for index, char in enumerate(code):
            if char == "{":
                header = f"{statement} {code[segment:index]}"
                transparent = bool(TRANSPARENT_HEADER.search(header))
                stack.append((transparent, not transparent and bool(CONTAINER_HEADER.search(header))))
                statement, segment = "", index + 1
            elif char == "}":
                if stack:
                    stack.pop()
                statement, segment = "", index + 1
            elif char == ";":
                statement, segment = "", index + 1
        statement += " " + code[segment:]
    return kept


def outline(lines: list[str], path: str, limit: int = OUTLINE_LIMIT) -> list[str] | None:
    """The declarations of a file, each as `N: text` (N the 1-based line
    number), or None when the language isn't supported. Python and GDScript
    are read by indentation: classes, functions and methods, constants,
    variables, enums, signals (GDScript `extends` and `class_name` too), at the
    top level and one level into a class. Brace languages (BRACE_EXTS) are read
    by brace depth: classes, structs, interfaces, enums, traits, impls,
    functions, methods, constants and fields at the top level and one level
    into a type body. The regexes are per family and approximate. At most
    `limit` lines, then a count of the rest."""
    ext = os.path.splitext(path)[1].lower()
    if ext in INDENT_EXTS:
        numbers = _indent_outline(lines, ext)
    elif ext in BRACE_EXTS:
        numbers = _brace_outline(lines, ext)
    else:
        return None
    result = [f"{n}: {lines[n - 1].rstrip()[:MAX_OUTLINE_LINE]}" for n in numbers[:limit]]
    if len(numbers) > limit:
        result.append(f"... {len(numbers) - limit} more declarations")
    return result
