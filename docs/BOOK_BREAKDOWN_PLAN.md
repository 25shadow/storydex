# TXT 拆书方案

## 产品目标

拆书不是摘要，而是把小说还原成可复核的结构数据：每个结论都能回到章节和原文证据，并明确区分“事实、推断、待复核”。当前版本先完成本地 TXT 摄取、编码识别、章节骨架和证据范围保存。

## 专业方法依据

- 反向大纲（reverse outline）：逐章记录事件、功能、人物、场景和时间线，用于发现情节漏洞、节奏失衡和无功能场景。
- Story Grid 场景分析：对每个场景记录激励事件、渐进复杂化、危机、高潮、结局，以及开场/结尾价值和价值转变。
- 文学 NLP：BookNLP 的实体、别名聚类、指代、引语归属和事件标注；中文采用规则解析 + HanLP/LLM 混合，以证据跨度和置信度约束推断。

## 分阶段管线

1. 摄取与规范化：UTF-8/GB18030、行尾、BOM、文件校验和。
2. 章节与场景边界：标题规则、段落密度、时间/地点转场；无标题文本按窗口降级并提示复核。
3. 反向大纲：事件、功能、人物、地点、时间、冲突和章节证据。
4. 人物与关系：实体、别名、指代、关系变化和首次/末次出现。
5. 事件与时间线：事件顺序、持续时间、伏笔/回收、叙事时序与故事时序。
6. 节拍与弧线：价值转变、五诫、主线/支线、角色目标-阻力-选择。
7. 风格统计：视角、句长、对话比例、感官词、重复词和叙述节奏。
8. 报告与导出：总览、章节、场景、人物、关系、时间线、节拍、风格；支持 JSON/Markdown/CSV。

## 工程约束

- 不使用单次巨型 LLM 请求；按章节/场景分块，结构化 JSON 输出后聚合。
- 每个非平凡结论包含 `sourceChapter`、`evidenceSpan`、`confidence`、`status`。
- 分析任务可暂停、重试、续跑；原文和结果写入 `~/.storydex/breakdowns/<analysisId>`。
- 默认本地处理；外发 LLM 前明确提示并允许关闭。

## 参考资料

- https://thewallflowerediting.com/how-can-a-reverse-outline-can-help-you-revise-your-novel/
- https://storygrid.com/scenes/
- https://storygrid.com/value-shift-101/
- https://storygrid.com/five-commandments-of-storytelling/
- https://github.com/booknlp/booknlp
- https://hanlp.com/
