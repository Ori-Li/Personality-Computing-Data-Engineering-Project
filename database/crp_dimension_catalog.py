from __future__ import annotations


LEAF_ZH = {
"passion":"激情","healing":"治愈感","awe":"敬畏感","nostalgia":"怀旧感","reflection":"反思","romance":"浪漫感","tension":"紧张感","warmth":"温暖感","loneliness":"孤独感","hope":"希望感",
"abstractness":"抽象程度","complexity":"复杂程度","depth_of_thought":"思想深度","logical_structure":"逻辑结构","novelty":"新颖程度","exploration":"探索程度","ambiguity":"多义程度","predictability":"可预测性","information_density":"信息密度","learning_value":"学习价值",
"personal_scale":"个人尺度","relationship_scale":"关系尺度","social_scale":"社会尺度","civilization_scale":"文明尺度","cosmic_scale":"宇宙尺度","time_span":"时间跨度","fantasy_level":"幻想程度",
"happiness":"快乐","sadness":"悲伤","fear":"恐惧","melancholy":"忧郁","anger":"愤怒","peace":"平静",
"beauty":"美感","minimalism":"极简程度","luxury":"华丽程度","darkness":"暗黑程度","brightness":"明亮程度","dreamlike":"梦幻程度","mystery":"神秘程度","experimental":"实验程度","classical":"古典程度","modern":"现代程度",
"plot_complexity":"情节复杂度","character_depth":"人物深度","character_growth":"人物成长","world_building":"世界构建","nonlinear_structure":"非线性结构","slow_immersion":"缓慢沉浸","action_intensity":"动作强度","conflict_intensity":"冲突强度","philosophical_depth":"哲学深度",
"individualism":"个人主义","collectivism":"集体主义","family":"家庭关系","friendship":"友谊","love":"爱情","community":"共同体","competition":"竞争","cooperation":"合作","power_relationship":"权力关系","identity":"身份认同",
"freedom":"自由","order":"秩序","truth":"真理","achievement":"成就","justice":"正义","sacrifice":"牺牲","self_exploration":"自我探索","tradition":"传统","change":"变化","survival":"生存","meaning":"意义",
"curiosity":"好奇","creation":"创造","adventure":"冒险","social":"社交","discipline":"自律","risk":"风险倾向","calm":"沉静","escape":"逃离",
"visual_intensity":"视觉强度","color_richness":"色彩丰富度","sound_intensity":"声音强度","rhythm_energy":"节奏能量","atmosphere":"氛围强度","immersion":"沉浸程度",
"cinematic_scale":"电影规模感","visual_storytelling":"视觉叙事","soundtrack_importance":"配乐重要性","actor_expression":"演员表现","director_style":"导演风格",
"episode_dependency":"剧集依赖度","long_term_character_growth":"长期人物成长","relationship_complexity":"关系复杂度","world_expansion":"世界扩展","season_progression":"季度推进",
"animation_quality":"动画表现质量","character_design":"角色设计","visual_style":"视觉风格","symbolic_expression":"象征表达","emotional_expression":"情绪表达",
"panel_expression":"分镜表达","drawing_style":"绘画风格","visual_symbolism":"视觉象征","reading_pace":"阅读节奏","author_style":"作者风格",
"literary_depth":"文学深度","writing_style":"写作风格","narrative_voice":"叙述声音","symbolism":"象征程度","inner_monologue":"内心独白","world_building_depth":"世界构建深度",
"strategy_depth":"策略深度","mechanical_skill":"操作技巧要求","player_agency":"玩家自主性","exploration_depth":"探索深度","collection_system":"收集系统","progression_system":"成长系统","social_interaction":"社交互动","competition_intensity":"竞争强度",
"tempo":"速度","energy":"能量强度","color_expression":"色彩表达","composition_complexity":"构图复杂度","historical_style":"历史风格",
"spatial_experience":"空间体验","functional_design":"功能设计","symbolic_meaning":"象征意义","scale_sense":"尺度感","human_interaction":"人与空间互动","structural_logic":"结构逻辑",
"reality_capture":"现实捕捉","human_focus":"人物关注","visual_composition":"视觉构图","documentary_value":"纪实价值","artistic_expression":"艺术表达","moment_sensitivity":"瞬间敏感度",
"live_presence":"现场在场感","body_expression":"身体表达","audience_interaction":"观众互动","performance_intensity":"表演强度","ritual_sense":"仪式感",
"three_dimensionality":"三维性","material_expression":"材料表达","body_representation":"身体再现","spatial_presence":"空间存在感","symbolic_power":"象征力量","craftsmanship":"工艺水平",
"literary_style":"文学风格","language_artistry":"语言艺术性","rhetorical_complexity":"修辞复杂度","intellectual_depth":"思想深度","cultural_depth":"文化深度","knowledge_density":"知识密度","interpretation_openness":"阐释开放度",
"conceptual_originality":"概念原创性","logical_rigor":"逻辑严密性","systematicity":"系统性","theoretical_depth":"理论深度","explanatory_power":"解释力","practical_guidance":"实践指导性","reasoning_quality":"推理质量","paradigm_shift":"范式革新","internal_consistency":"内部一致性","conceptual_clarity":"概念清晰度",
"aesthetic_expression":"审美表达","user_centeredness":"用户中心程度","ergonomics":"人体工学","innovation":"创新程度","design_execution":"设计执行力","commercial_positioning":"商业定位","brand_identity":"品牌识别","interaction_design":"交互设计",
"generic_expression":"综合表达","cultural_expression":"文化表达","functional_focus":"功能侧重"
}

GROUP_ZH={"experience":"直接体验","psychology.cognitive":"认知维度","psychology.worldview":"世界观维度","psychology.emotion":"情绪维度","psychology.aesthetic":"审美维度","psychology.narrative":"叙事维度","psychology.social_relationship":"社会关系维度","psychology.value":"价值维度","psychology.behavior":"行为维度","psychology.sensory":"感官维度"}

def chinese_name(dimension_code: str) -> str:
    leaf=dimension_code.rsplit(".",1)[-1]
    if leaf not in LEAF_ZH: raise KeyError(f"缺少维度中文名：{dimension_code}")
    return LEAF_ZH[leaf]

def group_chinese_name(group_code: str) -> str:
    if group_code in GROUP_ZH:return GROUP_ZH[group_code]
    media=group_code.removeprefix("media.").removesuffix("_vector")
    names={"movie":"电影","tv_series":"电视剧","anime":"动画","manga":"漫画","novel":"小说","game":"游戏","music":"音乐","painting":"绘画","architecture":"建筑","photography":"摄影","stage":"舞台","sculpture":"雕塑","literature":"文学","idea":"思想理论","design":"设计","other":"其他媒介"}
    return names.get(media,group_code)+"媒介维度"
