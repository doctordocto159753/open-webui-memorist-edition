import html


def escape_memory_text(text: str) -> str:
    escaped = html.escape(text, quote=True)
    return escaped.replace("</memory_context>", "&lt;/memory_context&gt;")
