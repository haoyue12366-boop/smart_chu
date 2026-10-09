"""无推断地导入 CSV，保留物理行、编号片段及字节哈希。"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path

from pydantic import ValidationError

from app.domain.ids import RecipeId
from app.domain.source_document import SourceDocument, SourceField, SourceStep

HEADERS = ("菜谱id", "名称", "食材清单", "烹饪步骤")
STEP_MARKER = re.compile(r"第(\d+)步(?=[:：【])")


class SourceImportError(ValueError):
    """来源格式、编码或身份损坏，不返回部分导入结果。"""


def read_source(path: Path) -> tuple[str, str, str]:
    """识别项目已有的两种文本编码，哈希始终绑定未经转码的原字节。"""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    encodings = ("utf-8-sig",) if raw.startswith(b"\xef\xbb\xbf") else ("utf-8", "gb18030")
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding, digest
        except UnicodeDecodeError:
            continue
    raise SourceImportError(f"{path.name}：无法按 UTF-8 或 GB18030 严格解码")


def _steps(text: str) -> tuple[SourceStep, ...]:
    markers = list(STEP_MARKER.finditer(text))
    if [int(marker[1]) for marker in markers] != list(range(1, len(markers) + 1)):
        raise ValueError("原始步骤编号不连续或重复")
    return tuple(
        SourceStep(
            number=int(marker[1]),
            start_offset=marker.start(),
            end_offset=markers[index + 1].start() if index + 1 < len(markers) else len(text),
            text=text[
                marker.start() : markers[index + 1].start()
                if index + 1 < len(markers)
                else len(text)
            ],
        )
        for index, marker in enumerate(markers)
    )


def import_recipes(path: Path) -> tuple[SourceDocument, ...]:
    """导入来源记录，拒绝重复 ID、损坏列及空记录，不解析工艺参数。"""
    text, encoding, digest = read_source(path)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records: list[SourceDocument] = []
    identifiers: set[str] = set()
    try:
        headers = next(reader, [])
        if len(headers) != len(HEADERS) or set(headers) != set(HEADERS):
            raise ValueError("表头必须恰好包含菜谱id、名称、食材清单、烹饪步骤")
        previous_end = reader.line_num
        for row_number, row in enumerate(reader, 1):
            line_start = previous_end + 1
            previous_end = reader.line_num
            if len(row) != len(headers):
                raise ValueError("字段数量不匹配或出现空记录")
            fields = dict(zip(headers, row, strict=True))
            identifier = fields["菜谱id"]
            if identifier in identifiers:
                raise ValueError(f"重复菜谱 ID：{identifier}")
            recipe = SourceDocument(
                recipe_id=RecipeId(identifier),
                name=fields["名称"],
                ingredients_text=fields["食材清单"],
                steps_text=fields["烹饪步骤"],
                source_file=path.name,
                source_sha256=digest,
                encoding=encoding,
                row_number=row_number,
                line_start=line_start,
                line_end=reader.line_num,
                raw_record=tuple(SourceField(name=k, value=v) for k, v in fields.items()),
                steps=_steps(fields["烹饪步骤"]),
            )
            records.append(recipe)
            identifiers.add(identifier)
        if not records:
            raise ValueError("菜谱记录为空")
    except (csv.Error, ValueError, ValidationError) as exc:
        raise SourceImportError(f"{path.name} 第 {reader.line_num} 行：{exc}") from exc
    return tuple(records)
