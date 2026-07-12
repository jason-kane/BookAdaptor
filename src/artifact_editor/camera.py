import json
import os

import logger
import const

log = logger.log(__name__)


def get_camera_fn(chapter):
    return os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        f"camera_{chapter.aspect}.json"
    )


def delete_camera(chapter):
    """
    Clear the camera cache for the book.
    """
    camera_file = get_camera_fn(chapter)
    
    if os.path.exists(camera_file):
        os.remove(camera_file)
    
    global _frame_loaded
    _frame_loaded = False

    global _frame_to_camera
    _frame_to_camera = []
    return

_frame_loaded = False
_frame_to_camera = []
def frame_to_camera(frame_index: int) -> dict:
    """
    Given a frame index, return the scroll lock for that frame.
    """ 
    if not _frame_loaded:       
        log.error("Camera data not loaded, cannot retrieve scroll lock for frame %d.", frame_index)
        raise ValueError("Camera data not loaded.")    
    try:
        return _frame_to_camera[frame_index]
    except IndexError:
        log.error(f"Frame index {frame_index} out of range for frame_to_camera ({len(_frame_to_camera)}).")
        return None


def set_scrollrate(
        frame: int,
        scrollrate: float
    ) -> bool:
    """
    Set the camera scroll rate for a specific frame.
    """   
    frame = int(frame)
    scrollrate = float(scrollrate)
    
    log.info(f'Setting scroll rate for frame {frame} to {scrollrate} pixels.')
    
    if frame < 0 or frame >= len(_frame_to_camera):
        log.error(f"Cannot set scroll rate at frame {frame}, out of range.")
        return True

    previous_height = _frame_to_camera[frame]
    delta = scrollrate - previous_height
    log.info(f'  Previous height was {previous_height}, delta is {delta}.')

    _frame_to_camera[frame] = scrollrate

    log.info(f'Adjusting all subsequent frames [{frame + 1}:{len(_frame_to_camera)}] by {delta} to maintain scroll rate.')
    if delta != 0:
        for i in range(frame + 1, len(_frame_to_camera)):
            if _frame_to_camera[i] is None:
                log.error(f"Cannot set scroll rate at frame {i}, prior value is None.")
            else:
                _frame_to_camera[i] += delta

    return True


def boost_scrollrate(
        frame: int,
        target_height: float,
    ) -> bool:
    """
    _gently_ alter the camera scroll rate sequence so we reach target_height at frame.
    """   
    
    log.info(f'Boosting scroll rate to reach {target_height} at frame {frame}.')
    frame = int(frame)
    target_height = float(target_height)
    
    # we're going to back up a good long ways, and adjust the scroll rate linearly.
    
    # 10 seconds of ramp up
    initial_frame = max(0, frame - (const.FPS * 10))
    if initial_frame == 0:
        log.info('We are up against the beginning of the video, cannot boost scroll rate gradually.')
        log.info('TODO:  figure something out.')
        return True

    initial_height = _frame_to_camera[initial_frame]
    previous_height = _frame_to_camera[frame]
    log.info(f'Initial frame {initial_frame} has height {initial_height}.')
    log.info(f'  Final frame {frame} has height {previous_height} but it should be {target_height}.')

    # so easy-sauce.  We have two coordinates,
    # (initial_frame, initial_height)
    # (frame, target_height)
    # the frames are evently distributed, so we can just do a linear interpolation
    total_frames = frame - initial_frame
    
    total_height_delta = target_height - previous_height
    log.info(f'Traversing {total_height_delta} pixels over {total_frames} frames.')
    # total_height_delta = target_height - initial_height

    if total_frames > 0:
        height_per_frame = total_height_delta / total_frames
        log.info(f'Adding {height_per_frame} pixels per frame [{initial_frame}:{frame}]')
        height_adjust = height_per_frame

        for i in range(initial_frame, frame + 1):
            _frame_to_camera[i] += height_adjust
            height_adjust += height_per_frame

    else:
        log.error(f"Cannot boost scroll rate at frame {frame}, invalid frame range.")
        return True
    
    #last_frame = initial_frame + total_frames

    log.info(f'Adjusting all subsequent frames [{frame + 1}:{len(_frame_to_camera)}] by {total_height_delta} to maintain scroll rate.')
    if total_height_delta != 0:
        for i in range(frame + 1, len(_frame_to_camera)):
            if _frame_to_camera[i] is None:
                log.error(f"Cannot boost scroll rate at frame {i}, prior value is None.")
            else:
                _frame_to_camera[i] += total_height_delta

    return True


def boost_scrollrate_old(
        start_frame: int,
        distance: int,
        adjustment_percentage: float
    ) -> bool:
    """
    Nudge the scroll rate for a specific frame.
    """   
    start_frame = int(start_frame)
    distance = int(distance)

    if distance > start_frame:
        log.error(f"Cannot boost scroll rate at frame {start_frame} by distance {distance}, distance exceeds start frame.")
        return True

    frame_index = start_frame - distance
    
    prior_scroll = _frame_to_camera[frame_index - 1]
    prechange = _frame_to_camera[frame_index]

    if not prior_scroll or not prechange:
        log.error(f"Cannot boost scroll rate at frame {frame_index}, missing prior or prechange values.")
        return True

    old_rate = prechange - prior_scroll
    new_rate = old_rate * (1 + adjustment_percentage)

    delta = new_rate - old_rate
        
    log.info(f"Altering scroll for frame {frame_index} from {prechange} pixels to {prior_scroll + new_rate} pixels, ({adjustment_percentage}%)")
    _frame_to_camera[frame_index] = prior_scroll + new_rate

    # and everything after that needs to slide linearly with no rate change
    log.info(f"Adjusting all subsequent frames after frame {frame_index} to maintain scroll rate.")
    for frame_index in range((start_frame - distance) + 1, len(_frame_to_camera)):
        # how much of an increment happened at frame_index _before_
        # we touched it?  we want to retain the increment.
        #increment = prechange - _frame_to_camera[frame_index]
        #prechange = _frame_to_camera[frame_index]

        _frame_to_camera[frame_index] += delta
        # _frame_to_camera[frame_index] + increment

    return True


def set_frame_to_camera(frame_to_camera_value):
    global _frame_to_camera
    _frame_to_camera = frame_to_camera_value


def load_camera(chapter, aspect='widescreen'):
    """
    Load the camera position from disk
    """
    global _frame_loaded
    global _frame_to_camera

    if _frame_loaded:
        log.info("Camera data already loaded, unloading first...")
        _frame_loaded = False
        _frame_to_camera = []

    camera_fn = get_camera_fn(chapter)

    if os.path.exists(camera_fn):
        with open(camera_fn, "r") as h:            
            _frame_loaded = True

            camera = json.load(h)
            log.info(f"Loading {len(camera)} frames from {camera_fn}")
            set_frame_to_camera(camera)
            return True
    return False


def save_camera(chapter):
    """
    Save the camera position to disk
    """
    camera_fn = get_camera_fn(chapter)

    with open(camera_fn, "w") as h:
        log.info(f"Saving camera to {camera_fn}")
        json.dump(_frame_to_camera, h, indent=4)
