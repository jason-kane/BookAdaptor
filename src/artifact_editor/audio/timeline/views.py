import logging
import os

from colour import Color
from flask import Blueprint, render_template, url_for

import const
from artifact_editor import tools
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter

from artifact_editor.images import htmx as images_htmx

log = logging.getLogger(__name__)

bp = Blueprint(
    'timeline',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

@bp.route('/')
def timeline(author, title, chapter_number, language):
    """
    Generates and renders a timeline view for a chapter's audio and image segments.
    Args:
        author (str): The author of the book.
        title (str): The title of the book.
        chapter_number (int): The chapter number.
        language (str): The language of the chapter.
    Returns:
        flask.Response: Rendered HTML template for the timeline view.
    The function processes chapter XML to extract paragraphs, images, and phrases,
    calculates frame and duration statistics, assigns color coding based on frame variation,
    and prepares data for rendering the timeline template.
    """
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)
    #chapterurl = tools.get_chapterurl(author, title, chapter)
    #chapterdir = tools.get_chapterdir(author, title, chapter)

    # only phrases consume time.
    # so we're going to present based on phrase
    # then decorate that timeline with our images,
    # including image/video and transitions.

    total_frames = 0
    paragraphs = []
    #  get_xml().find_all('paragraph'):
    for paragraph_xml in chapter.paragraphs():
        paragraph = {
            'index': paragraph_xml['index'], 
            'images': [],
            'frames': 0,
        }

        for segment in paragraph_xml.children:
            if segment.name == 'image':
                # we are displaying a new image
                paragraph['images'].append({
                    'image': segment,  # attaching the whole image_xml tag right here
                    'frames': 0,
                    'duration': 0,
                    'phrases': [],
                    'paragraph_index': int(paragraph_xml['index'])
                })
            elif segment.name == 'phrase':
                if paragraph['images']:
                    phrase_frames = int(segment.get('frames', '0'))
                    paragraph['images'][-1]['frames'] += phrase_frames
                    paragraph['images'][-1]['duration'] += phrase_frames / const.FPS
                    paragraph['frames'] += phrase_frames
                    paragraph['images'][-1]['phrases'].append(segment)
                else:
                    # we got a phrase with no image
                    log.error('Invalid paragraph: %s', paragraph_xml)
        
        paragraphs.append(paragraph)
    
    # image_xml.attrs['t2i'] = "tsqn.zimageturbo"
    # In case you didn't quite follow, paragraphs will look like this:
    # [
    #     {
    #         'index': 0,
    #         'frames': 300,
    #         'images': [
    #             {
    #                 'image': <image tag>,
    #                 'frames': 100,
    #                 'duration': 4.0,
    #                 'phrases': [<phrase tag>, <phrase tag>]
    #                 'first_frame': 0,
    #                 'last_frame': 100,
    #                 'variation_message': "Too Long",  # how is this part of the piece flawed?  be brief, make it hurt.
    #                 'frame_variation': 0.5,  # [0..1] How bad is it, for realzies?
    #                 'background': "#FF0000",  # if you want a RYG background, use this color.
    #                 'foreground': "#FFFFFF"  # and this font color (contrasting w/BG)
    #             },
    #             {
    #                 'image': <image tag>,
    #                 'frames': 200,
    #                 'duration': 8.0,
    #                 'phrases': [<phrase tag>, <phrase tag>]
    #                 ...
    #             }
    #         ]
    #     },
    #     ... one per _paragraph_
    # ]

    # the rest of this is to build that data structure, then dress it up for
    # display.
    
    # the various "tag" objects include an 'index' which should be an easy
    # lookup in the xml.

    first_frame = 0
    total_frames = 0
    for paragraph_index in range(len(paragraphs)):
        for image_index in range(len(paragraphs[paragraph_index]['images'])):
            paragraphs[paragraph_index]['images'][image_index]['first_frame'] = first_frame
            first_frame += paragraphs[paragraph_index]['images'][image_index]['frames']

            paragraphs[paragraph_index]['images'][image_index]['last_frame'] = first_frame
            first_frame += 1

        paragraphs[paragraph_index]['duration'] = paragraphs[paragraph_index]['frames'] / const.FPS
        total_frames += paragraphs[paragraph_index]['frames']

    avg_image_frames = total_frames / len(paragraphs)

    color_map = list(Color("lime").range_to(Color("red"), 32))
    for paragraph_index in range(len(paragraphs)):
        for image_index in range(len(paragraphs[paragraph_index]['images'])):
            # some additional decoration for each image now that we have real
            # context.
            ii = paragraphs[paragraph_index]['images'][image_index]
            # (else) == green
            # 2x == yellow
            # 4x == red
            variation = abs((ii['frames'] - avg_image_frames) / avg_image_frames)
            color_index = int(variation * 32)
            color_index = max(0, min(31, color_index))
            log.info(f'Distance: {variation} => {color_index}')

            if ii['frames'] > avg_image_frames:
                ii['variation_message'] = "Too Long"
            elif ii['frames'] < avg_image_frames:
                ii['variation_message'] = "Too Short"

            ii['frame_variation'] = variation
            ii['background'] = color_map[color_index].hex_l
            
            if color_map[color_index].luminance < 0.5:
                ii['foreground'] = "#FFFFFF"
            else:
                ii['foreground'] = "#000000"

            log.info(f"{ii['image']=}")
            if ii["image"].attrs.get('animation_method', 'false') != 'false':
                ii['variation_message'] += " (Animated)"
                ii['animation'] = True
                ii['animation_url'] = images_htmx.get_animation_url(
                    chapter, int(ii['image']['index'])
                )
            else:
                ii['animation'] = False  # explicite is better.

            paragraphs[paragraph_index]['images'][image_index] = ii

# # <video id="my-video" class="video-js" controls="" preload="auto" width="372" height="372" data-setup="{}">
#             <source src="animation/img_3_Americana_mode__Norman_Ro_20db3455_157e.mp4" type="video/mp4">
#             <p class="vjs-no-js">
#                 To view this video please enable JavaScript, and consider upgrading to a
#                 web browser that
#                 <a href="https://videojs.com/html5-video-support/" target="_blank">supports HTML5 video</a>
#             </p>
#             </video>


# http://localhost:5000/Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/images/0.png

    return render_template(
        'timeline.html',
        animation_configuration=images_htmx.get_animation_configuration_widgets,
        get_animation_url=images_htmx.get_animation_url,
        section="phrase.timeline",
        section_cosmetic="Phrase › Timeline",
        language=language,
        pretty_language=language.title(),
        author=author,
        pretty_author=author,
        title=title,
        pretty_title=title,
        chapter=chapter,
        chapterurl=chapter.url,
        paragraphs=paragraphs,
        avg_image_frames=avg_image_frames,
        avg_image_duration=avg_image_frames / const.FPS
    )

def animation_configuration(image_xml):
    """
    Given an image tag, return the animation configuration for that image, if it exists.
    """
    if image_xml.attrs.get('animation_method', 'false') == 'false':
        return None

    return {
        'method': image_xml['animation_method'],
        'parameters': {
            k: v
            for k, v in image_xml.attrs.items()
            if k.startswith('animation_parameter_')
        }
    }