"""Short recovery instructions for failed generation requests."""


def generation_failure_message(error):
    detail = str(error)
    if 'ComfyUI was restarted' in detail:
        return 'Generation stopped because of a GPU memory error. ComfyUI was restarted to recover. Wait for it to finish starting, then try again.'
    if 'recovery is already in progress' in detail:
        return 'Generation stopped because of a GPU memory error. ComfyUI recovery is already in progress. Wait for it to finish starting, then try again.'
    if 'restart suppressed' in detail:
        return 'Generation stopped because of a GPU memory error. ComfyUI recovery was attempted recently; another restart is paused for five minutes. Please wait before retrying.'
    if 'ComfyUI restart failed' in detail:
        return 'Generation stopped because of a GPU memory error, and ComfyUI could not be restarted. Check the server logs before retrying.'
    if 'out of memory' in detail.lower():
        return 'Generation stopped because the GPU ran out of memory. Try again after other GPU jobs finish.'
    if isinstance(error, TimeoutError):
        return 'Generation timed out. Check that ComfyUI is available, then try again.'
    return 'Generation failed. Please try again; check the server logs if it continues.'
