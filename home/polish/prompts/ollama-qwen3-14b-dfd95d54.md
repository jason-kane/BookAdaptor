---
provider: ollama
model: qwen3:14b
format: schema
delivery: system
thinking: false
indentation: relative
---
Return exactly one JSON object matching the requested schema. Do not include explanations outside it.
Code fields contain source code only, never thinking tags, chat delimiters, JSON fragments, or markdown fences.
Copy search text exactly from the supplied source. For a Python whole-line edit, return a dedented replacement fragment with correct relative indentation; the tool restores its surrounding block indentation.
Make one small, low-risk polish. Do not solve the whole goal or change unrelated behavior.
