from unittest.mock import MagicMock, call

from progress_cleanup import expire_stale_progress, _EXPIRE_STALE


def test_startup_scans_only_progress_namespaces_and_checks_age_atomically():
    client = MagicMock()
    client.scan.side_effect = [(12, ['video_job:old']), (0, ['video_job:fresh']),
                               (0, ['styler_progress:old']), (0, [])]
    pipe = client.pipeline.return_value.__enter__.return_value
    pipe.execute.side_effect = [[1], [0], [1]]
    assert expire_stale_progress(client) == 2
    assert client.scan.call_args_list == [
        call(cursor=0, match='video_job:*', count=1000),
        call(cursor=12, match='video_job:*', count=1000),
        call(cursor=0, match='styler_progress:*', count=1000),
        call(cursor=0, match='websocket_*', count=1000)]
    assert pipe.eval.call_args_list == [
        call(_EXPIRE_STALE, 1, key, 86400, 3600)
        for key in ('video_job:old', 'video_job:fresh', 'styler_progress:old')]
    client.delete.assert_not_called()
