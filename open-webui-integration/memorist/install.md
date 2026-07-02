# Install

1. Start Memorist Core locally.
2. Copy `filter/memorist_memory_filter.py` and the `shared/` folder into the Open WebUI Filter environment according to your Open WebUI deployment.
3. Copy `function/memorist_status_function.py` and the `shared/` folder into the Open WebUI Function environment if you want a status helper.
4. Configure environment variables if defaults are not enough.

Required default URL:

```env
MEMORIST_CORE_URL=http://localhost:8777
```

Do not configure provider API keys in Memorist integration files. Use Open WebUI Admin Settings → Connections for model providers.
