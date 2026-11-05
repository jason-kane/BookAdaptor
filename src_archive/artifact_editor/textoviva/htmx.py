"""Archived definitions from src/artifact_editor/textoviva/htmx.py.

Historical source fragments: decorators and bodies retain their original module
globals. This file is not a runtime module and must never be imported by src.
See src_archive/manifest.json for reachability evidence.
"""

def add_style_widget(
    selected_style:str="",
    url=None,
    target="#style-widget",
    label=None
) -> str:
    """
    These styles are directly applied to the image prompt.  This is to make it
    easy to use styles with context generated or manual prompts.  it isn't as
    nice as the generated prompt system since you can't change styles after they
    have been applied (without manually cleaning up the prompt)
    """
    #
    # styles are from Mile High Styler, you can browse them
    # at https://enragedantelope.github.io/Styles-FluxDev/
    # https://civitai.com/user/Triple_Headed_Monkey
    #
    log.warning('Obsolete code <TV0001>')
    style_options = []
    for s in sorted(styles.all_styles(), key=lambda x: x["name"]):
        style_options.append(
            f'<wa-option value="{styles.as_id(s["name"])}">{s["name"]}</wa-option>'
        )

    out = '<wa-select id="style-widget"'

    if label:
        out += f'            label="{label}"\n'
    if selected_style:
        out += f'            value="{selected_style}"\n'
    if url:
        out += f'''            hx-put="{url}"
            hx-swap="outerHTML transition:true"
            hx-target="{target}"
            hx-trigger="change"'''

    out += f"""
            name="style"
            id="style-selector">
            {"".join(style_options)}
        </wa-select>"""
    return out
