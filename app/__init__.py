"""备忘录提醒 app 包。

目录说明（改代码时按这个找地方）：
    config.py       路径 / 配色 / 默认设置        <- 想改颜色先看这里
    database.py     SQLite 建表与增删改查
    models.py       Task / Tag 数据类
    holidays.py     中国法定节假日 + 调休表
    recurrence.py   重复规则引擎（哪天该提醒，由它算）
    reminder.py     提醒调度（什么时候弹、错过怎么补）
    stats.py        完成率 / 连续打卡 / 分类占比
    theme.py        主题(QSS 样式表)生成
    ui/             所有界面
"""

APP_NAME = "备忘录提醒"
APP_VERSION = "1.0.0"
