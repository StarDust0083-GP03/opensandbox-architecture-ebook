"""离线测试：只验证本书描述的结构性语义，不验证上游实现。"""

import unittest

from mini_osb import (
    CreateRequest,
    Egress,
    Event,
    Execd,
    PoolRuntime,
    Rule,
    SandboxError,
    Server,
    SingleHostRuntime,
    State,
    TemplateRuntime,
    create_service,
)


class LifecycleTest(unittest.TestCase):
    def test_create_is_pending_then_running(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        self.assertEqual(sandbox.state, State.RUNNING)
        self.assertEqual(service.get(sandbox.id).id, sandbox.id)

    def test_missing_sandbox_is_404(self):
        with self.assertRaises(SandboxError) as caught:
            SingleHostRuntime().get("sbx-9999")
        self.assertEqual(caught.exception.status, 404)

    def test_pause_then_resume_keeps_id(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        paused = service.get(sandbox.id)
        self.assertEqual(paused.state, State.PAUSED)
        self.assertEqual(paused.snapshot, f"snap-{sandbox.id}")
        resumed = service.resume(sandbox.id)
        self.assertEqual(resumed.id, sandbox.id)
        self.assertEqual(resumed.state, State.RUNNING)

    def test_resume_is_not_generic_restart(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.delete(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.resume(sandbox.id)
        self.assertEqual(caught.exception.status, 404)

    def test_pause_twice_conflicts(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.pause(sandbox.id)
        self.assertEqual(caught.exception.status, 409)


class EndpointTest(unittest.TestCase):
    def test_single_host_multiplexes_ports_through_one_mapping(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        first = service.endpoint(sandbox.id, 8080)["url"]
        second = service.endpoint(sandbox.id, 5900)["url"]
        self.assertIn("/proxy/8080", first)
        self.assertIn("/proxy/5900", second)
        self.assertEqual(first.split("/proxy/")[0], second.split("/proxy/")[0])

    def test_kubernetes_endpoint_carries_routing_header(self):
        service = PoolRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        endpoint = service.endpoint(sandbox.id, 8080)
        self.assertEqual(endpoint["headers"]["OpenSandbox-Ingress-To"], f"{sandbox.id}-8080")

    def test_paused_sandbox_has_no_endpoint(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.endpoint(sandbox.id, 8080)
        self.assertEqual(caught.exception.status, 503)


class RoutingAndPoolTest(unittest.TestCase):
    def test_factory_selects_by_configuration_only(self):
        self.assertIsInstance(create_service("docker"), SingleHostRuntime)
        self.assertEqual(create_service("kubernetes").pod_backend.__class__, PoolRuntime)
        with self.assertRaises(ValueError):
            create_service("firecracker")

    def test_pool_is_bounded_capacity(self):
        service = PoolRuntime()
        service.create(CreateRequest(image="alpine"))
        service.create(CreateRequest(image="alpine"))
        with self.assertRaises(SandboxError) as caught:
            service.create(CreateRequest(image="alpine"))
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.code, "POOL_CAPACITY_EXHAUSTED")

    def test_released_pod_returns_to_pool(self):
        service = PoolRuntime()
        first = service.create(CreateRequest(image="alpine"))
        service.delete(first.id)
        again = service.create(CreateRequest(image="alpine"))
        self.assertEqual(again.state, State.RUNNING)

    def test_template_requests_reject_per_request_workload(self):
        service = TemplateRuntime()
        request = CreateRequest(image="ignored", metadata={"templateId": "t-1"}, entrypoint=["sh"])
        with self.assertRaises(SandboxError) as caught:
            service.create(request)
        self.assertEqual(caught.exception.status, 400)

    def test_composite_routes_by_create_source_and_id_prefix(self):
        service = create_service("kubernetes")
        by_template = service.create(CreateRequest(image="ignored", metadata={"templateId": "t-1"}))
        by_image = service.create(CreateRequest(image="alpine"))
        self.assertTrue(by_template.id.startswith("fsb-"))
        self.assertFalse(by_image.id.startswith("fsb-"))
        self.assertEqual(service.get(by_template.id).id, by_template.id)


class ExpirationTest(unittest.TestCase):
    def test_expiration_is_absolute(self):
        clock = [1000.0]
        server = Server("docker", api_key=None, now=clock[0])
        server.clock = lambda: clock[0]
        sandbox = server.create(None, CreateRequest(image="alpine", timeout=30))
        server.service.pause(sandbox.id)  # 暂停不延长 TTL
        clock[0] += 31
        self.assertEqual(server.expire(), [sandbox.id])

    def test_expired_sandbox_reports_terminated(self):
        clock = [1000.0]
        server = Server("docker", api_key=None)
        server.clock = lambda: clock[0]
        sandbox = server.create(None, CreateRequest(image="alpine", timeout=10))
        clock[0] += 11
        self.assertEqual(server.get(None, sandbox.id).state, State.TERMINATED)

    def test_api_key_is_enforced(self):
        server = Server("kubernetes", api_key="k")
        with self.assertRaises(SandboxError) as caught:
            server.create("wrong", CreateRequest(image="alpine"))
        self.assertEqual(caught.exception.status, 401)


class DataPlaneAndEgressTest(unittest.TestCase):
    def test_command_stream_ends_with_exit_code(self):
        events = Execd().run(["echo", "hi"])
        self.assertEqual(events[-1].type, "execution_complete")
        self.assertEqual(events[-1].exit_code, 0)

    def test_nonzero_exit_is_reported(self):
        events = Execd().run(["false"])
        self.assertEqual(events[-1].exit_code, 1)

    def test_upload_is_atomic_from_the_readers_view(self):
        execd = Execd()
        execd.write_file("/tmp/a", b"old")
        execd.write_file("/tmp/a", b"new")
        self.assertEqual(execd.read_file("/tmp/a"), b"new")
        self.assertNotIn("/tmp/a.stage", execd.files)

    def test_platform_deny_beats_user_allow(self):
        egress = Egress(deny_always=[Rule("deny", "metadata.internal")])
        egress.add(Rule("allow", "metadata.internal"))
        self.assertEqual(egress.decide("metadata.internal"), "deny")

    def test_default_action_is_deny(self):
        egress = Egress()
        egress.add(Rule("allow", "*.python.org"))
        self.assertEqual(egress.decide("files.python.org"), "allow")
        self.assertEqual(egress.decide("python.org"), "deny")  # 通配符不匹配裸域
        self.assertEqual(egress.decide("example.com"), "deny")

    def test_sse_frame_is_single_line_json(self):
        frame = Event("stdout", "a\nb").to_sse()
        self.assertTrue(frame.startswith("data: {"))
        self.assertEqual(frame.count("\n\n"), 1)


if __name__ == "__main__":
    unittest.main()
