# name_deduplicate.py

import re
import sys


def clean_names(file_path):

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取所有双引号中的名字
    names = re.findall(r'"([^"]+)"', content)

    if not names:
        print("没有找到名字")
        return

    seen = set()
    result = []

    for name in names:

        name = name.strip()

        if name and name not in seen:
            seen.add(name)
            result.append(name)

        else:
            print("即将删除:", name)   

    # 覆盖写入
    with open(file_path, "w", encoding="utf-8") as f:

        f.write("[\n")

        for i, name in enumerate(result):

            comma = "," if i != len(result)-1 else ""

            f.write(f'    "{name}"{comma}\n')

           

        f.write("]\n")


    print("处理完成")
    print("原数量:", len(names))
    print("去重后:", len(result))
    print("删除:", len(names)-len(result))


if __name__ == "__main__":

    fileName = './characterNameListFinalList/03_中国_音乐领域_人物.md'

    with open("./characterNameListFinalList/03_中国_音乐领域_人物.md", "r", encoding="utf-8-sig") as f:
        content = f.read()

      

    clean_names(fileName)