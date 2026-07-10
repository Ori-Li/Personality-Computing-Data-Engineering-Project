import re


# ==============================
# 需要删除的名字集合
# ==============================

REMOVE_NAMES = [
   
]


# ==============================
# 指定需要修改的父集合文件
# ==============================

TARGET_FILE = "./characterNameListFinalList/03_中国_音乐领域_人物.md"



# ==============================
# 删除逻辑
# ==============================

def remove_names_from_file(file_path, remove_names):

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()


    original_content = content


    for name in remove_names:

        # 匹配:
        #
        # "name",
        #
        # "name"
        #
        pattern = (
            r'^[ \t]*["\']'
            + re.escape(name)
            + r'["\']\s*,?\s*$'
        )

        content = re.sub(
            pattern,
            "",
            content,
            flags=re.MULTILINE
        )


    # 清理连续空行
    content = re.sub(
        r'\n{3,}',
        "\n\n",
        content
    )


    if content != original_content:

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True


    return False



# ==============================
# Main
# ==============================

if __name__ == "__main__":

    print("开始清洗:", TARGET_FILE)


    result = remove_names_from_file(
        TARGET_FILE,
        REMOVE_NAMES
    )


    if result:

        print("✅ 删除完成，文件已更新")

    else:

        print("⚠️ 没有找到需要删除的名字")