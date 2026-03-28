import ollama

tools = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories in the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list",
                        "default": "."
                    }
                },
                "required": []
            },
        },
    }
]

response = ollama.chat(
    model="qwen2.5-coder:32b",
    messages=[
        {"role": "user", "content": "List the files in the current directory."}
    ],
    tools=tools,
)

print(response)
print()
print("message:", response["message"])
print("tool_calls:", response["message"].get("tool_calls"))
