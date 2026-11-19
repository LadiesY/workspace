# -*- coding: utf-8 -*-
"""知识库：资料抽取、切块、索引、存储。

对外只有三层：

    store.add(paths)      导入资料（复制副本 → 抽取 → 切块 → 重建索引）
    store.load()          读整库
    search.Index(chunks)  检索

抽取器是零依赖的（pdftext / docxtext），因为现场机器装不了第三方包。
"""
