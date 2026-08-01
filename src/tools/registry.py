import os
import sys
import importlib.util

from src.types.tools import Tool, ToolResult


class ToolRegistry:
    def __init__(self, truncation_dir: str = ""):
        from src.agent.truncate import TruncateService

        self._tools: dict[str, Tool] = {}
        self._truncate = TruncateService(truncation_dir)

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]):
        for t in tools:
            self.register(t)

    def load_local(self, tools_dir: str):
        if not os.path.isdir(tools_dir):
            return
        project_root = os.path.dirname(os.path.abspath(tools_dir))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        for f in sorted(os.listdir(tools_dir)):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            mod = self._import_module(os.path.join(tools_dir, f))
            if mod and hasattr(mod, "tool"):
                self.register(mod.tool)

    def _import_module(self, path: str):
        name = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    def schemas(self) -> list:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, args: dict) -> dict:
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        try:
            raw = tool.execute(**args)
        except TypeError as e:
            return {"title": name, "output": f"Tool execution error: {e}", "metadata": {"error": True}}

        if isinstance(raw, ToolResult):
            result_dict = {"title": raw.title, "output": raw.output, "metadata": raw.metadata, "attachments": raw.attachments}
        elif isinstance(raw, dict):
            result_dict = {
                "title": raw.get("title", name),
                "output": raw.get("output", str(raw)),
                "metadata": raw.get("metadata", {}),
                "attachments": raw.get("attachments", []),
            }
        else:
            result_dict = {"title": name, "output": str(raw), "metadata": {}, "attachments": []}

        truncated = self._truncate.output(result_dict["output"], max_lines=2000, max_bytes=50 * 1024)
        result_dict["output"] = truncated.content
        if truncated.truncated:
            result_dict["metadata"]["truncated"] = True
            result_dict["metadata"]["output_path"] = truncated.output_path

        if tool.to_model_output:
            result_dict["output"] = tool.to_model_output(result_dict)

        return result_dict

    def cleanup(self):
        self._truncate.cleanup()
