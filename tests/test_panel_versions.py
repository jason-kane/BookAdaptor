from unittest.mock import patch

from home.service_info import image_version, panel_versions


def test_version_labels_tags_and_digests():
    assert image_version('redis:8.8', {'Config': {'Labels': {'org.opencontainers.image.version': '8.8.1'}}}) == '8.8.1'
    assert image_version('redis:8.8') == '8.8'
    assert image_version('registry:5000/team/app:1.2') == '1.2'
    assert image_version('registry:5000/team/app') == 'latest'
    assert image_version('app@sha256:1234567890abcdef') == 'sha256:1234567890ab'
    assert image_version('') == 'Version unavailable'


def test_running_image_metadata_is_used_and_shared_images_inspected_once(tmp_path):
    config = tmp_path / 'compose.yaml'
    config.write_text('services:\n  redis:\n    image: redis:9\n')
    container = {'Labels': {'com.docker.compose.service': 'redis'}, 'Image': 'redis:8.8',
                 'ImageID': 'sha256:actual', 'State': 'running'}
    with patch('home.programs._docker', side_effect=[
        {'Config': {'Labels': {'com.docker.compose.project': 'example'}}},
        [container], {'Config': {'Labels': {'org.opencontainers.image.version': '8.8.1'}}}
    ]) as docker:
        result = panel_versions({'redis': ('redis',), 'library': ('redis',)}, str(config))
    assert result['redis'][0]['version'] == '8.8.1'
    assert result['redis'][0]['image'] == 'redis:8.8'
    assert result['redis'] == result['library']
    assert docker.call_count == 3
    assert 'sha256%3Aactual' in docker.call_args.args[1]


def test_configured_tag_is_available_when_docker_is_unavailable(tmp_path):
    config = tmp_path / 'compose.yaml'
    config.write_text('services:\n  redis:\n    image: redis:8.8\n')
    with patch('home.programs._docker', side_effect=OSError('offline')):
        result = panel_versions({'redis': ('redis',)}, str(config))
    assert result['redis'][0]['version'] == '8.8'


def test_base_os_labels_are_not_reported_as_application_versions():
    ubuntu = {'Config': {'Labels': {'org.opencontainers.image.title': 'ubuntu',
                                   'org.opencontainers.image.version': '26.04'}}}
    assert image_version('bookadaptor-app:latest', ubuntu) == 'latest'
    assert image_version('ubuntu:26.04', ubuntu) == '26.04'
    rocky = {'Config': {'Labels': {'org.opencontainers.image.title': 'rockylinux',
                                  'org.opencontainers.image.version': '10', 'version': '10'}}}
    assert image_version('comfyui-boot:cu130-megapak-pt211', rocky) == 'cu130-megapak-pt211'
