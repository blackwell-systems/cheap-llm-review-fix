import unittest

from ask_model import answer_object, schema_errors

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["name", "kind", "code"],
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "code": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "count": {"type": "integer"},
    },
}


class SchemaErrorsTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(schema_errors({"name": "x", "kind": "a", "code": ""}, SCHEMA), [])

    def test_missing_required(self):
        errors = schema_errors({"name": "x", "kind": "a"}, SCHEMA)
        self.assertEqual(errors, ["$: missing 'code'"])

    def test_wrong_types(self):
        errors = schema_errors({"name": 3, "kind": "a", "code": "", "tags": "no", "count": True}, SCHEMA)
        self.assertTrue(any("$.name: expected string" in e for e in errors))
        self.assertTrue(any("$.tags: expected array" in e for e in errors))
        self.assertTrue(any("$.count: expected an integer" in e for e in errors))

    def test_array_items(self):
        errors = schema_errors({"name": "x", "kind": "a", "code": "", "tags": ["ok", 5]}, SCHEMA)
        self.assertEqual(len(errors), 1)
        self.assertIn("$.tags[1]", errors[0])

    def test_enum(self):
        errors = schema_errors({"name": "x", "kind": "z", "code": ""}, SCHEMA)
        self.assertEqual(len(errors), 1)
        self.assertIn("is not one of", errors[0])

    def test_additional_properties_but_underscore_keys_allowed(self):
        base = {"name": "x", "kind": "a", "code": ""}
        self.assertEqual(schema_errors(dict(base, extra=1), SCHEMA), ["$: unexpected 'extra'"])
        self.assertEqual(schema_errors(dict(base, _meta={"model": "m"}), SCHEMA), [])


class AnswerObjectTest(unittest.TestCase):
    def test_prose_around_the_answer(self):
        text = 'Here you go:\n{"name": "x", "kind": "a", "code": "1"}\nHope that helps.'
        value, error = answer_object(text, SCHEMA)
        self.assertEqual(error, "")
        self.assertEqual(value["name"], "x")

    def test_schema_echoed_before_the_answer(self):
        text = ('{"type": "object", "properties": {"name": {"type": "string"}}}\n'
                'Answer:\n{"name": "real", "kind": "b", "code": ""}')
        value, error = answer_object(text, SCHEMA)
        self.assertEqual(error, "")
        self.assertEqual(value["name"], "real")

    def test_unescaped_quotes_in_code_are_repaired(self):
        text = '{"name": "x", "kind": "a", "code": "print("hi")"}'
        value, error = answer_object(text, SCHEMA)
        self.assertEqual(error, "")
        self.assertEqual(value["code"], 'print("hi")')

    def test_raw_newlines_in_strings_accepted(self):
        value, error = answer_object('{"name": "x", "kind": "a", "code": "a\nb"}', SCHEMA)
        self.assertEqual(error, "")
        self.assertEqual(value["code"], "a\nb")

    def test_missing_comma_is_not_repaired(self):
        # The quote repair may mangle it, but the result never passes the schema,
        # so ask_model.py reports an error instead of using it.
        value, error = answer_object('{"name": "x" "kind": "a", "code": ""}', SCHEMA)
        self.assertTrue(value is None or schema_errors(value, SCHEMA))
        value, error = answer_object('{"name": "x", "kind": "a" "code": ""}', SCHEMA)
        self.assertTrue(value is None or schema_errors(value, SCHEMA))

    def test_no_object(self):
        value, error = answer_object("no json here", SCHEMA)
        self.assertIsNone(value)
        self.assertEqual(error, "no JSON object found")


if __name__ == "__main__":
    unittest.main()


class SystemMessageTest(unittest.TestCase):
    def test_no_file_gives_no_system_message(self):
        from ask_model import system_message
        self.assertEqual(system_message(None), [])

    def test_a_file_becomes_the_system_message(self):
        import tempfile
        from ask_model import system_message
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("You fix one bug.")
        try:
            self.assertEqual(system_message(handle.name), [{"role": "system", "content": "You fix one bug."}])
        finally:
            os.remove(handle.name)
