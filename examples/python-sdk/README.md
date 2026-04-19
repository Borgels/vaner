# Python SDK Integration

Embed Vaner directly in custom tools and pipelines.

```python
from vaner import api

# Optional one-shot prep
api.prepare(".")

# Query context package
package = api.query("how is auth enforced?", ".", top_n=8, client_id="ci-bot")
print(package.injected_context)
print(package.cache_tier, package.token_used, package.token_budget)
```

Inspect per-client context decisions:

```python
print(api.inspect_last(".", client_id="ci-bot"))
```
