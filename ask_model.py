#!/usr/bin/env python3
"""Ask a model on OpenRouter a question with files and images attached.

Used for code review, second opinions and (through model_fix.py) small fixes.
The key comes from OPENROUTER_API_KEY or the macOS keychain (service
"openrouter"); it is never printed. Requests set data_collection to "deny",
so providers that collect data are refused.

Defaults: model xiaomi/mimo-v2.6-pro, providers tried in order deepinfra then
xiaomi (override with --model and --provider; --no-fallback stops OpenRouter
from trying any other provider). With --json-schema the schema goes into the
prompt, and the answer is parsed (prose, an echoed schema and unescaped quotes
inside code strings are tolerated) and validated. The answer streams, with a
progress line in <out>.progress while it runs.

  ask_model.py --prompt-file examples/review_prompt.md --file src/cart.py \
      --json-schema review_schema.json --strict-schema --out review.json
"""
import argparse
import base64
import json
import mimetypes
import os
import subprocess
import time
import sys
import urllib.request
from typing import Any

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "xiaomi/mimo-v2.6-pro"
DEFAULT_PROVIDERS = ["deepinfra", "xiaomi"]


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    try:
        return subprocess.run(["security", "find-generic-password", "-s", "openrouter", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("No key: set OPENROUTER_API_KEY or add a keychain item with service 'openrouter'.")


def file_block(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return f"===== FILE: {path} =====\n{handle.read()}\n===== END FILE: {path} =====\n"


def image_part(path: str) -> dict:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Validates `value` against the JSON schema subset our schemas use (type,
    required, properties, additionalProperties false, items, enum). Returns
    the problems found, empty when it conforms."""
    errors: list[str] = []
    kind = schema.get("type")
    checks = {"object": dict, "array": list, "string": str, "boolean": bool}
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return [f"{path}: expected a number"]
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return [f"{path}: expected an integer"]
    if kind in checks and not isinstance(value, checks[kind]):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing '{key}'")
        for key, item in value.items():
            if key in properties:
                errors += schema_errors(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False and not key.startswith("_"):
                errors.append(f"{path}: unexpected '{key}'")
    if kind == "array" and "items" in schema:
        for index, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}[{index}]")
    return errors


def _escape_stray_quotes(text: str, decoder: json.JSONDecoder, tries: int = 200) -> Any:
    """Repairs the usual broken answer: code inside a string with its quote
    marks unescaped (`"print("hi")"`). Each time the parser stops expecting a
    delimiter, the quote just before that spot is taken as part of the string
    and escaped. Returns the decoded object, or None."""
    for _ in range(tries):
        try:
            return decoder.raw_decode(text)[0]
        except json.JSONDecodeError as problem:
            if "delimiter" not in problem.msg:
                return None
            quote = text.rfind('"', 0, problem.pos)
            if quote <= 0:
                return None
            text = text[:quote] + "\\" + text[quote:]
    return None


def answer_object(text: str, schema: dict) -> tuple[Any, str]:
    """The JSON object in a model's reply. Models sometimes echo the schema
    or add prose around the answer, so every top-level object is decoded and
    the last one matching the schema wins (else the last object, so its
    schema problems get reported). Raw tabs and newlines inside strings are
    accepted (`strict=False`): models write code that way."""
    decoder = json.JSONDecoder(strict=False)
    found: list[Any] = []
    error = "no JSON object found"
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError as problem:
            error = str(problem)
            repaired = _escape_stray_quotes(text[index:], decoder)
            if repaired is not None:
                found.append(repaired)
                break
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            found.append(value)
        index = text.find("{", end)
    for value in reversed(found):
        if not schema_errors(value, schema):
            return value, ""
    return (found[-1], "") if found else (None, error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", default="", help="question text")
    parser.add_argument("--prompt-file", help="read the question from a file")
    parser.add_argument("--file", action="append", default=[], help="text file to attach (repeatable)")
    parser.add_argument("--image", action="append", default=[], help="image to attach (repeatable)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", action="append", help="provider order (default: deepinfra, xiaomi)")
    parser.add_argument("--no-fallback", action="store_true",
                        help="use only the listed providers (no fallback to others, e.g. heavily quantized ones)")
    parser.add_argument("--max-tokens", type=int, default=48000, help="output limit, reasoning included")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"],
                        help="reasoning effort (reasoning models spend output tokens thinking)")
    parser.add_argument("--out", help="write the answer here (default: stdout)")
    parser.add_argument("--json-schema", help="answer as JSON matching this schema file (for example "
                        "review_schema.json); the answer is parsed, validated against it and saved "
                        "as pretty JSON")
    parser.add_argument("--strict-schema", action="store_true",
                        help="also send the schema as OpenRouter's strict response_format (some providers, "
                        "for example Z.ai for GLM, reject it with a 404)")
    args = parser.parse_args()

    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as handle:
            prompt = handle.read() + ("\n\n" + prompt if prompt else "")
    schema = None
    if args.json_schema:
        with open(args.json_schema, encoding="utf-8") as handle:
            schema = json.load(handle)
        prompt += ("\n\nAnswer with ONLY one JSON object (no prose before or after, no code fence) that "
                   "matches this JSON schema:\n" + json.dumps(schema, indent=1))
    text = prompt + "\n\n" + "".join(file_block(path) for path in args.file)
    content = [{"type": "text", "text": text}] + [image_part(path) for path in args.image]
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": args.max_tokens,
        # Reasoning is streamed (not excluded) only so progress can show the model
        # thinking; it is counted, never saved. It is billed either way.
        "reasoning": {"effort": args.effort, "exclude": False},
        "provider": {"order": args.provider or DEFAULT_PROVIDERS, "data_collection": "deny",
                     "allow_fallbacks": not args.no_fallback},
    }
    if schema is not None and args.strict_schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "answer", "strict": True, "schema": schema}}
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), headers={
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "X-Title": "cheap-llm-review-fix",
    })
    # Streamed, so a long reasoning phase never trips a read timeout (OpenRouter
    # sends keep-alive comments while the model thinks). Progress goes to
    # <out>.progress so a long review can be watched.
    body["stream"] = True
    request.data = json.dumps(body).encode("utf-8")
    progress_path = (args.out + ".progress") if args.out else None
    started = time.time()
    parts: list[str] = []
    usage: dict = {}
    model = provider = None
    finish = None
    events = 0
    thinking = 0
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            events += 1
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                if "error" in chunk:
                    sys.exit(f"Error from the provider: {json.dumps(chunk['error'])[:2000]}")
                model = chunk.get("model", model)
                provider = chunk.get("provider", provider)
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    parts.append(delta.get("content") or "")
                    thinking += len(delta.get("reasoning") or "")
                    finish = choice.get("finish_reason") or finish
            if progress_path and events % 20 == 0:
                with open(progress_path, "w", encoding="utf-8") as handle:
                    handle.write(f"{time.time() - started:.0f} s, thinking {thinking} chars, "
                                 f"answer {sum(len(p) for p in parts)} chars\n")
    answer = "".join(parts)
    if progress_path and os.path.exists(progress_path):
        os.remove(progress_path)
    if not answer.strip():
        sys.exit(f"Empty answer (finish reason {finish}): the output limit went on "
                 "reasoning. Raise --max-tokens or lower --effort.")
    note = (f"model {model}, provider {provider}, "
            f"tokens in {usage.get('prompt_tokens')}, out {usage.get('completion_tokens')}, "
            f"cost {usage.get('cost')}, {time.time() - started:.0f} s")
    if schema is not None:
        cleaned = answer.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed, error = answer_object(cleaned, schema)
        if parsed is None:
            if args.out:
                with open(args.out + ".raw", "w", encoding="utf-8") as handle:
                    handle.write(answer)
            sys.exit(f"The answer is not valid JSON ({error}); raw text saved to {args.out}.raw. {note}")
        problems = schema_errors(parsed, schema)
        if problems:
            if args.out:
                with open(args.out + ".raw", "w", encoding="utf-8") as handle:
                    handle.write(answer)
            sys.exit(f"The answer doesn't match the schema ({len(problems)} problems, first: "
                     f"{'; '.join(problems[:5])}); raw text saved to {args.out}.raw. {note}")
        parsed["_meta"] = {"model": model, "provider": provider, "usage": usage,
                           "seconds": round(time.time() - started)}
        answer = json.dumps(parsed, indent=1, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(answer)
        print(note)
    else:
        print(answer)
        print("\n---\n" + note, file=sys.stderr)


if __name__ == "__main__":
    main()
