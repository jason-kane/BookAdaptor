import const
from artifact_editor.masterplan import masterplan

def inject_image_slots(chapter):

    phrases = list(chapter.phrases())

    if not phrases:
        raise ValueError("No phrases found in chapter.  Upstream problem.")

    for phrase in phrases:
        if 'duration' not in phrase.attrs:
            return {"status": "error", "message": f"Phrase {phrase['index']} is missing duration."}

    # we do NOT want a whole new image exactly every 8 seconds _exactly_.  That ends of feeling
    # very mechanical. Not what we want.  So.. first try, consume phrases until
    # we first exceed 8 seconds, drop an image, repeat.  Easy.
    # start with an image.
    new_image = chapter.soup.new_tag("image")
    new_image.attrs['index'] = "1"
    new_image.attrs["workflow_image_template"] = const.DEFAULT_IMAGE_TEMPLATE
    
    phrases[1].insert_before(new_image)

    accumulated_time = 0
    image_index = 2
    for phrase_index, phrase in enumerate(phrases):
        if phrase_index < 2:
            continue  # skip the 0 phrase, it's the title/author/etc..

        accumulated_time += float(phrase.attrs['duration'])

        if accumulated_time >= 8.0:
            # drop an image here.
            new_image = chapter.soup.new_tag("image")
            new_image.attrs['index'] = str(image_index)

            if phrase_index + 1 < len(phrases):
                phrases[phrase_index + 1].insert_before(new_image)
                image_index += 1
            # else just ignore it, let the last image run longer.
            accumulated_time = 0

    # recalculate 'frames' for each phrase
    # easiest to just regenerate the masterplan
    #plan = masterplan.generate_masterplan(chapter)
    #masterplan.save_masterplan(chapter, plan)