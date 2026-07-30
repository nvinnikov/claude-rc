def chunks(text: str, limit: int = 3800) -> list[str]:
    """Режет текст под лимит Telegram, по возможности по границе строки."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut])
        rest = rest[cut:]
        if rest.startswith("\n"):
            rest = rest[1:]
    if rest:
        parts.append(rest)
    return parts
