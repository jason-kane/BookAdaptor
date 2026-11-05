"""Keep narration and quoted dialogue in separate import phrase runs."""


def speech_runs(operations):
    """Split Quill operations at double quotes without losing text or styling.

    Run IDs survive formatting changes. Closing quotes stay with the dialogue;
    apostrophes and nested single quotes do not change the outer speaker.
    """
    run = 0
    in_dialogue = False
    for operation in operations:
        if 'illustration' in operation or not isinstance(operation.get('insert'), str):
            yield dict(operation, speech_run=run)
            continue
        text = operation['insert']
        start = 0
        for index, char in enumerate(text):
            opens = char == '“' or (char == '"' and not in_dialogue)
            closes = char == '”' or (char == '"' and in_dialogue)
            if opens and not in_dialogue:
                if index > start:
                    yield dict(operation, insert=text[start:index], speech_run=run)
                run += 1
                start = index
                in_dialogue = True
            elif closes and in_dialogue:
                yield dict(operation, insert=text[start:index + 1], speech_run=run)
                run += 1
                start = index + 1
                in_dialogue = False
        if start < len(text):
            yield dict(operation, insert=text[start:], speech_run=run)
