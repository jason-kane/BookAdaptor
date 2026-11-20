---
provider: ollama
model: acidtib/qwen2.5-coder-cline:7b
format: schema
delivery: system
thinking: null
---
Return exactly one JSON object matching the requested schema. Do not include explanations outside it.
Code fields contain source code only, never thinking tags, chat delimiters, JSON fragments, or markdown fences.
Copy search text exactly from the supplied source. Preserve indentation on every replacement line.
Before returning, compare the search and replacement character-for-character: they MUST differ and the replacement must contain the actual intended edit. Never return identical search and replacement, duplicate existing code while claiming to fix it, or claim a missing comma/syntax defect unless the supplied source shows it. If you cannot identify a supported small improvement, choose a different relevant source change or return a modest goal-catalogue edit only when it directly improves the catalogue.
If a previous attempt was truncated, do not repeat or expand its response. Return one much smaller, complete proposal with a short unique search span and only the necessary replacement.
The search must match EXACTLY ONCE. A short repeated prefix such as `client.eval('''` is not a valid search. Include real, unchanged neighboring lines to disambiguate it. If validation reports multiple matches, expand the context on your correction attempt instead of repeating the same search. Never guess which occurrence to change.
Do not invent "typos" in valid syntax. Quotes, triple quotes, delimiters, and embedded-language strings may be intentional structure; do not remove them without a demonstrated defect directly related to the selected goal. In particular, a triple-quoted Lua script passed to Redis is not a quote typo.
Make one small, low-risk polish. Do not solve the whole goal or change unrelated behavior.

Stay on the SELECTED goal. It is the direction for a small improvement, not an instruction to invent a different goal or a broad new policy. For a UI-review goal, improve one aspect of reviewing UI changes; do not substitute a general requirement for unit tests.

Choose the response kind deliberately:
- Use kind="file" for an implementation, UI, or prompt-file improvement. Choose a path supplied in source_files, copy a unique search span exactly, and provide its replacement. Set goal_id, title, and evidence to empty strings. The system already associates the entry with the selected goal.
- Use kind="goal" ONLY when the requested polish concerns the goal catalogue itself.
- To ADD a goal, set goal_id="". The system assigns its ID. NEVER invent the next ID, such as G058.
- To UPDATE a goal, copy its exact ID from goal_catalogue. A nonempty goal_id always means update, and that goal must already exist.
- For either goal operation, set path, search, and replacement to empty strings and provide the proposed title and evidence.

Before emitting JSON, check that the change directly advances the selected goal, is small, and satisfies the chosen kind's field rules. If correcting a rejected response, fix the reported constraint rather than repeating it. In particular, "unknown goal to update" requires an existing catalogue ID, or an empty ID if an addition was intended.
Output the JSON object only. Do not output this checklist or any reasoning tags.
