from __future__ import annotations

from typing import Any


def get_nested(record: dict[str, Any], dotted_path: str) -> Any:
    value: Any = record
    for key in dotted_path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(f"Missing field '{dotted_path}' (failed at '{key}')")
        value = value[key]
    return value


def flatten_numeric(value: Any, prefix: str = "") -> dict[str, float]:
    if isinstance(value, dict):
        result: dict[str, float] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            result.update(flatten_numeric(child, child_prefix))
        return result
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {prefix: float(value)}
    return {}


def build_text(record: dict[str, Any]) -> str:
    # Raw collection-agent records are the intended model input. Annotated
    # records remain supported for migration/debugging, but semantic output is
    # deliberately not used because it would leak annotation information.
    basic = record.get("basic", record)
    creators = record.get("creators", [])
    creator_text = "、".join(
        f"{item.get('creatorName', '')}（{item.get('creatorType', '')}）"
        for item in creators if isinstance(item, dict)
    )
    parts = [
        f"作品：{record.get('workName', basic.get('name', ''))}",
        f"原名：{record.get('originalName', '')}",
        f"类型：{basic.get('type', '')}",
        f"子类：{record.get('subcategory', '')}",
        f"国家或地区：{basic.get('countryName', '')}",
        f"年代：{basic.get('year', '')}",
        f"创作者：{creator_text}",
        f"作品介绍：{record.get('introduction', '')}",
        f"文化影响：{record.get('culturalImpact', '')}",
    ]
    return "\n".join(part for part in parts if not part.endswith("："))
