"""Chart specifications are derived from, and validated against, the actual result columns."""
CHART_TYPES = {"bar", "line", "table", "histogram"}


def build_chart(requested: dict | None, columns: list[dict], rows: list[list]) -> dict:
    names = {c["name"]: c["type"] for c in columns}
    if not rows:
        return {"type": "table", "reason": "no rows"}
    req = requested or {}
    t = req.get("type")
    if t in CHART_TYPES and t != "table":
        x, y = req.get("x"), req.get("y")
        if t == "histogram" and x in names and names[x] == "number":
            return {"type": "histogram", "x": x, "title": req.get("title")}
        if x in names and y in names and names[y] == "number" and t in ("bar", "line"):
            return {"type": t, "x": x, "y": y, "title": req.get("title")}
    if t == "table":
        return {"type": "table"}
    # fallback heuristic (also used when the requested spec referenced unknown columns)
    nums = [c["name"] for c in columns if c["type"] == "number"]
    cats = [c["name"] for c in columns if c["type"] in ("text", "boolean")]
    dates = [c["name"] for c in columns if c["type"] == "date"]
    if dates and nums:
        return {"type": "line", "x": dates[0], "y": nums[0], "title": None, "note": "auto-selected"}
    if cats and nums and len(rows) <= 50:
        return {"type": "bar", "x": cats[0], "y": nums[0], "title": None, "note": "auto-selected"}
    return {"type": "table"}
