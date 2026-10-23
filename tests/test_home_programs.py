import importlib.util
from pathlib import Path
import unittest
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("home_programs", ROOT / "src/artifact_editor/home/programs.py")
programs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(programs)


class GlancesTests(unittest.TestCase):
    def setUp(self):
        self.app = {"Config": {"Labels": {
            "com.docker.compose.project": "bookadaptor",
            "com.docker.compose.project.working_dir": "/host/BookAdaptor",
        }}, "NetworkSettings": {"Networks": {"bookadaptor_home-network": {}}}}

    def test_existing_container_is_started_idempotently(self):
        with patch.object(programs, "_docker", side_effect=[self.app, [{"Id": "existing"}], None]) as docker:
            programs.start_glances()
        self.assertEqual(docker.call_count, 3)
        self.assertIn("all=1", docker.call_args_list[1].args[1])
        self.assertEqual(docker.call_args.args, ("POST", "/containers/existing/start"))
        self.assertEqual(docker.call_args.kwargs["allowed"], (204, 304))

    def test_first_launch_uses_host_paths_ports_and_network_alias(self):
        config = (ROOT / "docker-compose.yaml").read_text()
        with patch("builtins.open", mock_open(read_data=config)), patch.object(
            programs, "_docker", side_effect=[self.app, [], {"Id": "new"}, None]
        ) as docker:
            programs.start_glances()
        body = docker.call_args_list[2].args[2]
        host = body["HostConfig"]
        self.assertIn("/host/BookAdaptor/glances.conf:/app/glances/conf/glances.conf", host["Binds"])
        self.assertIn("/:/rootfs:ro", host["Binds"])
        self.assertEqual(host["RestartPolicy"], {"Name": "no"})
        self.assertEqual(host["PortBindings"]["61208/tcp"], [{"HostPort": "61208"}])
        self.assertEqual(body["NetworkingConfig"]["EndpointsConfig"]["bookadaptor_home-network"]["Aliases"], ["glances"])

    def test_simultaneous_creation_uses_existing_name(self):
        config = (ROOT / "docker-compose.yaml").read_text()
        with patch("builtins.open", mock_open(read_data=config)), patch.object(
            programs, "_docker", side_effect=[self.app, [], {"message": "already exists"}, None]
        ) as docker:
            programs.start_glances()
        self.assertEqual(docker.call_args.args[1], "/containers/bookadaptor-glances-1/start")

    def test_readiness_handles_connection_refused(self):
        with patch.object(programs.http.client, "HTTPConnection") as connection:
            connection.return_value.request.side_effect = ConnectionRefusedError
            self.assertFalse(programs.glances_ready())
            connection.return_value.close.assert_called_once()

    def test_redisinsight_creation_preserves_name_and_uses_optional_defaults(self):
        config = (ROOT / "docker-compose.yaml").read_text()
        with patch("builtins.open", mock_open(read_data=config)), patch.object(
            programs, "_docker", side_effect=[self.app, [], {"Id": "insight"}, None]
        ) as docker:
            programs.start_program("redisinsight")
        create = docker.call_args_list[2]
        self.assertEqual(create.args[1], "/containers/create?name=redis_dashboard")
        body = create.args[2]
        self.assertEqual(body["HostConfig"]["Binds"], [])
        self.assertEqual(body["HostConfig"]["PidMode"], "")
        self.assertEqual(body["HostConfig"]["Tmpfs"], {})
        self.assertEqual(body["HostConfig"]["PortBindings"]["5540/tcp"], [{"HostPort": "5540"}])
        self.assertEqual(body["NetworkingConfig"]["EndpointsConfig"]["bookadaptor_home-network"]["Aliases"], ["redisinsight"])

    def test_redisinsight_readiness_uses_its_own_service_and_port(self):
        with patch.object(programs.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            self.assertTrue(programs.program_ready("redisinsight"))
        connection.assert_called_once_with("redisinsight", 5540, timeout=1)

    def test_only_known_programs_can_be_started(self):
        with patch.object(programs, "_docker") as docker:
            with self.assertRaises(ValueError):
                programs.start_program("redis")
        docker.assert_not_called()

    def test_running_status_includes_starting_container_before_http_ready(self):
        with patch.object(programs, "_docker", side_effect=[self.app, [{"Id": "insight", "State": "running"}]]):
            self.assertTrue(programs.program_running("redisinsight"))

    def test_stopped_or_missing_container_is_not_running(self):
        for containers in ([], [{"Id": "glances", "State": "exited"}]):
            with self.subTest(containers=containers), patch.object(programs, "_docker", side_effect=[self.app, containers]):
                self.assertFalse(programs.program_running("glances"))

    def test_stop_is_scoped_to_selected_service_and_keeps_container(self):
        with patch.object(programs, "_docker", side_effect=[self.app, [{"Id": "insight"}], None]) as docker:
            programs.stop_program("redisinsight")
        self.assertIn("redisinsight", docker.call_args_list[1].args[1])
        self.assertEqual(docker.call_args.args, ("POST", "/containers/insight/stop?t=5"))

    def test_stopping_missing_container_is_successful_without_mutations(self):
        with patch.object(programs, "_docker", side_effect=[self.app, []]) as docker:
            programs.stop_program("glances")
        self.assertTrue(all(call.args[0] == "GET" for call in docker.call_args_list))

    def test_stop_rejects_other_services(self):
        with patch.object(programs, "_docker") as docker:
            with self.assertRaises(ValueError):
                programs.stop_program("redis")
        docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
