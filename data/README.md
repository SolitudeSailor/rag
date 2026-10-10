# 原始数据

本目录仅存放公开数据集输入，不保存实验回答或成绩。

`NQ-open.dev.jsonl` 是保留下来的官方 NQ-open 开发集缓存，避免重复下载。来源：
https://raw.githubusercontent.com/google-research-datasets/natural-questions/master/nq_open/NQ-open.dev.jsonl

批量脚本固定使用 `random.seed(42)` 抽取 30 题，样本和数据集 SHA256 写入新实验目录。数据集缓存不提交 Git；缺失时脚本自动下载。
