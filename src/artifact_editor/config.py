'''
This is the per-book level configuraton.
'''
import os
import json 
import logger
import const
from functools import lru_cache


log = logger.log(__name__)


def save_config(chapterdir, config):
    """
    config is the new config.  remember it forever.
    """
    chapterdir = chapterdir.lstrip('/')

    pfn = os.path.join(
        const.LIBRARY_DIR, 
        chapterdir,
        'config.json'
    )
    log.info(f'Saving {pfn}')
    
    os.makedirs(os.path.dirname(pfn), exist_ok=True)

    with open(pfn, 'w') as h:
        h.write(json.dumps(config, indent=4))

    log.info('Saved Config', config=config)
    # get_config.cache_clear()
    return config


def get_config(chapterdir):   
    # we're being inconsistent somewhere.
    chapterdir = chapterdir.lstrip('/')

    log.debug('config.get_config()', const_LIBRARY_DIR=const.LIBRARY_DIR, chapterdir=chapterdir)
    pfn = os.path.join(
        const.LIBRARY_DIR, 
        chapterdir, 
        'config.json'
    )
    
    log.info(f'Opening {pfn}')
    config = {}
    if os.path.exists(pfn):
        with open(pfn, 'r') as h:
            config = json.loads(h.read())

    return config