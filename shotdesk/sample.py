"""示例项目：三条镜头分别走自带、外部和导入。"""

SAMPLE_NAME = "雨夜送伞"
SAMPLE_LOGLINE = "巷口借伞。三条镜头用来看三种画面来源。"
SAMPLE_SCRIPT = """画面：雨夜巷口，路灯把积水照亮。
对白：伞还在店里。
角色：阿宁
运镜：缓慢推近
时长：5秒

画面：阿宁把伞撑开，雨水顺着伞骨滑下来。
对白：那你就先用我的。
角色：阿宁
运镜：停在伞面
时长：5秒

画面：两人并肩走进巷子，灯影被拉长。
对白：明天再还。
角色：阿宁、老周
运镜：跟着走
时长：5秒
"""

SAMPLE_ASSETS = [
    ("character", "阿宁", "深色外套，手里还没有伞"),
    ("character", "老周", "浅色衬衫，站在路灯下"),
    ("location", "雨夜巷口", "窄巷、积水和一盏路灯"),
]


def sample_shots() -> list[dict]:
    from shotdesk.script import parse_script

    shots = parse_script(SAMPLE_SCRIPT)
    shots[0]["source"] = "builtin"
    shots[1]["source"] = "external"
    shots[2]["source"] = "import"
    return shots
