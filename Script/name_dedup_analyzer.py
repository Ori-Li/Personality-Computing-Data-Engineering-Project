"""
Entity Name Deduplication Analyzer
==================================
Multi-domain general-purpose tool to detect potential duplicate entries
in name lists across creative & professional fields.

Covered domains:
  - Music (音乐)        - Architecture (建筑)
  - Film & TV (影视)    - Gaming (游戏)
  - Anime (动漫)        - Painting (绘画)
  - Stage (舞台/戏剧)   - Manga (漫画)
  - Novels & Literature (小说/文学)
  - Photography (摄影)  - Sculpture (雕塑)

Detection strategies:
  - Name with/without descriptive suffix (e.g. "惘闻" vs "惘闻乐队",
    "宫崎骏" vs "宫崎骏监督", "安藤忠雄" vs "安藤忠雄建筑师")
  - Same person with different name forms (alias, full name, real name, pen name)
  - Name subset relationships (e.g. "张亚东" vs "亚东")
  - Case differences (e.g. "三无MarBlue" vs "三无Marblue")
  - English/romanized name vs native name
  - Studio / workshop / group name variations

Output format:
  [
    [{"name": "name1"}, {"name": "name2"}],
    [{"name": "name3"}, {"name": "name4"}, {"name": "name5"}]
  ]
  Each inner list is a group of names suspected to refer to the same entity.
"""

import json
import re
import os
import sys
import ast
from collections import defaultdict

# ============================================================
# DOMAIN CONFIGURATION — Expand for your dataset
# ============================================================
# Each domain defines:
#   suffixes   — role/group/label words appended to names (e.g. "乐队", "导演", "建筑师")
#   prefixes   — role/group/label words prepended to names
#   labels     — infix patterns that connect group + member names (e.g. "成员", "饰演")
#
# These are used by extract_base_name() and the suffix-detection strategy.

DOMAIN_SUFFIXES = {
    # ───────── Music (音乐) ─────────
    "music": [
        # Chinese
        "乐队", "乐团", "组合", "合唱团", "团",
        # English
        "band", "group", "ensemble", "orchestra", "chorus", "quartet", "trio",
        # Japanese
        "バンド", "グループ", "楽団",
        # Korean
        "밴드", "그룹",
    ],

    # ───────── Architecture (建筑) ─────────
    "architecture": [
        "建筑师", "设计师", "建筑事务所", "建筑师事务所",
        "建筑设计院", "设计院", "设计所", "事务所",
        "工作室", "建筑工作室", "设计工作室",
        "architect", "studio", "atelier",
    ],

    # ───────── Film & TV (影视) ─────────
    "film_tv": [
        "导演", "监制", "制片人", "制片", "编剧",
        "主演", "饰演", "配音", "旁白",
        "剧组", "摄制组", "制作组",
        "director", "producer", "screenwriter",
        "production", "studio",
    ],

    # ───────── Gaming (游戏) ─────────
    "game": [
        "游戏", "制作组", "开发组", "开发团队",
        "工作室", "游戏工作室", "制作人",
        "game", "studio", "developer", "producer",
        "開発", "スタジオ",
    ],

    # ───────── Anime (动漫) ─────────
    "anime": [
        "动画", "监督", "制作委员会", "委員会",
        "工作室", "动画工作室",
        "原作", "作画", "角色原案", "原案",
        "声优", "配音", "配音演员",
        "anime", "studio", "production committee",
    ],

    # ───────── Painting (绘画) ─────────
    "painting": [
        "画家", "画师", "绘画", "画室", "画派",
        "painter", "artist", "studio", "atelier",
    ],

    # ───────── Stage (舞台/戏剧/舞蹈) ─────────
    "stage": [
        "剧团", "舞团", "戏剧", "剧社", "剧团",
        "舞台", "舞剧", "歌剧", "音乐剧", "话剧",
        "演员", "舞者", "戏剧家",
        "theatre", "troupe", "company",
    ],

    # ───────── Manga (漫画) ─────────
    "manga": [
        "漫画家", "原作", "作画", "漫画",
        "manga", "artist", "comic",
    ],

    # ───────── Novels & Literature (小说/文学) ─────────
    "literature": [
        "作家", "作者", "著", "译", "编译",
        "文学", "小说", "诗人", "散文家",
        "writer", "author", "novelist", "poet",
    ],

    # ───────── Photography (摄影) ─────────
    "photography": [
        "摄影师", "摄影家", "摄影",
        "photographer",
    ],

    # ───────── Sculpture (雕塑) ─────────
    "sculpture": [
        "雕塑家", "雕塑师", "雕塑",
        "sculptor",
    ],
}

# ── Inter-domain group/company suffixes that appear across fields ──
GENERIC_GROUP_SUFFIXES = [
    "工作室", "工坊", "社", "公司", "株式会社",
    "事务所", "协会", "学会", "学院", "研究院",
    "studio", "workshop", "company", "inc", "corp", "llc",
    "group", "team", "collective",
]

# ── Infix patterns that connect a group name to an individual ──
# e.g. "INTO1成员刘宇" → strip "成员" → compare with "刘宇"
#      "饰演孙悟空" → strip "饰演" → compare with actor name
MEMBER_LABEL_PATTERNS = [
    # Chinese
    r"成员", r"团员", r"前成员", r"出道",
    r"饰演", r"扮演", r"配音", r"出演",
    r"原作", r"作画", r"原案",
    r"著", r"译", r"编译", r"编", r"绘", r"摄影",
    # English
    r"member\s+of\s+", r"feat\.?\s*", r"featuring\s+",
    r"as\s+", r"voiced\s+",
    # Japanese
    r"\d+期生", r"メンバー",
    # Collaboration markers
    r"\s*[xX×Ｘ]\s*", r"\s*f(ea)?t\.?\s*",
]

# ============================================================
# COMBINED SUFFIX LIST (all domains)
# ============================================================
# Used by suffix-detection strategies; sorted by length (longest first)
ALL_SUFFIXES = []
for domain_suffixes in DOMAIN_SUFFIXES.values():
    ALL_SUFFIXES.extend(domain_suffixes)
ALL_SUFFIXES.extend(GENERIC_GROUP_SUFFIXES)
# Deduplicate while preserving order
_SEEN = set()
COMBINED_SUFFIXES = []
for s in ALL_SUFFIXES:
    if s not in _SEEN:
        _SEEN.add(s)
        COMBINED_SUFFIXES.append(s)
COMBINED_SUFFIXES.sort(key=len, reverse=True)


# ============================================================
# NORMALIZATION FUNCTIONS
# ============================================================

def strip_tail(name, suffixes):
    """Remove known descriptive suffixes from the tail of a name."""
    stripped = name.strip()
    for suffix in sorted(suffixes, key=len, reverse=True):
        idx = stripped.lower().rfind(suffix.lower())
        if idx == len(stripped) - len(suffix):
            core = stripped[:idx].strip()
            if core and len(core) >= 1:
                return core
    return stripped


def strip_head(name, prefixes):
    """Remove known prefixes from the head of a name."""
    stripped = name.strip()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if stripped.lower().startswith(prefix.lower()):
            core = stripped[len(prefix):].strip()
            if core and len(core) >= 1:
                return core
    return stripped


def normalize_whitespace(name):
    """Collapse multiple spaces/whitespace into single space."""
    return re.sub(r"\s+", " ", name).strip()


def strip_non_alphanum(name):
    """Remove everything except letters, numbers, and CJK chars."""
    return re.sub(
        r"[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]",
        "", name,
    )


def strip_english(name):
    """Remove ASCII letters/numbers for CJK-only comparison."""
    return re.sub(r"[a-zA-Z0-9._\-\s]+", "", name).strip()


def extract_base_name(name):
    """
    Extract the base/core name by removing labels, suffixes, and normalizing.
    Used as the grouping key for suffix-based dedup detection.
    """
    n = name.strip()

    # Remove member-label infix patterns
    for pattern in MEMBER_LABEL_PATTERNS:
        n = re.sub(pattern, "", n).strip()

    # Remove tail suffixes (e.g. 乐队, 导演, 建筑师, 工作室)
    n = strip_tail(n, COMBINED_SUFFIXES)

    # Remove head prefixes (e.g. "member of ")
    n = strip_head(n, [])

    return normalize_whitespace(n)


# ============================================================
# DETECTION STRATEGIES
# ============================================================

def find_subset_duplicates(names):
    """
    Find names where one is a substring/subset of another.
    E.g. "张亚东" vs "亚东", "宫崎骏" vs "宫崎骏监督"
    """
    groups = []
    seen = set()

    for i in range(len(names)):
        if i in seen:
            continue
        group = [i]
        n1 = normalize_whitespace(names[i])

        for j in range(i + 1, len(names)):
            if j in seen:
                continue
            n2 = normalize_whitespace(names[j])

            shorter, longer = (n1.lower(), n2.lower()) if len(n1) <= len(n2) else (n2.lower(), n1.lower())

            # Minimum length threshold to avoid false positives
            if len(strip_non_alphanum(shorter)) >= 2:
                if shorter in longer and len(longer) >= len(shorter) + 1:
                    group.append(j)

        if len(group) >= 2:
            for idx in group:
                seen.add(idx)
            groups.append(group)

    return groups


def find_suffix_duplicates(names):
    """
    Find names that are the same except for a descriptive suffix.
    E.g. "惘闻" vs "惘闻乐队", "宫崎骏" vs "宫崎骏监督",
          "安藤忠雄" vs "安藤忠雄建筑师"
    """
    base_map = defaultdict(list)

    for i, name in enumerate(names):
        base = extract_base_name(name)
        if base:
            base_map[base].append((i, name))

    groups = []
    for base, entries in base_map.items():
        if len(entries) < 2:
            continue
        normalized_set = set()
        unique_entries = []
        for idx, nm in entries:
            key = strip_non_alphanum(nm.lower())
            if key not in normalized_set:
                normalized_set.add(key)
                unique_entries.append((idx, nm))
        if len(unique_entries) >= 2:
            groups.append([idx for idx, _ in unique_entries])

    return groups


def find_case_duplicates(names):
    """
    Find names that differ only by case.
    E.g. "三无MarBlue" vs "三无Marblue"
    """
    case_map = defaultdict(list)
    for i, name in enumerate(names):
        key = name.lower()
        case_map[key].append((i, name))

    groups = []
    for key, entries in case_map.items():
        unique_names = set(e[1] for e in entries)
        if len(unique_names) >= 2:
            groups.append([e[0] for e in entries])
    return groups


def find_mixed_name_duplicates(names):
    """
    Find pairs where one name is an English/romanized version
    and another is a Chinese/native name for the same entity.
    E.g. "GAI" vs "GAI周延", "F.I.R.飞儿乐团" vs "飞儿乐团"
    """
    groups = []
    seen = set()

    for i in range(len(names)):
        if i in seen:
            continue
        n1 = names[i]
        cn1 = strip_english(n1)
        en1 = re.sub(r"[\u4e00-\u9fff]", "", n1).strip()

        if not cn1 or not en1:
            continue

        group = [i]
        for j in range(len(names)):
            if i == j or j in seen:
                continue
            n2 = names[j]
            cn2 = strip_english(n2)
            en2 = re.sub(r"[\u4e00-\u9fff]", "", n2).strip()

            # Same Chinese core, one has extra English
            if cn1 and cn2 and len(cn1) >= 2 and cn1 == cn2:
                group.append(j)
            # English part of A == Chinese-only form of B
            elif en1 and cn2 and len(en1) >= 3 and en1.lower() == cn2.strip().lower():
                group.append(j)
            elif cn1 and en2 and len(cn2) >= 3 and cn1.strip().lower() == en2.lower():
                group.append(j)

        if len(group) >= 2:
            for idx in group:
                seen.add(idx)
            groups.append(group)

    return groups


# ============================================================
# MAIN ANALYZER
# ============================================================

def analyze_duplicates(names, config=None):
    """
    Run all detection strategies and compile deduplication groups.

    Args:
        names: List of name strings
        config: Optional dict; if provided, used to override global settings

    Returns:
        list: List of groups, each group is a list of {"name": name} dicts
    """
    _ = config  # reserved for future per-call overrides

    # Run all strategies
    strategy_results = [
        find_suffix_duplicates(names),
        find_case_duplicates(names),
        find_subset_duplicates(names),
        find_mixed_name_duplicates(names),
    ]

    # Collect all groups (index-lists)
    all_groups = []
    for groups in strategy_results:
        all_groups.extend(g for g in groups if len(g) >= 2)

    # Merge overlapping groups via union-find
    merged = _merge_groups(all_groups)

    # Convert to output format
    output = []
    for group in merged:
        if len(group) >= 2:
            output.append([{"name": names[i]} for i in sorted(set(group))])

    return output


def _merge_groups(groups):
    """Merge overlapping groups using union-find."""
    parent = {}

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for group in groups:
        if not group:
            continue
        first = group[0]
        for item in group:
            if item not in parent:
                parent[item] = item
            union(first, item)

    root_map = defaultdict(set)
    for item in parent:
        root_map[find(item)].add(item)

    return [sorted(items) for items in root_map.values()]


# ============================================================
# INPUT LOADER
# ============================================================

def load_names_from_file(filepath):
    """
    Load names from a file, supporting multiple formats:
      - JSON array:           ["name1", "name2", ...]
      - Python-style list:    ["name1", "name2", ...]
      - One name per line (plain text)
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read().strip()

    # --- Strategy 1: JSON ---
    if raw.startswith("[") and raw.endswith("]"):
        try:
            names = json.loads(raw)
            if isinstance(names, list):
                return names
        except json.JSONDecodeError:
            pass

        # --- Strategy 2: ast.literal_eval (handles single/double quotes) ---
        try:
            names = ast.literal_eval(raw)
            if isinstance(names, list):
                return names
        except (ValueError, SyntaxError):
            pass

        # --- Strategy 3: regex extraction of quoted strings ---
        inner = raw[1:raw.rfind("]")] if "]" in raw else raw[1:]
        matches = re.findall(r"'([^']*)'|\"([^\"]*)\"", inner)
        if matches:
            names = [m[0] if m[0] else m[1] for m in matches]
            if names:
                return names

    # --- Strategy 4: one name per line ---
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if lines and not raw.startswith("["):
        return lines

    raise ValueError(f"Could not parse input file: {filepath}")


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    """
    Usage:
        python name_dedup_analyzer.py <input_file> [output_file.json]

    Supported input formats:
      - JSON array:           ["name1", "name2", ...]
      - Python-style list:    ["name1", "name2", ...]
      - One name per line (plain text)

    Example output:
      [
        [{"name": "惘闻"}, {"name": "惘闻乐队"}],
        [{"name": "三无MarBlue"}, {"name": "三无Marblue"}],
        [{"name": "GAI"}, {"name": "GAI周延"}]
      ]
    """
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        print("\nUsage:", file=sys.stderr)
        print("  python name_dedup_analyzer.py input.txt [output.json]", file=sys.stderr)
        print("\nInput formats:", file=sys.stderr)
        print("  - JSON:           [\"name1\", \"name2\", ...]", file=sys.stderr)
        print('  - Python list:    ["name1", "name2", ...]', file=sys.stderr)
        print("  - Plain text:     one name per line", file=sys.stderr)
        sys.exit(1)

    input_path = sys.argv[1]

    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        names = load_names_from_file(input_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(names, list):
        print("Error: Could not parse input as a list of names.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(names)} names from {input_path}", file=sys.stderr)

    result = analyze_duplicates(names)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        total_duplicates = sum(len(g) for g in result)
        print(f"Found {len(result)} duplicate groups ({total_duplicates} total entries).", file=sys.stderr)
        print(f"Output written to: {output_path}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
