"""Validate publish metadata and stream the rendered chapter video to YouTube."""


def upload_body(values):
    title = values.get('title', '').strip()
    description = values.get('description', '')
    privacy = values.get('privacy_status', 'private')
    license_type = values.get('license', 'youtube')
    kids = values.get('made_for_kids')
    ai_use = values.get('ai_use', 'true')
    paid_promotion = values.get('paid_promotion', 'false')
    category = values.get('category_id', '').strip() or '24'
    if not title or len(title) > 100:
        raise ValueError('Enter a title of 1–100 characters.')
    if len(description.encode('utf-8')) > 5000:
        raise ValueError('Description must be at most 5,000 bytes.')
    if privacy not in ('private', 'unlisted', 'public'):
        raise ValueError('Choose a valid visibility.')
    if license_type not in ('youtube', 'creativeCommon'):
        raise ValueError('Choose a valid license.')
    if kids not in ('true', 'false'):
        raise ValueError('Choose whether the video is made for kids.')
    if ai_use not in ('true', 'false'):
        raise ValueError('Choose whether the video uses AI.')
    if paid_promotion not in ('true', 'false'):
        raise ValueError('Choose whether the video includes paid promotion.')
    if not category.isascii() or not category.isdigit():
        raise ValueError('Category ID must be numeric.')
    snippet = {'title': title, 'description': description, 'categoryId': category}
    tags = [tag.strip() for tag in values.get('tags', '').split(',') if tag.strip()]
    if tags:
        snippet['tags'] = tags
    return {'snippet': snippet, 'status': {
        'privacyStatus': privacy, 'license': license_type, 'selfDeclaredMadeForKids': kids == 'true',
        'containsSyntheticMedia': ai_use == 'true'},
        'paidProductPlacementDetails': {'hasPaidProductPlacement': paid_promotion == 'true'}}


def upload_video(youtube, file_path, body):
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(file_path, mimetype='video/mp4',
                            chunksize=8 * 1024 * 1024, resumable=True)
    try:
        upload = youtube.videos().insert(part='snippet,status,paidProductPlacementDetails',
                                         body=body, media_body=media)
        response = None
        while response is None:
            _, response = upload.next_chunk(num_retries=3)
        if not response.get('id'):
            raise RuntimeError('YouTube did not return a video ID.')
        return response['id']
    finally:
        media.stream().close()
