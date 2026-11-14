#!/usr/bin/env python3
"""build.py 的回归测试。运行：python3 -m unittest test_build -v"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import build

TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><title>时间墙</title>
<!--build:vars--><style>:root{--cols:52}</style><!--/build:vars-->
</head>
<body data-state="unbuilt">
<h1><!--build:heading--><!--/build:heading--></h1>
<p><!--build:date-range--><!--/build:date-range--> · 共 <!--build:n-weeks--><!--/build:n-weeks--> 周 · 周<!--build:week-start-name--><!--/build:week-start-name-->起始</p>
<p>已记录 <b><!--build:n-recorded--><!--/build:n-recorded--></b> / <!--build:n-total--><!--/build:n-total--> 天<!--build:top-clause--><!--/build:top-clause-->。<!--build:countdown--><!--/build:countdown--></p>
<div><!--build:stats--><!--/build:stats--></div>
<div><!--build:months--><!--/build:months--></div>
<div><!--build:weekdays--><!--/build:weekdays--></div>
<div><!--build:cells--><!--/build:cells--></div>
<div><span class="legend-item"><span class="swatch swatch--empty"></span>未记录</span><!--build:legend-types--><!--/build:legend-types--></div>
<ul><!--build:notes--><!--/build:notes--></ul>
<p>共 <!--build:n-records--><!--/build:n-records--> 条记录 · 数据生成于 <!--build:build-time--><!--/build:build-time--> · 源自 events.jsonl</p>
</body></html>
"""

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)

# 窗口跟着 TODAY 走，测试才不会过几个月自己失效
WINDOW_START = TODAY - timedelta(days=7 * 8)
WINDOW_END = TODAY + timedelta(days=7 * 3)

CONFIG = {
    "title": "测试墙",
    "weekStart": 1,
    "showGlyph": False,
    "windowStart": WINDOW_START.isoformat(),
    "windowEnd": WINDOW_END.isoformat(),
    "countdownLabel": "高考结束",
    "types": [
        {"id": "school", "name": "正常上学", "color": "#6e7681"},
        {"id": "vacation", "name": "休假", "color": "#2ea043"},
    ],
}

VALID_IDS = {"school", "vacation"}

# 期望值一律从矩阵推：起点取整到周首，终点是硬边界（最后一列可能不满 7 格）
MATRIX = build.window_dates(WINDOW_START, WINDOW_END, 1)
WEEKS = len(MATRIX[0])
CELLS = len(build.drawn_days(MATRIX))
BOUNDS = (MATRIX[0][0], WINDOW_END)

# windowStart / windowEnd 是必填项，手搓最小 config 时得带上
WINDOW_FIELDS = {
    "windowStart": WINDOW_START.isoformat(),
    "windowEnd": WINDOW_END.isoformat(),
}


def cell_count(html_text: str) -> int:
    return len(re.findall(r'role="gridcell"', html_text))


def filled_cells(html_text: str) -> list[str]:
    return re.findall(r'<div class="cell" style="background:(#[0-9a-fA-F]{6})"', html_text)


class TempCase(unittest.TestCase):
    """提供临时目录与写文件的小工具。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name: str, content: str | dict | list) -> Path:
        path = self.tmp / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return path

    def write_config(self, config: dict | None = None) -> Path:
        return self.write("config.json", config if config is not None else CONFIG)

    def write_events(self, lines: list[str] | str) -> Path:
        content = lines if isinstance(lines, str) else "\n".join(lines) + "\n"
        return self.write("events.jsonl", content)

    def assertErrors(self, errors: list[str], *needles: str) -> None:
        joined = "\n".join(errors)
        for needle in needles:
            self.assertIn(needle, joined)


class TestConfig(TempCase):
    def test_valid_config_is_normalized(self) -> None:
        path = self.write_config()
        config = build.load_config(path)
        self.assertEqual(config["title"], "测试墙")
        self.assertEqual(config["weekStart"], 1)
        self.assertEqual(config["showGlyph"], False)
        self.assertEqual([t["id"] for t in config["types"]], ["school", "vacation"])
        # 起点取整到周首；终点是硬边界，原样保留
        self.assertEqual(config["windowStart"], BOUNDS[0])
        self.assertEqual(config["windowEnd"], WINDOW_END)
        self.assertEqual(config["countdownLabel"], "高考结束")

    def test_defaults_applied_when_optional_fields_missing(self) -> None:
        path = self.write_config(
            {**WINDOW_FIELDS, "types": [{"id": "a", "name": "A", "color": "#000000"}]}
        )
        config = build.load_config(path)
        self.assertEqual(config["title"], "时间墙")
        self.assertEqual(config["weekStart"], 1)
        self.assertEqual(config["showGlyph"], False)
        self.assertIsNone(config["countdownLabel"])

    def test_glyph_kept_only_when_single_char(self) -> None:
        path = self.write_config(
            {
                **WINDOW_FIELDS,
                "types": [{"id": "a", "name": "A", "color": "#000000", "glyph": "★"}],
            }
        )
        self.assertEqual(build.load_config(path)["types"][0]["glyph"], "★")

    def test_start_snaps_to_week_start_but_end_stays_hard(self) -> None:
        # 起点 2027-06-09（周三）取整到周一；终点不扩周，原样停在那天
        path = self.write_config(
            dict(CONFIG, windowStart="2027-06-09", windowEnd="2027-06-09")
        )
        config = build.load_config(path)
        self.assertEqual(config["windowStart"], date(2027, 6, 7))
        self.assertEqual(config["windowEnd"], date(2027, 6, 9))

    def test_countdown_label_optional_and_must_be_nonempty_string(self) -> None:
        without = {k: v for k, v in CONFIG.items() if k != "countdownLabel"}
        self.assertIsNone(build.load_config(self.write_config(without))["countdownLabel"])
        for bad in ["", "   ", 5, True]:
            with self.assertRaises(build.ValidationError, msg=f"label={bad!r}") as ctx:
                build.load_config(self.write_config(dict(CONFIG, countdownLabel=bad)))
            self.assertErrors(ctx.exception.errors, "countdownLabel")

    def test_missing_window_rejected(self) -> None:
        for key in ("windowStart", "windowEnd"):
            bad = {k: v for k, v in CONFIG.items() if k != key}
            with self.assertRaises(build.ValidationError, msg=key) as ctx:
                build.load_config(self.write_config(bad))
            self.assertErrors(ctx.exception.errors, key, "YYYY-MM-DD")

    def test_malformed_window_rejected(self) -> None:
        for value in ["2027-6-13", "2027/06/13", "20270613", "2027-02-30", None, 20270613]:
            bad = dict(CONFIG, windowEnd=value)
            with self.assertRaises(build.ValidationError, msg=f"windowEnd={value!r}") as ctx:
                build.load_config(self.write_config(bad))
            self.assertErrors(ctx.exception.errors, "windowEnd")

    def test_window_end_before_start_rejected(self) -> None:
        bad = dict(CONFIG, windowStart="2027-06-13", windowEnd="2026-11-02")
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(self.write_config(bad))
        self.assertErrors(ctx.exception.errors, "不能早于")

    def test_unknown_field_rejected(self) -> None:
        bad = dict(CONFIG, colour="#ffffff")
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(self.write_config(bad))
        self.assertErrors(ctx.exception.errors, "未知字段", "colour")

    def test_duplicate_type_id_rejected(self) -> None:
        bad = dict(
            CONFIG,
            types=[
                {"id": "school", "name": "上学", "color": "#6e7681"},
                {"id": "school", "name": "又上学", "color": "#2ea043"},
            ],
        )
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(self.write_config(bad))
        self.assertErrors(ctx.exception.errors, "重复")

    def test_bad_color_rejected(self) -> None:
        for color in ["6e7681", "#6e768", "#gggggg", "", None]:
            bad = {"types": [{"id": "a", "name": "A", "color": color}]}
            with self.assertRaises(build.ValidationError, msg=f"color={color!r}"):
                build.load_config(self.write_config(bad))

    def test_bad_week_start_rejected(self) -> None:
        for week_start in [2, -1, "1", True]:
            bad = dict(CONFIG, weekStart=week_start)
            with self.assertRaises(build.ValidationError, msg=f"weekStart={week_start!r}"):
                build.load_config(self.write_config(bad))

    def test_empty_types_rejected(self) -> None:
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(self.write_config(dict(CONFIG, types=[])))
        self.assertErrors(ctx.exception.errors, "types 必须是非空数组")

    def test_missing_file_rejected(self) -> None:
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(self.tmp / "nope.json")
        self.assertErrors(ctx.exception.errors, "文件不存在")

    def test_broken_json_reports_position(self) -> None:
        path = self.write("config.json", '{"types": [}')
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_config(path)
        self.assertErrors(ctx.exception.errors, "JSON 解析失败", "第 1 行")


class TestEvents(TempCase):
    def test_valid_records_sorted_by_date(self) -> None:
        path = self.write_events(
            [
                '{"date": "2026-11-13", "type": "school", "note": "普通一天"}',
                '{"date": "2026-01-05", "type": "vacation"}',
            ]
        )
        days, warnings = build.load_events(path, VALID_IDS, None)
        self.assertEqual(list(days), ["2026-01-05", "2026-11-13"])
        self.assertEqual(days["2026-11-13"], {"type": "school", "note": "普通一天"})
        self.assertEqual(days["2026-01-05"], {"type": "vacation"})
        self.assertEqual(warnings, [])

    def test_blank_and_comment_lines_skipped(self) -> None:
        path = self.write_events(
            [
                "# 这是注释",
                "",
                '{"date": "2026-11-13", "type": "school"}',
                "   ",
            ]
        )
        days, _ = build.load_events(path, VALID_IDS, None)
        self.assertEqual(len(days), 1)

    def test_broken_json_reports_line_number(self) -> None:
        path = self.write_events(
            ['{"date": "2026-11-13", "type": "school"}', '{"date": oops}']
        )
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, ":2:", "JSON 解析失败")

    def test_unknown_type_rejected(self) -> None:
        path = self.write_events(['{"date": "2026-11-13", "type": "schol"}'])
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, ":1:", "schol", "不在 config.json")

    def test_impossible_date_rejected(self) -> None:
        for bad_date in ["2026-02-30", "2026-13-01", "2026-00-10", "2026-04-31"]:
            path = self.write_events(
                [json.dumps({"date": bad_date, "type": "school"})]
            )
            with self.assertRaises(build.ValidationError, msg=bad_date):
                build.load_events(path, VALID_IDS, None)

    def test_malformed_date_rejected(self) -> None:
        for bad_date in ["2026-11-1", "26-11-13", "2026/11/13", "20261113", None, 20261113]:
            path = self.write_events(
                [json.dumps({"date": bad_date, "type": "school"})]
            )
            with self.assertRaises(build.ValidationError, msg=f"date={bad_date!r}"):
                build.load_events(path, VALID_IDS, None)

    def test_leap_day_accepted(self) -> None:
        path = self.write_events(['{"date": "2028-02-29", "type": "school"}'])
        days, _ = build.load_events(path, VALID_IDS, None)
        self.assertIn("2028-02-29", days)

    def test_unknown_field_rejected(self) -> None:
        path = self.write_events(
            ['{"date": "2026-11-13", "type": "school", "notes": "拼错了"}']
        )
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, ":1:", "未知字段", "notes")

    def test_empty_note_rejected(self) -> None:
        path = self.write_events('{"date": "2026-11-13", "type": "school", "note": "  "}')
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, "note 是空字符串")

    def test_duplicate_date_rejected_by_default(self) -> None:
        path = self.write_events(
            [
                '{"date": "2026-11-13", "type": "school"}',
                '{"date": "2026-11-13", "type": "vacation"}',
            ]
        )
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, ":2:", "重复", "--allow-duplicate last")

    def test_duplicate_date_last_wins_when_allowed(self) -> None:
        path = self.write_events(
            [
                '{"date": "2026-11-13", "type": "school"}',
                '{"date": "2026-11-13", "type": "vacation", "note": "改主意了"}',
            ]
        )
        days, warnings = build.load_events(path, VALID_IDS, "last")
        self.assertEqual(days["2026-11-13"], {"type": "vacation", "note": "改主意了"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("重复", warnings[0])

    def test_all_errors_reported_at_once(self) -> None:
        path = self.write_events(
            [
                '{"date": "2026-02-30", "type": "school"}',
                '{"date": "2026-11-13", "type": "nope"}',
                "not json at all",
            ]
        )
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(path, VALID_IDS, None)
        self.assertEqual(len(ctx.exception.errors), 3)

    def test_missing_file_rejected(self) -> None:
        with self.assertRaises(build.ValidationError) as ctx:
            build.load_events(self.tmp / "nope.jsonl", VALID_IDS, None)
        self.assertErrors(ctx.exception.errors, "文件不存在")

    def test_record_outside_window_rejected(self) -> None:
        for edge in (BOUNDS[0] - timedelta(days=1), BOUNDS[1] + timedelta(days=1)):
            path = self.write_events(
                [json.dumps({"date": edge.isoformat(), "type": "school"})]
            )
            with self.assertRaises(
                build.ValidationError, msg=edge.isoformat()
            ) as ctx:
                build.load_events(path, VALID_IDS, None, window=BOUNDS)
            self.assertErrors(
                ctx.exception.errors, edge.isoformat(), "不在窗口", "windowStart"
            )

    def test_record_on_window_edges_accepted(self) -> None:
        path = self.write_events(
            [
                json.dumps({"date": BOUNDS[0].isoformat(), "type": "school"}),
                json.dumps({"date": BOUNDS[1].isoformat(), "type": "vacation"}),
            ]
        )
        days, warnings = build.load_events(path, VALID_IDS, None, window=BOUNDS)
        self.assertEqual(len(days), 2)
        self.assertEqual(warnings, [])

    def test_window_check_skipped_when_window_is_none(self) -> None:
        far = (BOUNDS[1] + timedelta(days=400)).isoformat()
        path = self.write_events([json.dumps({"date": far, "type": "school"})])
        days, _ = build.load_events(path, VALID_IDS, None)
        self.assertEqual(list(days), [far])


class TestRender(TempCase):
    """静态渲染：格子/标签/列表在构建时写死，页面不含脚本。"""

    def build_html(self, days: dict | None = None) -> str:
        config = build.load_config(self.write_config())
        records = days if days is not None else {}
        type_by_id = {t["id"]: t for t in config["types"]}
        matrix = build.window_dates(
            config["windowStart"], config["windowEnd"], config["weekStart"]
        )
        stats_parts, stats = build.render_stats(
            matrix, records, config["types"], TODAY, config["countdownLabel"]
        )
        values = {
            "heading": build.esc(config["title"]),
            "date-range": "日期区间",
            "n-weeks": "周数",
            "week-start-name": "一",
            **stats_parts,
            "stats": stats,
            "months": build.render_months(matrix),
            "weekdays": build.render_weekdays(config["weekStart"]),
            "cells": build.render_cells(matrix, records, type_by_id, TODAY, config["showGlyph"]),
            "legend-types": build.render_legend(config["types"]),
            "notes": build.render_notes(records, type_by_id),
            "n-records": "记录数",
            "build-time": "生成时间",
            "vars": build.render_vars(len(matrix[0])),
        }
        return build.fill_regions(TEMPLATE, values, Path("index.html"))

    def test_grid_covers_whole_window_as_static_cells(self) -> None:
        html_text = self.build_html()
        self.assertEqual(cell_count(html_text), CELLS)
        self.assertEqual(html_text.count('class="row" role="row"'), 7)

    def test_cols_var_matches_window_width(self) -> None:
        html_text = self.build_html()
        self.assertIn(f"<style>:root{{--cols:{WEEKS}}}</style>", html_text)

    def test_recorded_days_render_with_type_color(self) -> None:
        html_text = self.build_html(
            {
                TODAY.isoformat(): {"type": "school", "note": "上课"},
                YESTERDAY.isoformat(): {"type": "vacation"},
            }
        )
        self.assertEqual(sorted(filled_cells(html_text)), sorted(["#6e7681", "#2ea043"]))
        self.assertEqual(html_text.count("cell--empty"), CELLS - 2)

    def test_no_script_tags_in_output(self) -> None:
        html_text = self.build_html({TODAY.isoformat(): {"type": "school"}})
        self.assertNotIn("<script", html_text)

    def test_body_flips_to_built(self) -> None:
        html_text = self.build_html()
        self.assertIn('data-state="built"', html_text)
        self.assertNotIn('data-state="unbuilt"', html_text)

    def test_title_region_filled(self) -> None:
        html_text = self.build_html()
        # <title> 里不能有注释标记（escapable raw text 会把它当正文显示）
        self.assertIn("<title>测试墙</title>", html_text)
        self.assertNotIn("<title><!--", html_text)
        # 页内大标题仍走区域标记，标记保留以保证幂等
        self.assertIn("<!--build:heading-->测试墙<!--/build:heading-->", html_text)

    def test_note_appears_in_tip_and_notes_list_escaped(self) -> None:
        html_text = self.build_html(
            {TODAY.isoformat(): {"type": "school", "note": '</script><b onclick="x">期中考'}}
        )
        self.assertNotIn("</script>", html_text)
        self.assertIn("&lt;/script&gt;", html_text)
        self.assertIn("&lt;b onclick=&quot;x&quot;&gt;期中考", html_text)
        # 悬窗、记录列表、aria-label 各一处，共三处
        self.assertEqual(html_text.count("&lt;/script&gt;"), 3)

    def test_future_days_marked(self) -> None:
        # 窗口终点固定在 config 里且晚于 TODAY，所以「还没到」的格子数是可以精确算出来的
        html_text = self.build_html({TODAY.isoformat(): {"type": "school"}})
        self.assertEqual(html_text.count("cell--future"), (BOUNDS[1] - TODAY).days)

    def test_fill_is_idempotent(self) -> None:
        once = self.build_html()
        twice = self.build_html()
        self.assertEqual(once, twice)
        # 对已构建的产物再填一次也不应报错（允许把 --template 指向上次产物）
        config = build.load_config(self.write_config())
        matrix = build.window_dates(
            config["windowStart"], config["windowEnd"], config["weekStart"]
        )
        values = {name: "" for name in build.REGIONS}
        values["cells"] = build.render_cells(
            matrix, {}, {t["id"]: t for t in config["types"]}, TODAY, False
        )
        refilled = build.fill_regions(once, values, Path("index.html"))
        self.assertIn('data-state="built"', refilled)

    def test_missing_region_marker_rejected(self) -> None:
        broken = TEMPLATE.replace("<!--build:cells-->", "").replace("<!--/build:cells-->", "")
        values = {name: "" for name in build.REGIONS}
        with self.assertRaises(build.ValidationError) as ctx:
            build.fill_regions(broken, values, Path("index.html"))
        self.assertErrors(ctx.exception.errors, "cells")

    def test_missing_data_state_rejected(self) -> None:
        broken = TEMPLATE.replace('data-state="unbuilt"', "")
        values = {name: "" for name in build.REGIONS}
        with self.assertRaises(build.ValidationError) as ctx:
            build.fill_regions(broken, values, Path("index.html"))
        self.assertErrors(ctx.exception.errors, "data-state")

    def test_weekday_labels_are_mon_wed_fri_for_monday_start(self) -> None:
        self.assertEqual(
            build.render_weekdays(1),
            "<div>一</div><div></div><div>三</div><div></div><div>五</div><div></div><div></div>",
        )

    def test_window_matrix_starts_on_week_start_and_ends_on_hard_end(self) -> None:
        matrix = build.window_dates(WINDOW_START, WINDOW_END, 1)
        self.assertEqual(len(matrix), 7)
        self.assertEqual(matrix[0][0].weekday(), 0)  # 首格周一
        self.assertLessEqual(matrix[0][0], WINDOW_START)
        self.assertEqual(max(build.drawn_days(matrix)), WINDOW_END)  # 最晚一格就是终点当天
        self.assertEqual(len(matrix[0]), WEEKS)

    def test_wall_stops_exactly_on_gaokao_end_day(self) -> None:
        # 2026-11-02（周一）→ 2027-06-08（周二）：32 列，最后一列只画周一周二两格
        matrix = build.window_dates(date(2026, 11, 2), date(2027, 6, 8), 1)
        self.assertEqual(matrix[0][0], date(2026, 11, 2))
        self.assertEqual(len(matrix[0]), 32)
        self.assertEqual(matrix[0][-1], date(2027, 6, 7))
        self.assertEqual(matrix[1][-1], date(2027, 6, 8))
        self.assertIsNone(matrix[2][-1])  # 周三及之后不画格子
        self.assertEqual(len(build.drawn_days(matrix)), 219)

    def test_end_anchor_is_not_expanded_to_whole_week(self) -> None:
        matrix = build.window_dates(date(2027, 6, 8), date(2027, 6, 8), 1)
        self.assertEqual(matrix[0][0], date(2027, 6, 7))
        self.assertEqual(
            build.drawn_days(matrix), [date(2027, 6, 7), date(2027, 6, 8)]
        )

    def test_sunday_start_week_rows_run_sun_to_sat(self) -> None:
        matrix = build.window_dates(date(2027, 6, 9), date(2027, 6, 9), 0)
        self.assertEqual(matrix[0][0], date(2027, 6, 6))  # 周日
        self.assertEqual(build.drawn_days(matrix)[-1], date(2027, 6, 9))
        self.assertIsNone(matrix[4][0])  # 周四及之后不画


class TestMain(TempCase):
    def run_build(self, *extra: str) -> tuple[int, Path]:
        config = self.write_config()
        events = self.write_events(
            [
                json.dumps({"date": YESTERDAY.isoformat(), "type": "school"}),
                json.dumps({"date": TODAY.isoformat(), "type": "vacation", "note": "休息"}),
            ]
        )
        template = self.write("index.html", TEMPLATE)
        out = self.tmp / "dist" / "index.html"
        code = build.main(
            [
                "--config", str(config),
                "--events", str(events),
                "--template", str(template),
                "--out", str(out),
                *extra,
            ]
        )
        return code, out

    def test_build_writes_dist_and_nojekyll(self) -> None:
        code, out = self.run_build()
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertTrue((out.parent / ".nojekyll").exists())
        html_text = out.read_text(encoding="utf-8")
        self.assertEqual(cell_count(html_text), CELLS)
        self.assertIn(f"<style>:root{{--cols:{WEEKS}}}</style>", html_text)
        self.assertIn('data-state="built"', html_text)
        self.assertNotIn("<script", html_text)
        self.assertEqual(sorted(filled_cells(html_text)), sorted(["#6e7681", "#2ea043"]))
        self.assertIn("休息", html_text)

    def test_subtitle_spells_out_the_fixed_window(self) -> None:
        _, out = self.run_build()
        html_text = out.read_text(encoding="utf-8")
        # 骨架在模板里，值被区域标记夹着，所以按标记分段核对
        self.assertIn(
            f"<!--build:date-range-->{BOUNDS[0]} → {BOUNDS[1]}<!--/build:date-range-->",
            html_text,
        )
        self.assertIn(f"<!--build:n-weeks-->{WEEKS}<!--/build:n-weeks--> 周", html_text)
        self.assertNotIn("最近 52 周", html_text)

    def test_headline_carries_the_countdown(self) -> None:
        _, out = self.run_build()
        html_text = out.read_text(encoding="utf-8")
        self.assertIn(
            f"距离高考结束还有 <b>{(WINDOW_END - TODAY).days}</b> 天", html_text
        )

    def test_headline_drops_countdown_when_label_missing(self) -> None:
        config = {k: v for k, v in CONFIG.items() if k != "countdownLabel"}
        events = self.write_events(
            [json.dumps({"date": TODAY.isoformat(), "type": "school"})]
        )
        template = self.write("index.html", TEMPLATE)
        out = self.tmp / "dist" / "index.html"
        code = build.main(
            [
                "--config", str(self.write_config(config)),
                "--events", str(events),
                "--template", str(template),
                "--out", str(out),
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("距离", out.read_text(encoding="utf-8"))

    def test_check_does_not_write(self) -> None:
        code, out = self.run_build("--check")
        self.assertEqual(code, 0)
        self.assertFalse(out.exists())

    def test_out_of_window_record_fails_build(self) -> None:
        self.write_config()
        self.write_events(
            [json.dumps({"date": (BOUNDS[1] + timedelta(days=1)).isoformat(), "type": "school"})]
        )
        self.write("index.html", TEMPLATE)
        out = self.tmp / "dist" / "index.html"
        code = build.main(
            [
                "--config", str(self.tmp / "config.json"),
                "--events", str(self.tmp / "events.jsonl"),
                "--template", str(self.tmp / "index.html"),
                "--out", str(out),
            ]
        )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())

    def test_invalid_data_returns_nonzero_and_writes_nothing(self) -> None:
        self.write_config()
        self.write_events([json.dumps({"date": TODAY.isoformat(), "type": "nope"})])
        self.write("index.html", TEMPLATE)
        out = self.tmp / "dist" / "index.html"
        code = build.main(
            [
                "--config", str(self.tmp / "config.json"),
                "--events", str(self.tmp / "events.jsonl"),
                "--template", str(self.tmp / "index.html"),
                "--out", str(out),
            ]
        )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())

    def test_reproducible_with_source_date_epoch(self) -> None:
        os.environ["SOURCE_DATE_EPOCH"] = "1763000000"
        self.addCleanup(os.environ.pop, "SOURCE_DATE_EPOCH", None)
        _, first = self.run_build()
        first_bytes = first.read_bytes()
        _, second = self.run_build()
        self.assertEqual(first_bytes, second.read_bytes())


class TestProjectFiles(TempCase):
    """对仓库里真实的 config.json / events.jsonl / index.html 做一次冒烟测试。"""

    def test_repo_data_passes_check(self) -> None:
        if not (build.ROOT / "events.jsonl").exists():
            self.skipTest("仓库数据文件不存在")
        code = build.main(
            [
                "--config", str(build.ROOT / "config.json"),
                "--events", str(build.ROOT / "events.jsonl"),
                "--template", str(build.ROOT / "index.html"),
                "--out", str(self.tmp / "index.html"),
                "--check",
            ]
        )
        self.assertEqual(code, 0)

    def test_repo_template_builds(self) -> None:
        if not (build.ROOT / "index.html").exists():
            self.skipTest("模板不存在")
        out = self.tmp / "index.html"
        code = build.main(
            [
                "--config", str(build.ROOT / "config.json"),
                "--events", str(build.ROOT / "events.jsonl"),
                "--template", str(build.ROOT / "index.html"),
                "--out", str(out),
            ]
        )
        self.assertEqual(code, 0)
        html_text = out.read_text(encoding="utf-8")
        # 期望格数从仓库自己的 config 算出来，改窗口日期不用回来改测试
        config = build.load_config(build.ROOT / "config.json")
        matrix = build.window_dates(
            config["windowStart"], config["windowEnd"], config["weekStart"]
        )
        weeks = len(matrix[0])
        self.assertEqual(cell_count(html_text), len(build.drawn_days(matrix)))
        self.assertIn(f"<style>:root{{--cols:{weeks}}}</style>", html_text)
        self.assertIn(f"距离{config['countdownLabel']}还有", html_text)
        self.assertIn('data-state="built"', html_text)
        self.assertNotIn("<script", html_text)
        # 构建只写 --out，仓库里的模板必须保持未构建状态
        template_text = (build.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-state="unbuilt"', template_text)
        self.assertIn("<!--build:vars-->", template_text)


if __name__ == "__main__":
    unittest.main()
