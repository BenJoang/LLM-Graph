import asyncio
import unittest

from src.tools.create_tool import create_tool_py


class CreateToolTemplateTests(unittest.IsolatedAsyncioTestCase):
    def load_template(self, execution: str, destructive: bool = False) -> dict:
        source = create_tool_py(
            "example_tool",
            destructive=destructive,
            execution=execution,
        )
        compile(source, f"<generated-{execution}-tool>", "exec")
        namespace: dict = {
            "__file__": f"C:/generated/{execution}/tool.py",
            "__name__": f"generated_{execution}_tool",
        }
        exec(source, namespace)
        return namespace

    async def test_sync_template_exposes_acall(self):
        tool = self.load_template("sync")

        result = await tool["acall"](placeholder="value")

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["input"]["placeholder"], "value")

    async def test_async_template_uses_acall_as_primary_entry(self):
        tool = self.load_template("async", destructive=True)

        result = await tool["acall"](placeholder="value")

        self.assertTrue(result["ok"])
        self.assertFalse(tool["IS_READ_ONLY"])
        self.assertTrue(tool["IS_DESTRUCTIVE"])

        with self.assertRaisesRegex(RuntimeError, "await acall"):
            tool["call"](placeholder="value")

    def test_invalid_execution_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "execution"):
            create_tool_py("bad_tool", execution="worker")


if __name__ == "__main__":
    unittest.main()
