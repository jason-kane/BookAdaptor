from .registry import registry
import logging
import json

log = logging.getLogger(__name__)


def generate_image(
        provider_key,
        chapter,
        image_pfn,
        prompt,
        flag_fn,
        seed,
        image_index,
        lora_weights_json,
        sample) -> bytes:
    log.info(f'generate_image({provider_key=}, {chapter=})')
    #from artifact_editor.chapter import Chapter

    # incoming message, strip off the first arg as the provider key, the reset
    # we send on to generate_image()
    provider = registry.get(provider_key)

    if not provider:
        # tsqn.zimageturbo
        raise ValueError(f"Unknown TextToImageProvider key: {provider_key}")
    
    instance = provider()  # instantiating an instance of the class.

    return instance.generate_image(
        chapter_key=chapter.key,
        image_fn=image_pfn,
        prompt=prompt,
        flag_fn=flag_fn, 
        seed=seed,
        image_index=image_index,
        lora_weights=json.loads(lora_weights_json),
        sample=sample
    )
    
