from __future__ import annotations

import argparse
from pathlib import Path

from database.crp_dataset_pipeline import EXPERIENCE_KEYS, PSYCHOLOGY_GROUPS, SCHEMA_VERSION
from database.crp_dimension_catalog import chinese_name, group_chinese_name
from database.generate_missing_crp import MEDIA_KEYS


ALL_MEDIA_KEYS = {
    **MEDIA_KEYS,
    "music_vector": ["tempo", "energy"],
    "idea_vector": ["conceptual_originality","logical_rigor","systematicity","theoretical_depth","explanatory_power","practical_guidance","reasoning_quality","paradigm_shift","internal_consistency","conceptual_clarity"],
    "design_vector": ["aesthetic_expression","functional_design","user_centeredness","ergonomics","innovation","design_execution","commercial_positioning","brand_identity","interaction_design"],
    "other_vector": ["generic_expression","cultural_expression","functional_focus","knowledge_density"],
}


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def definitions():
    order=0
    for key in EXPERIENCE_KEYS:
        order+=1; yield f"experience.{key}","experience",order
    for group,keys in PSYCHOLOGY_GROUPS.items():
        for key in keys:
            order+=1; yield f"psychology.{group}.{key}",f"psychology.{group}",order
    for vector,keys in ALL_MEDIA_KEYS.items():
        group=f"media.{vector}"
        for key in keys:
            order+=1; yield f"{group}.{key}",group,order


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    rows=[]
    for code,group,order in definitions():
        description=f"{group_chinese_name(group)}：由 CPR-1.2 Prompt 定义的归一化维度"
        rows.append(f"({quote(code)},{quote(group)},{quote(chinese_name(code))},{quote(code.rsplit('.',1)[-1])},{quote(description)},{quote(SCHEMA_VERSION)},{order},TRUE)")
    sql="""-- CPR-1.2 维度本体静态种子。维度代码是机器契约，name_zh 是前端展示中文名。\nINSERT INTO `t_crp_dimension_definition`\n    (`dimension_code`,`group_code`,`name_zh`,`name_en`,`description`,`schema_version`,`display_order`,`is_active`)\nVALUES\n"""+",\n".join(rows)+"\nON DUPLICATE KEY UPDATE\n `group_code`=VALUES(`group_code`),`name_zh`=VALUES(`name_zh`),`name_en`=VALUES(`name_en`),`description`=VALUES(`description`),`display_order`=VALUES(`display_order`),`is_active`=TRUE;\n"
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(sql,encoding="utf-8");print(f"generated {len(rows)} definitions: {a.output}")

if __name__=="__main__":main()
