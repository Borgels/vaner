---
name: install
description: Install the Vaner CLI by invoking the canonical install.sh with the user's explicit consent. Use when the user asks to install Vaner or when the SessionStart hook reports that `vaner` is not on PATH.
---

When the user invokes `/vaner:install`:

1. Confirm they want to install. Explain that this will download and execute the official installer from `https://vaner.ai/install.sh`. The Bash tool will prompt for its own permission before running; describe what will happen first.
2. Run:

   ```
   curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes
   ```

3. After the install completes, verify success with `command -v vaner` and `vaner --version`.
4. Advise the user to restart Claude Code so the bundled Vaner MCP server is registered for new sessions.

Do not run this skill automatically. It performs a network fetch and executes the downloaded script, so it requires an explicit user request — either a direct `/vaner:install` invocation or a clear "install Vaner" instruction.
