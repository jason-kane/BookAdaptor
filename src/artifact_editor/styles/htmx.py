from . import styles

def lora_selector():
    out = """
            <wa-select
                class="smooth"
                label="LORA"
                name="lora"
                id="lora"
            >
                <wa-option value="">None</wa-option>
    """
    for lora in sorted(styles.all_loras(), key=lambda x: x["name"]):
        out += f'<wa-option value="{lora["filename"]}">{lora["name"]}</wa-option>'

    out += "</wa-select>"

    # out += """
    #             <wa-option value="comics_factory_V3.safetensors">Comics Factory</wa-option>
    #             <wa-option value="EtherealGothicEleganceZ_000002000.safetensors">Ethereal Gothic Elegance</wa-option>
    #             <wa-option value="Sketch_Portrait.safetensors">Sketch Portrait</wa-option>
    #             <wa-option value="DaVinciDrawing01_CE_ZIMGT_AIT4k.safetensors">DaVinci Drawing</wa-option>
    #             <wa-option value="Low_Poly_Papercraft_Z-Image.safetensors">Low Poly Papercraft</wa-option>
    #             <wa-option value="VintageDrawing01a_CE_ZIMGT_AIT5k.safetensors">Vintage Drawing</wa-option>
    #             <wa-option value="ArtNoveauZ.safetensors">Art Noveau</wa-option>
    #             <wa-option value="Dorota_E14.safetensors">Dorota E14</wa-option>
    #             <wa-option value="MidJourneyNSFWZ.safetensors">MidJourney NSFW</wa-option>
    #             <wa-option value="Watercolor_V7_E10.safetensors">Watercolor</wa-option>
    #         </wa-select>
    #     """
    return out

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


def get_style_choices():
    """
    returns a list of tuples of (style_key, style_name) for all styles 
    available for use.
    """
    choices = []
    for s in styles.all_styles():
        choices.append(
            (s["tag"], s["name"])
        )
    return choices

def style_selector(selected_style:str="", url=None, target="#style-selector") -> str:
    """
    returns a wa-select element with all styles available for use.
    """
    style_options = []
    for s in sorted(styles.all_styles(), key=lambda x: x["name"]):
        style_options.append(
            f'<wa-option value="{styles.as_id(s["name"])}">{s["name"]}</wa-option>'
        )

    out = '<wa-select label="Style" id="style-selector"'
    
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

def get_chapter_style_selector(chapter):
    chapter_style = chapter.get_chapter_style()
    return style_selector(
        selected_style=chapter_style,
        url="set_chapter_style"
    )

    out = f"""
    <div class="wa-cluster">
        <div class="label">Style:</div>
        <wa-select
            value="{chapter_style}"
            hx-put="set_chapter_style"
            hx-swap="outerHTML transition:true"
            hx-trigger="change"
            name="chapter_style"
        >"""
    
    for option in get_style_choices():
        out += f'<wa-option value="{option[0]}">{option[1]}</wa-option>'
    
    out += """
        </wa-select>      
    </div>
    """
    return out