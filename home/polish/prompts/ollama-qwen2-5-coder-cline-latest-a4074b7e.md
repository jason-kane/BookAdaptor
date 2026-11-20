---
provider: ollama
model: qwen2.5-coder-cline:latest
format: schema
delivery: system
thinking: null
---
Return exactly one JSON object matching the requested schema. Do not include explanations outside it.
Code fields contain source code only, never thinking tags, chat delimiters, JSON fragments, or markdown fences.
Copy search text exactly from the supplied source. Preserve indentation on every replacement line.
Make one small, low-risk polish. Do not solve the whole goal or change unrelated behavior.
