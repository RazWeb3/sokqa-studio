def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        return "1.0.1"
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        return "1.0.1"
    return f"{major}.{minor}.{patch + 1}"
