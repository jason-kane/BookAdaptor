# TikTok publishing setup

This integration uses Content Posting API **Upload to inbox**, with `video.upload` permission. It transfers the chapter MP4; the creator finishes posting in TikTok. Captions, visibility, AI disclosure, music, and remix choices are reviewed there. It does not silently apply the YouTube form settings.

1. Register an app at https://developers.tiktok.com/ and enable Login Kit (Web) and Content Posting API upload access. Follow the portal's app review/sandbox and target user setup; access depends on TikTok approval and granted scopes.
2. Serve this editor through an HTTPS origin you control (a development tunnel may work). Register the exact callback `https://YOUR-HOST/oauth/tiktok/callback` in Login Kit. TikTok web Login Kit requires HTTPS, unlike the local HTTP Google setup. Open the editor using this same HTTPS origin when connecting.
3. Create `src/.tiktok-oauth/client_secret.json` locally, with owner-only directory/file permissions (0700/0600):

```json
{
  "client_key": "YOUR_CLIENT_KEY",
  "client_secret": "YOUR_CLIENT_SECRET",
  "redirect_uri": "https://YOUR-HOST/oauth/tiktok/callback"
}
```

The directory is ignored by Git and persists through the existing `/app` Docker bind mount. `TIKTOK_OAUTH_DIR` can override its location (environment or Flask config). Credentials are stored server-side in `credentials.json` with owner-only permissions and refreshed when needed. The account is shared by this local editor installation.

4. Open Publish → TikTok → Connect to TikTok and grant upload permission.
5. Render the chapter video, click Upload Video to TikTok Inbox, and keep the tab open. Use Check processing status after transfer. Open the TikTok inbox notification, review all settings including AI disclosure, and post from TikTok.

The upload identifier and transfer status are saved beside the chapter MP4 in `tiktok-upload.json`. Check the TikTok inbox before retrying an uncertain transfer to avoid duplicate uploads. Uploads currently occupy a web request, subject to the deployment's request timeout.

TikTok imposes upload caps and media limits. Use a compatible MP4 (H.264 recommended), 23–60 fps, dimensions 360–4096, and a maximum 10-minute video / 4 GB; the account may have a shorter publishing duration. The integration transfers existing output and does not transcode it.

References:
- https://developers.tiktok.com/docs/en/login-kit-web
- https://developers.tiktok.com/docs/en/oauth-user-access-token-management
- https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content
- https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide

Live OAuth and upload require your registered app and have not been verified against a TikTok account.
