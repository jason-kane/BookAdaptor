import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("home_programs", ROOT / "home/programs.py")
programs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(programs)


class GlancesTests(unittest.TestCase):
    def setUp(self):
        self.app = {"Config": {"Labels": {
            "com.docker.compose.project": "bookadaptor",
            "com.docker.compose.project.working_dir": "/host/BookAdaptor",
        }}, "NetworkSettings": {"Networks": {"bookadaptor_home-network": {}}}}

    def test_launch_uses_compose_project_and_host_directory(self):
        for service in programs.PROGRAMS:
            with self.subTest(service=service), patch.object(programs, "_docker", return_value=self.app), patch.object(
                programs.subprocess, "run"
            ) as run:
                programs.start_program(service)
            command = run.call_args.args[0]
            self.assertEqual(command, [
                "docker", "compose", "--file", "/docker-compose.yaml",
                "--project-directory", "/host/BookAdaptor",
                "--project-name", "bookadaptor", "--profile", "on-demand",
                "up", "-d", "--no-build", service,
            ])
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(run.call_args.kwargs["timeout"], 120)

    def test_compose_errors_and_timeouts_are_reported(self):
        errors = [programs.subprocess.CalledProcessError(1, "docker", stderr="launch failed"),
                  programs.subprocess.TimeoutExpired("docker", 120)]
        for error in errors:
            with self.subTest(error=error), patch.object(programs, "_docker", return_value=self.app), patch.object(
                programs.subprocess, "run", side_effect=error
            ):
                with self.assertRaises(RuntimeError):
                    programs.start_program("artifactserver")

    def test_readiness_handles_connection_refused(self):
        with patch.object(programs.http.client, "HTTPConnection") as connection:
            connection.return_value.request.side_effect = ConnectionRefusedError
            self.assertFalse(programs.glances_ready())
            connection.return_value.close.assert_called_once()

    def test_redisinsight_readiness_uses_its_own_service_and_port(self):
        with patch.object(programs.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            self.assertTrue(programs.program_ready("redisinsight"))
        connection.assert_called_once_with("redisinsight", 5540, timeout=1)

    def test_library_readiness_uses_internal_port_and_library_route(self):
        with patch.object(programs.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            self.assertTrue(programs.program_ready("artifactserver"))
        connection.assert_called_once_with("artifactserver", 8080, timeout=10)
        connection.return_value.request.assert_called_once_with("GET", "/library/")

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
