import os
import glob as glob_mod

from src.types.tools import Tool


def execute(pattern: str, path: str = "", **kwargs) -> dict:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_dir = path or project_root
    search_dir = os.path.abspath(os.path.expanduser(search_dir))

    if not os.path.isdir(search_dir):
        return {
            "title": pattern,
            "output": f"Path is not a directory: {search_dir}",
            "metadata": {"error": True},
        }

    full_pattern = os.path.join(search_dir, pattern)
    matches = sorted(glob_mod.glob(full_pattern, recursive=True))
    matches = [m for m in matches if os.path.isfile(m) or os.path.isdir(m)]

    limit = 100
    truncated = len(matches) > limit
    selected = matches[:limit]

    output_lines = []
    if not selected:
        output_lines.append("No files found")
    else:
        for m in selected:
            output_lines.append(m)
        if truncated:
            output_lines.append("")
            output_lines.append(
                f"(Results are truncated: showing first {limit} results. "
                "Consider using a more specific path or pattern.)"
            )

    return {
        "title": pattern,
        "output": "\n".join(output_lines),
        "metadata": {"count": len(selected), "truncated": truncated},
    }


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]
    return data.get("output", "")


tool = Tool(
    name="glob",
    description="""- Fast file pattern matching (glob) for any codebase size
- Supports patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths
- Use for finding files by name patterns
- For open-ended multi-round searches, use Task tool instead""",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. If not specified, the current working directory will be used.",
            },
        },
        "required": ["pattern"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
