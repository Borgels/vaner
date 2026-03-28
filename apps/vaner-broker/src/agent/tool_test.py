from langchain_core.tools import tool
from langchain_ollama import ChatOllama


@tool
def list_files(path: str = ".") -> str:
    """List files and directories in the given path."""
    import os
    return "\n".join(sorted(os.listdir(path)))

llm = ChatOllama(
    model="qwen2.5-coder:32b",
    temperature=0,
)

llm_with_tools = llm.bind_tools([list_files])

resp = llm_with_tools.invoke("List the files in the current directory.")
print(resp)
print("tool_calls:", getattr(resp, "tool_calls", None))
