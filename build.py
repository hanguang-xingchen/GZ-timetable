#!/usr/bin/env python3
"""把 config.json 与 events.jsonl 渲染进 index.html 模板，生成 dist/index.html。

零 JS 架构：格子、月份、图例、统计、记录列表全部在构建时写成静态 markup，
页面不含任何脚本。模板里用 <!--build:NAME--> 成对注释标记待填区域。

只依赖 Python 标准库。校验失败时一次性列出全部错误并以退出码 1 结束——
GitHub Actions 因此在部署前就失败，线上页面保持上一个可用版本。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DAYS_PER_WEEK = 7

CONFIG_KEYS = {
    "title", "weekStart", "showGlyph", "types",
    "windowStart", "windowEnd", "countdownLabel",
}
TYPE_KEYS = {"id", "name", "color", "glyph"}
RECORD_KEYS = {"date", "type", "note"}

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

WEEKDAY_LONG = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
WEEKDAY_CHAR = ["日", "一", "二", "三", "四", "五", "六"]

REGIONS = [
    "heading", "subtitle", "headline", "stats",
    "months", "weekdays", "cells", "legend", "notes", "footer", "vars",
]

# <title> 是 escapable raw text，里面的注释标记会被当成正文显示在标签栏，
# 所以标题不用区域标记，改为整体替换 <title> 标签的内容
TITLE_RE = re.compile(r"(<title>).*?(</title>)", re.DOTALL)


class ValidationError(Exception):
    """一批校验错误。errors 是可直接打印的字符串列表。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors))
        self.errors = errors


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError([f"{path}: 文件不存在"]) from None
    except OSError as exc:
        raise ValidationError([f"{path}: 读取失败（{exc}）"]) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            [f"{path}: JSON 解析失败（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"]
        ) from None


def parse_iso_date(raw: object) -> date | None:
    """把 YYYY-MM-DD 字符串解析成 date；格式不对或日期不存在时返回 None。"""
    if not isinstance(raw, str) or not DATE_RE.match(raw):
        return None
    year, month, day = (int(part) for part in DATE_RE.match(raw).groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def week_offset(day: date, week_start: int) -> int:
    """day 距离其所在周第一天的天数。week_start: 0=周日起始，1=周一起始。"""
    dow_sun = (day.weekday() + 1) % DAYS_PER_WEEK
    return (dow_sun - week_start) % DAYS_PER_WEEK


def load_config(path: Path) -> dict:
    """校验并规范化 config.json。"""
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise ValidationError([f"{path}: 顶层必须是 JSON 对象"])

    errors: list[str] = []
    for key in raw:
        if key not in CONFIG_KEYS:
            errors.append(
                f"{path}: 未知字段 {key!r}（可用：{' / '.join(sorted(CONFIG_KEYS))}）"
            )

    title = raw.get("title", "时间墙")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{path}: title 必须是非空字符串，当前为 {title!r}")

    week_start = raw.get("weekStart", 1)
    # True == 1，所以要先排除布尔值，否则 weekStart: true 会被当成合法
    if isinstance(week_start, bool) or week_start not in (0, 1):
        errors.append(
            f"{path}: weekStart 只能是 0（周日起始）或 1（周一起始），当前为 {week_start!r}"
        )

    show_glyph = raw.get("showGlyph", False)
    if not isinstance(show_glyph, bool):
        errors.append(f"{path}: showGlyph 必须是布尔值，当前为 {show_glyph!r}")

    # 起点向外取整到所在周的第一天（墙的左边缘总是完整周）；
    # 终点是硬边界：最后一列只画到这一天为止，考完那几天不该被墙吞掉也不该多画
    window_start = parse_iso_date(raw.get("windowStart"))
    window_end = parse_iso_date(raw.get("windowEnd"))
    for key, value, parsed in (
        ("windowStart", raw.get("windowStart"), window_start),
        ("windowEnd", raw.get("windowEnd"), window_end),
    ):
        if parsed is None:
            errors.append(
                f"{path}: {key} 必须是 YYYY-MM-DD 形式的真实日期，当前为 {value!r}"
            )
    if window_start is not None and not isinstance(week_start, bool) and week_start in (0, 1):
        window_start -= timedelta(days=week_offset(window_start, week_start))
    if window_start is not None and window_end is not None and window_end < window_start:
        errors.append(
            f"{path}: windowEnd（{window_end}）不能早于 windowStart（{window_start}）"
        )

    countdown_label = raw.get("countdownLabel")
    if countdown_label is not None and (
        not isinstance(countdown_label, str) or not countdown_label.strip()
    ):
        errors.append(
            f"{path}: countdownLabel 必须是非空字符串（不需要倒计时请省略该字段），"
            f"当前为 {countdown_label!r}"
        )
        countdown_label = None

    raw_types = raw.get("types")
    if not isinstance(raw_types, list) or not raw_types:
        errors.append(f"{path}: types 必须是非空数组")
        raw_types = []

    seen_ids: set[str] = set()
    types: list[dict] = []
    for index, item in enumerate(raw_types):
        where = f"{path}: types[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: 必须是 JSON 对象")
            continue
        for key in item:
            if key not in TYPE_KEYS:
                errors.append(
                    f"{where}: 未知字段 {key!r}（可用：{' / '.join(sorted(TYPE_KEYS))}）"
                )

        type_id = item.get("id")
        if not isinstance(type_id, str) or not ID_RE.match(type_id):
            errors.append(
                f"{where}: id 必须是小写字母开头的 [a-z0-9_-] 字符串，当前为 {type_id!r}"
            )
            type_id = None
        elif type_id in seen_ids:
            errors.append(f"{where}: id {type_id!r} 重复")
            type_id = None
        else:
            seen_ids.add(type_id)

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: name 必须是非空字符串，当前为 {name!r}")

        color = item.get("color")
        if not isinstance(color, str) or not COLOR_RE.match(color):
            errors.append(f"{where}: color 必须是 #RRGGBB 形式，当前为 {color!r}")

        glyph = item.get("glyph")
        if glyph is not None and (not isinstance(glyph, str) or len(glyph) != 1):
            errors.append(f"{where}: glyph 必须是单个字符，当前为 {glyph!r}")

        if type_id is not None and isinstance(name, str) and isinstance(color, str):
            entry: dict = {"id": type_id, "name": name, "color": color}
            if isinstance(glyph, str) and len(glyph) == 1:
                entry["glyph"] = glyph
            types.append(entry)

    if errors:
        raise ValidationError(errors)

    return {
        "title": title,
        "weekStart": week_start,
        "showGlyph": show_glyph,
        "windowStart": window_start,
        "windowEnd": window_end,
        "countdownLabel": countdown_label,
        "types": types,
    }


def load_events(
    path: Path,
    valid_ids: set[str],
    allow_duplicate: str | None,
    window: tuple[date, date] | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """逐行校验 events.jsonl，返回 (按日期排序的 days, warnings)。

    任何一行不合法都会记入错误并在最后一起抛出——绝不静默跳过，
    否则墙上少一格你永远不会发现。window 给出时，窗口外的日期同样算错：
    它进不了格子，却照样出现在备注列表和页脚计数里，三处数字会互相打架。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError([f"{path}: 文件不存在"]) from None
    except OSError as exc:
        raise ValidationError([f"{path}: 读取失败（{exc}）"]) from None

    errors: list[str] = []
    warnings: list[str] = []
    records: dict[str, dict] = {}

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # 空行与 # 注释行允许存在

        where = f"{path}:{lineno}"
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: JSON 解析失败：{exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"{where}: 每行必须是一个 JSON 对象")
            continue

        for key in record:
            if key not in RECORD_KEYS:
                errors.append(
                    f"{where}: 未知字段 {key!r}（可用：date / type / note）"
                )

        raw_date = record.get("date")
        if not isinstance(raw_date, str) or not DATE_RE.match(raw_date):
            errors.append(f"{where}: date 必须是 YYYY-MM-DD 字符串，当前为 {raw_date!r}")
            continue
        year, month, day = (int(part) for part in DATE_RE.match(raw_date).groups())
        try:
            parsed_date = date(year, month, day)
        except ValueError as exc:
            errors.append(f"{where}: date {raw_date!r} 不是有效日期（{exc}）")
            continue
        if window is not None and not window[0] <= parsed_date <= window[1]:
            errors.append(
                f"{where}: date {raw_date} 不在窗口 "
                f"{window[0].isoformat()} ~ {window[1].isoformat()} 内，墙上画不出这一格"
                "（要收录它就改 config.json 的 windowStart / windowEnd）"
            )
            continue

        type_id = record.get("type")
        if type_id not in valid_ids:
            errors.append(
                f"{where}: type {type_id!r} 不在 config.json 的 types 中"
                f"（可用：{' / '.join(sorted(valid_ids))}）"
            )
            continue

        note = record.get("note")
        if note is not None:
            if not isinstance(note, str):
                errors.append(f"{where}: note 必须是字符串，当前为 {note!r}")
                continue
            if not note.strip():
                errors.append(f"{where}: note 是空字符串，选填字段请直接省略")
                continue

        if raw_date in records:
            if allow_duplicate == "last":
                warnings.append(f"{where}: date {raw_date} 重复，已用本行覆盖之前的记录")
            else:
                errors.append(
                    f"{where}: date {raw_date} 重复"
                    "（如需以最后一行为准，加 --allow-duplicate last）"
                )
                continue

        entry: dict = {"type": type_id}
        if note is not None:
            entry["note"] = note
        records[raw_date] = entry

    if errors:
        raise ValidationError(errors)

    days = {key: records[key] for key in sorted(records)}
    return days, warnings


def build_timestamp() -> str:
    """SOURCE_DATE_EPOCH 存在时用它，以便产出可复现的构建（测试与 CI 缓存）。"""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            moment = datetime.now(tz=timezone.utc)
    else:
        moment = datetime.now(tz=timezone.utc)
    return moment.astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 渲染


def window_dates(start: date, end: date, week_start: int) -> list[list[date | None]]:
    """返回 7 行 × N 列的日期矩阵（按行输出 DOM），N 由起止日期决定。

    起点向外取整到所在周的第一天，终点是硬边界：最后一列里晚于 end 的位置填 None，
    渲染时直接跳过，所以墙会精确停在那一天（最后一列可能不满 7 格）。
    """
    first = start - timedelta(days=week_offset(start, week_start))
    last_week = end - timedelta(days=week_offset(end, week_start))
    weeks = (last_week - first).days // DAYS_PER_WEEK + 1
    return [
        [
            d if d <= end else None
            for d in (
                first + timedelta(days=col * DAYS_PER_WEEK + row) for col in range(weeks)
            )
        ]
        for row in range(DAYS_PER_WEEK)
    ]


def drawn_days(matrix: list[list[date | None]]) -> list[date]:
    """矩阵里真正会画出来的日期，按行优先顺序。"""
    return [d for row in matrix for d in row if d is not None]


def date_label(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日 · {WEEKDAY_LONG[(d.weekday() + 1) % 7]}"


def aria_label(d: date, type_def: dict | None, record: dict | None, future: bool) -> str:
    base = date_label(d)
    if type_def is None:
        return base + ("，尚未到来" if future else "，未记录")
    text = f"{base}，{type_def['name']}"
    if record and record.get("note"):
        text += f"：{record['note']}"
    return text


def tip_html(d: date, type_def: dict, record: dict) -> str:
    parts = [f'<span class="tip-date">{esc(date_label(d))}</span>']
    parts.append(
        f'<span class="tip-type"><i style="background:{type_def["color"]}"></i>'
        f'{esc(type_def["name"])}</span>'
    )
    if record.get("note"):
        parts.append(f'<span class="tip-note">{esc(record["note"])}</span>')
    return '<span class="tip">' + "".join(parts) + "</span>"


def render_cells(
    matrix: list[list[date | None]],
    days: dict[str, dict],
    type_by_id: dict[str, dict],
    today: date,
    show_glyph: bool,
) -> str:
    rows = []
    for row_dates in matrix:
        cells = []
        for d in row_dates:
            if d is None:
                continue  # 窗口终点之后的位置，不画格子
            record = days.get(d.isoformat())
            type_def = type_by_id.get(record["type"]) if record else None
            classes = ["cell"]
            style = ""
            if type_def:
                style = f' style="background:{type_def["color"]}"'
            else:
                classes.append("cell--empty")
            future = d > today
            if future:
                classes.append("cell--future")
            inner = ""
            if type_def and show_glyph and type_def.get("glyph"):
                inner += f'<span class="glyph">{esc(type_def["glyph"])}</span>'
            if type_def:
                inner += tip_html(d, type_def, record)
            cells.append(
                f'<div class="{" ".join(classes)}"{style} role="gridcell" '
                f'aria-label="{esc(aria_label(d, type_def, record, future))}">{inner}</div>'
            )
        rows.append(f'<div class="row" role="row">{"".join(cells)}</div>')
    return "".join(rows)


def render_months(matrix: list[list[date | None]]) -> str:
    labels = []
    last_col = -99
    for col in range(len(matrix[0])):
        for row in range(DAYS_PER_WEEK):
            d = matrix[row][col]
            if d is not None and d.day == 1:
                if col - last_col >= 3:
                    labels.append(f'<span style="grid-column:{col + 1}">{d.month}月</span>')
                    last_col = col
                break
    return "".join(labels)


def render_weekdays(week_start: int) -> str:
    out = []
    for row in range(DAYS_PER_WEEK):
        wd = (week_start + row) % DAYS_PER_WEEK
        out.append(f"<div>{WEEKDAY_CHAR[wd] if wd in (1, 3, 5) else ''}</div>")
    return "".join(out)


def render_legend(types: list[dict]) -> str:
    items = ['<span class="legend-item"><span class="swatch swatch--empty"></span>未记录</span>']
    for t in types:
        items.append(
            f'<span class="legend-item"><span class="swatch" '
            f'style="background:{t["color"]}"></span>{esc(t["name"])}</span>'
        )
    return "".join(items)


def render_vars(weeks: int) -> str:
    """列数写成一个独立的 <style> 元素，靠源码顺序覆盖主样式块里的 --cols。

    不能直接把区域标记塞进主 <style>：那是 raw text，<!--...--> 不会被当注释，
    会变成非法 CSS（和 <title> 同一个坑）。
    """
    return f"<style>:root{{--cols:{weeks}}}</style>"


def render_stats(
    matrix: list[list[date | None]],
    days: dict[str, dict],
    types: list[dict],
    today: date,
    countdown_label: str | None,
) -> tuple[str, str]:
    drawn = drawn_days(matrix)
    counts = {t["id"]: 0 for t in types}
    recorded = 0
    remaining = 0
    for d in drawn:
        if d > today:
            remaining += 1
        record = days.get(d.isoformat())
        if record and record["type"] in counts:
            counts[record["type"]] += 1
            recorded += 1
    weeks = len(matrix[0])
    ranked = sorted(types, key=lambda t: counts[t["id"]], reverse=True)
    top = ranked[0] if ranked else None
    max_count = counts[top["id"]] if top else 0

    if recorded == 0:
        headline = f"这 {weeks} 周还没有任何记录。"
    else:
        headline = (
            f"已记录 <b>{recorded}</b> / {len(drawn)} 天，其中 "
            f"<b>{esc(top['name'])} {counts[top['id']]} 天</b> 最多。"
        )
    if countdown_label and remaining > 0:
        headline += f"距离{esc(countdown_label)}还有 <b>{remaining}</b> 天。"

    bars = []
    for t in ranked:
        n = counts[t["id"]]
        pct = (n / max_count * 100) if max_count else 0
        share = round(n / recorded * 100) if recorded else 0
        bars.append(
            f'<div class="bar-row"><span class="bar-name">{esc(t["name"])}</span>'
            f'<div class="bar-track"><span class="bar-fill" '
            f'style="background:{t["color"]};width:{pct:.1f}%"></span></div>'
            f'<span class="bar-num">{n} 天 · {share}%</span></div>'
        )
    return headline, "".join(bars)


def render_notes(days: dict[str, dict], type_by_id: dict[str, dict]) -> str:
    noted = [(key, rec) for key, rec in sorted(days.items(), reverse=True) if rec.get("note")]
    if not noted:
        return '<li class="n-none">还没有带备注的记录。</li>'
    items = []
    for key, rec in noted:
        year, month, day = (int(p) for p in key.split("-"))
        d = date(year, month, day)
        t = type_by_id.get(rec["type"])
        color = t["color"] if t else "transparent"
        name = t["name"] if t else rec["type"]
        items.append(
            f'<li><span class="n-date">{key} · {WEEKDAY_LONG[(d.weekday() + 1) % 7]}</span>'
            f'<span class="n-type"><i style="background:{color}"></i>{esc(name)}</span>'
            f'<span class="n-note">{esc(rec["note"])}</span></li>'
        )
    return "".join(items)


def fill_regions(template: str, values: dict[str, str], template_path: Path) -> str:
    """替换模板里成对的 <!--build:NAME--> 标记之间的内容。幂等，可反复执行。"""
    out = template
    for name in REGIONS:
        pattern = re.compile(rf"(<!--build:{name}-->).*?(<!--/build:{name}-->)", re.DOTALL)
        new, count = pattern.subn(
            lambda m, v=values[name]: m.group(1) + v + m.group(2), out, count=1
        )
        if count != 1:
            raise ValidationError(
                [f"{template_path}: 缺少成对区域标记 <!--build:{name}--> … <!--/build:{name}-->"]
            )
        out = new

    new, count = TITLE_RE.subn(
        lambda m: m.group(1) + values["heading"] + m.group(2), out, count=1
    )
    if count != 1:
        raise ValidationError([f"{template_path}: 缺少 <title> 标签"])
    out = new

    if 'data-state="unbuilt"' in out:
        out = out.replace('data-state="unbuilt"', 'data-state="built"', 1)
    elif 'data-state="built"' not in out:
        raise ValidationError([f'{template_path}: 缺少 body 上的 data-state 标记'])
    return out


def summarize(
    days: dict[str, dict], types: list[dict], matrix: list[list[date | None]]
) -> list[str]:
    drawn = drawn_days(matrix)
    # 行优先顺序下 drawn[-1] 不是最晚一天（末列不满 7 格时后面几行仍会补进更早的列）
    lines = [
        f"✓ 窗口 {drawn[0].isoformat()} → {max(drawn).isoformat()}"
        f"（{len(matrix[0])} 周，{len(drawn)} 格）"
    ]
    if not days:
        lines.append("✓ 0 条记录（events.jsonl 为空）")
        return lines
    dates = sorted(days)
    counts = Counter(entry["type"] for entry in days.values())
    names = {t["id"]: t["name"] for t in types}
    breakdown = " · ".join(
        f"{names.get(type_id, type_id)} {count}"
        for type_id, count in counts.most_common()
    )
    lines.append(f"✓ {len(days)} 条记录，{dates[0]} → {dates[-1]}")
    lines.append(f"  {breakdown}")
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 config.json 与 events.jsonl 渲染进 index.html，生成 dist/index.html",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--events", type=Path, default=ROOT / "events.jsonl")
    parser.add_argument("--template", type=Path, default=ROOT / "index.html")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "index.html")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验数据文件，不生成任何输出（给 CI 当门禁用）",
    )
    parser.add_argument(
        "--allow-duplicate",
        choices=["last"],
        default=None,
        metavar="last",
        help="同一 date 出现多次时以最后一行为准（默认报错）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        valid_ids = {t["id"] for t in config["types"]}
        matrix = window_dates(
            config["windowStart"], config["windowEnd"], config["weekStart"]
        )
        # 记录校验的边界与「画出来的范围」严格一致：左到取整后的周首，右到 windowEnd 当天
        window = (matrix[0][0], config["windowEnd"])
        days, warnings = load_events(
            args.events, valid_ids, args.allow_duplicate, window=window
        )
    except ValidationError as exc:
        for message in exc.errors:
            print(f"✗ {message}", file=sys.stderr)
        return 1

    for message in warnings:
        print(f"⚠ {message}", file=sys.stderr)

    if args.check:
        for line in summarize(days, config["types"], matrix):
            print(line)
        return 0

    type_by_id = {t["id"]: t for t in config["types"]}
    today = date.today()
    weeks = len(matrix[0])
    headline, stats = render_stats(
        matrix, days, config["types"], today, config["countdownLabel"]
    )
    start = matrix[0][0]
    end = config["windowEnd"]

    values = {
        "heading": esc(config["title"]),
        "subtitle": esc(
            f"{start.isoformat()} → {end.isoformat()} · 共 {weeks} 周 · "
            f"周{'一' if config['weekStart'] == 1 else '日'}起始"
        ),
        "headline": headline,
        "stats": stats,
        "months": render_months(matrix),
        "weekdays": render_weekdays(config["weekStart"]),
        "cells": render_cells(matrix, days, type_by_id, today, config["showGlyph"]),
        "legend": render_legend(config["types"]),
        "notes": render_notes(days, type_by_id),
        "vars": render_vars(weeks),
        "footer": esc(
            f"共 {len(days)} 条记录 · 数据生成于 {build_timestamp()} · 源自 events.jsonl"
        ),
    }

    try:
        template = args.template.read_text(encoding="utf-8")
        html_out = fill_regions(template, values, args.template)
    except ValidationError as exc:
        for message in exc.errors:
            print(f"✗ {message}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"✗ {args.template}: 读取失败（{exc}）", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out, encoding="utf-8")
    # 保险：确保 Pages 不会尝试用 Jekyll 处理产物
    (args.out.parent / ".nojekyll").write_text("", encoding="utf-8")

    for line in summarize(days, config["types"], matrix):
        print(line)
    size_kb = args.out.stat().st_size / 1024
    print(f"  → {args.out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
