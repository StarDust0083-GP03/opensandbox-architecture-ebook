#!/usr/bin/env python3
"""mini_osb —— 用标准库复刻 OpenSandbox 的骨架。

这里没有容器、没有 Kubernetes、没有 Firecracker。它只保留本书讨论的
结构性决定，每个决定都能对应到上游源码里的一个文件：

    Server(api + service 工厂)   -> server/opensandbox_server/services/factory.py
    SandboxService 接口          -> server/opensandbox_server/services/sandbox_service.py
    按 ID 前缀路由的 Composite   -> server/opensandbox_server/services/composite_service.py
    单主机端口 + /proxy/{port}   -> components/execd 的内置反向代理
    Pool 预热与分配注解          -> kubernetes/internal/controller/pool_controller.go
    execd 的 SSE 事件与退出码    -> components/execd/pkg/web/model/codeinterpreting.go
    egress 的规则优先级          -> components/egress/pkg/policy/always_rules.go
    绝对过期时间与暂停/恢复             -> docs/guides/pause-resume.md

运行：python3 examples/mini_osb.py
测试：python3 -m unittest discover -s examples -v
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


class State(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSING = "Pausing"
    PAUSED = "Paused"
    RESUMING = "Resuming"
    STOPPING = "Stopping"
    TERMINATED = "Terminated"
    FAILED = "Failed"


class SandboxError(Exception):
    """服务层对外只抛一种错误，附带 HTTP 语义的状态码。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


@dataclass
class Sandbox:
    id: str
    image: str
    state: State
    expires_at: float | None
    entrypoint: list[str]
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    node: str | None = None
    # 数据面地址：单主机后端只有一个 host proxy 端口，K8s 后端是 pod IP。
    data_plane: tuple[str, int] | None = None
    # 只有 paused 时才有值：恢复时要读回的 rootfs 快照 ID。
    snapshot: str | None = None
    reason: str | None = None
    message: str | None = None


# --------------------------------------------------------------------------
# 数据面：一个 execd 替身。命令执行走“事件流 + 终止事件”，不是返回值。
# --------------------------------------------------------------------------

@dataclass
class Event:
    """对应 execd 的 ServerStreamEvent：type/text/exit_code。"""

    type: str
    text: str = ""
    exit_code: int | None = None

    def to_sse(self) -> str:
        payload = {"type": self.type}
        if self.text:
            payload["text"] = self.text
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class Execd:
    """沙箱内守护进程：命令、文件、指标。全部在进程内模拟。"""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.env: dict[str, str] = {}

    def run(self, argv: list[str]) -> list[Event]:
        if not argv:
            return [Event("error", "argv must not be empty"), Event("execution_complete", exit_code=2)]
        out = " ".join(argv)
        # 只认识一个内置命令；其他命令都当作成功的 echo，避免真的执行外部程序。
        if argv[0] == "false":
            return [Event("stderr", "command failed\n"), Event("execution_complete", exit_code=1)]
        if argv[0] == "ls":
            body = "\n".join(sorted(self.files)) or ""
            return [Event("stdout", body + ("\n" if body else "")), Event("execution_complete", exit_code=0)]
        return [Event("stdout", f"{out}\n"), Event("execution_complete", exit_code=0)]

    def write_file(self, path: str, data: bytes) -> None:
        # 上游把上传写到目标旁边的暂存文件再 rename；这里只保留“不会写一半”的语义。
        staged = f"{path}.stage"
        self.files[staged] = data
        self.files[path] = self.files.pop(staged)

    def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise SandboxError(404, "FILE_NOT_FOUND", path)
        return self.files[path]


# --------------------------------------------------------------------------
# 出向网络：两条层（DNS 与网络）在本书里压成一次求值，但优先级保持原样。
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    action: str  # "allow" | "deny"
    target: str  # "pypi.org" / "*.pypi.org" / "10.0.0.0/8"

    def matches(self, host: str) -> bool:
        if self.target.startswith("*."):
            return host.endswith(self.target[1:])
        return host == self.target


class Egress:
    """platform deny.always > platform allow.always > 用户策略。"""

    def __init__(self, deny_always: Iterable[Rule] = (), allow_always: Iterable[Rule] = ()) -> None:
        self.deny_always = list(deny_always)
        self.allow_always = list(allow_always)
        self.rules: list[Rule] = []
        self.default_action = "deny"

    def add(self, *rules: Rule) -> None:
        self.rules.extend(rules)

    def decide(self, host: str) -> str:
        for rule in self.deny_always:
            if rule.matches(host):
                return "deny"
        for rule in self.allow_always:
            if rule.matches(host):
                return "allow"
        for rule in self.rules:  # 用户策略：第一条命中的规则生效
            if rule.matches(host):
                return rule.action
        return self.default_action


# --------------------------------------------------------------------------
# 控制面：一个接口，两个后端。
# --------------------------------------------------------------------------

class SandboxService:
    """所有运行时实现同一套生命周期语义；API 层不认识容器或 Pod。"""

    id_prefix = ""

    def __init__(self) -> None:
        self.sandboxes: dict[str, Sandbox] = {}

    def ready(self, sandbox: Sandbox) -> None:
        sandbox.state = State.RUNNING
        sandbox.reason = "Ready"
        sandbox.message = "sandbox is running"

    def create(self, request: "CreateRequest") -> Sandbox:
        raise NotImplementedError

    def get(self, sandbox_id: str) -> Sandbox:
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(404, "SANDBOX_NOT_FOUND", sandbox_id)
        return sandbox

    def list(self) -> list[Sandbox]:
        return sorted(self.sandboxes.values(), key=lambda s: s.id)

    def delete(self, sandbox_id: str) -> None:
        sandbox = self.get(sandbox_id)
        sandbox.state = State.STOPPING
        self.sandboxes.pop(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        sandbox = self.get(sandbox_id)
        if sandbox.state == State.PAUSED:
            raise SandboxError(409, "SANDBOX_ALREADY_PAUSED", sandbox_id)
        if sandbox.state != State.RUNNING:
            raise SandboxError(409, "SANDBOX_NOT_RUNNING", f"{sandbox_id} is {sandbox.state.value}")
        sandbox.state = State.PAUSING
        sandbox.snapshot = f"snap-{sandbox_id}"  # 先写快照，再报 Paused
        sandbox.state = State.PAUSED
        sandbox.node = None
        sandbox.data_plane = None

    def resume(self, sandbox_id: str) -> Sandbox:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.PAUSED:
            # resume 是暂停态操作，不是通用重启；已退出就是 Terminated/Failed。
            raise SandboxError(409, "SANDBOX_NOT_PAUSED", f"{sandbox_id} is {sandbox.state.value}")
        if sandbox.snapshot is None:
            raise SandboxError(409, "SANDBOX_SNAPSHOT_MISSING", sandbox_id)
        sandbox.state = State.RESUMING
        self._restore(sandbox)
        self.ready(sandbox)
        return sandbox

    def _restore(self, sandbox: Sandbox) -> None:
        raise NotImplementedError

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        raise NotImplementedError


class SingleHostRuntime(SandboxService):
    """Docker 形态：每个沙箱只映射一个 host 端口，其余端口走 /proxy/{port}。"""

    id_prefix = "sbx-"
    PROXY_PORT = 44772

    def __init__(self) -> None:
        super().__init__()
        self._next_host_port = 40000

    def create(self, request: "CreateRequest") -> Sandbox:
        sandbox = Sandbox(
            id=f"sbx-{len(self.sandboxes) + 1:04d}",
            image=request.image,
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=list(request.entrypoint),
            env=dict(request.env),
            metadata=dict(request.metadata),
        )
        self.sandboxes[sandbox.id] = sandbox
        self._next_host_port += 1
        sandbox.data_plane = ("127.0.0.1", self._next_host_port)
        sandbox.node = "localhost"
        self.ready(sandbox)
        return sandbox

    def _restore(self, sandbox: Sandbox) -> None:
        # 容器级 pause/resume 保留容器，所以恢复只是取消冻结；host 端口不变。
        self._next_host_port += 1
        sandbox.data_plane = ("127.0.0.1", self._next_host_port)
        sandbox.node = "localhost"

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING or sandbox.data_plane is None:
            raise SandboxError(503, "SANDBOX_NOT_READY", sandbox_id)
        host, proxy_port = sandbox.data_plane
        if port == self.PROXY_PORT:
            return {"url": f"http://{host}:{proxy_port}"}
        # 所有容器端口共用一个 host 端口，路径前缀是唯一的路由信息。
        return {"url": f"http://{host}:{proxy_port}/proxy/{port}", "headers": {}}


class PoolRuntime(SandboxService):
    """Kubernetes + Pool 形态：预热 Pod 是容量，分配是标注，不是调度。"""

    id_prefix = "sbx-"
    CAPACITY = 2

    def __init__(self) -> None:
        super().__init__()
        self.warm: list[str] = [f"pod-{i}" for i in range(1, self.CAPACITY + 1)]
        self.allocated: dict[str, str] = {}  # sandbox_id -> pod

    def create(self, request: "CreateRequest") -> Sandbox:
        if not self.warm:
            raise SandboxError(429, "POOL_CAPACITY_EXHAUSTED", "no warm pod available")
        pod = self.warm.pop(0)
        sandbox = Sandbox(
            id=f"sbx-{len(self.sandboxes) + 1:04d}",
            image=request.image,
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=list(request.entrypoint),
            env=dict(request.env),
            metadata=dict(request.metadata),
            node=pod,
        )
        self.sandboxes[sandbox.id] = sandbox
        self.allocated[sandbox.id] = pod
        # 预热 Pod 没有镜像信息，entrypoint 只能作为分配后的 task 下发。
        sandbox.metadata.setdefault("opensandbox.io/task", json.dumps(sandbox.entrypoint))
        sandbox.data_plane = (f"10.0.0.{len(self.allocated)}", 44772)
        self.ready(sandbox)
        return sandbox

    def _restore(self, sandbox: Sandbox) -> None:
        # 恢复不保证落回原 Pod：IP 会变，客户端必须重新解析 endpoint。
        sandbox.node = self.allocated.get(sandbox.id, "pod-recycled")
        sandbox.data_plane = (f"10.0.0.{len(self.allocated) + 10}", 44772)

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING or sandbox.data_plane is None:
            raise SandboxError(503, "SANDBOX_NOT_READY", sandbox_id)
        host, _ = sandbox.data_plane
        return {"url": f"http://{host}:{port}", "headers": {"OpenSandbox-Ingress-To": f"{sandbox_id}-{port}"}}

    def delete(self, sandbox_id: str) -> None:
        super().delete(sandbox_id)
        pod = self.allocated.pop(sandbox_id, None)
        if pod is not None:
            self.warm.append(pod)  # recycleStrategy=Delete：换一个干净的 Pod


@dataclass
class CreateRequest:
    image: str
    timeout: float | None = 600.0
    entrypoint: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    now: float = field(default_factory=time.time)

    def expires_at(self) -> float | None:
        # 绝对 TTL：之后无论是重启还是暂停都不会重置。
        return None if self.timeout is None else self.now + self.timeout


class CompositeService(SandboxService):
    """一个 runtime.type，两种创建路径：镜像走 Pod，模板走 fsb。"""

    def __init__(self, pod_backend: PoolRuntime, template_backend: SandboxService | None = None) -> None:
        super().__init__()
        self.pod_backend = pod_backend
        self.template_backend = template_backend

    def _route(self, request: CreateRequest) -> SandboxService:
        if request.metadata.get("templateId") and self.template_backend is not None:
            return self.template_backend
        return self.pod_backend

    def create(self, request: CreateRequest) -> Sandbox:
        return self._route(request).create(request)

    def _backend(self, sandbox_id: str) -> SandboxService:
        # 上游用 "fsb-" 前缀分发既有沙箱；这里用同一个思路。
        if sandbox_id.startswith("fsb-") and self.template_backend is not None:
            return self.template_backend
        return self.pod_backend

    def get(self, sandbox_id: str) -> Sandbox:
        return self._backend(sandbox_id).get(sandbox_id)

    def list(self) -> list[Sandbox]:
        return self.pod_backend.list() + (self.template_backend.list() if self.template_backend else [])

    def delete(self, sandbox_id: str) -> None:
        self._backend(sandbox_id).delete(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        self._backend(sandbox_id).pause(sandbox_id)

    def resume(self, sandbox_id: str) -> Sandbox:
        return self._backend(sandbox_id).resume(sandbox_id)

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        return self._backend(sandbox_id).endpoint(sandbox_id, port)


def create_service(runtime: str) -> SandboxService:
    """对应 services/factory.py：配置决定实现，API 层不写 if runtime == ..."""
    if runtime == "docker":
        return SingleHostRuntime()
    if runtime == "kubernetes":
        return CompositeService(PoolRuntime(), TemplateRuntime())
    raise ValueError(f"Unsupported runtime: {runtime}")


class TemplateRuntime(SandboxService):
    """fsb 形态：模板决定工作负载，创建请求只表达意图（谁、多久）。"""

    id_prefix = "fsb-"
    counter = 0

    def create(self, request: CreateRequest) -> Sandbox:
        if request.entrypoint or request.env:
            raise SandboxError(400, "TEMPLATE_FIELD_REJECTED", "entrypoint/env come from the template")
        TemplateRuntime.counter += 1
        sandbox = Sandbox(
            id=f"fsb-{TemplateRuntime.counter:04d}",
            image=f"template:{request.metadata['templateId']}",
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=[],
            metadata=dict(request.metadata),
            node="fastlet-1",
            data_plane=("10.30.0.5", 44772),
        )
        self.sandboxes[sandbox.id] = sandbox
        self.ready(sandbox)
        return sandbox


# --------------------------------------------------------------------------
# 一个最小的 Server：认证、路由、过期扫描。
# --------------------------------------------------------------------------

class Server:
    def __init__(self, runtime: str, api_key: str | None = None, now: float | None = None) -> None:
        if api_key is None and runtime != "docker":
            # 上游在非交互环境里拒绝无 API key 启动（startup_guard.api_key_confirm）。
            raise SystemExit("refusing to start without an API key outside local development")
        self.service = create_service(runtime)
        self.api_key = api_key
        self.clock = now if now is not None else time.time

    def _auth(self, key: str | None) -> None:
        if self.api_key is not None and key != self.api_key:
            raise SandboxError(401, "UNAUTHORIZED", "missing or invalid OPEN-SANDBOX-API-KEY")

    def create(self, key: str | None, request: CreateRequest) -> Sandbox:
        self._auth(key)
        request.now = self.clock()
        return self.service.create(request)

    def get(self, key: str | None, sandbox_id: str) -> Sandbox:
        self._auth(key)
        sandbox = self.service.get(sandbox_id)
        if sandbox.expires_at is not None and self.clock() >= sandbox.expires_at:
            sandbox.state = State.TERMINATED
            sandbox.reason = "Expired"
        return sandbox

    def endpoint(self, key: str | None, sandbox_id: str, port: int) -> dict[str, str]:
        self._auth(key)
        return self.service.endpoint(sandbox_id, port)

    def expire(self) -> list[str]:
        """过期是绝对的：扫描只看 expires_at，不看最后一次访问时间。"""
        expired = [
            sandbox.id
            for sandbox in self.service.list()
            if sandbox.expires_at is not None and self.clock() >= sandbox.expires_at
        ]
        for sandbox_id in expired:
            self.service.delete(sandbox_id)
        return expired


def main() -> None:
    server = Server("kubernetes", api_key="demo-key")
    sandbox = server.create("demo-key", CreateRequest(image="alpine:3.19", timeout=60))
    print("created:", sandbox.id, sandbox.state.value, "on", sandbox.node)

    execd = Execd()
    execd.write_file("/tmp/hello.sh", b"echo hi\n")
    for event in execd.run(["sh", "/tmp/hello.sh"]):
        print(event.to_sse().strip())

    egress = Egress(deny_always=[Rule("deny", "metadata.internal")], allow_always=[Rule("allow", "pypi.org")])
    egress.add(Rule("allow", "*.python.org"))
    print("egress pypi.org        ->", egress.decide("pypi.org"))
    print("egress evil.example    ->", egress.decide("evil.example"))
    print("egress metadata.internal ->", egress.decide("metadata.internal"))

    proxy = server.endpoint("demo-key", sandbox.id, 8080)
    print("endpoint 8080:", proxy["url"], proxy["headers"] or "(no headers)")

    server.service.pause(sandbox.id)
    print("paused:", server.service.get(sandbox.id).state.value, "snapshot:", server.service.get(sandbox.id).snapshot)
    resumed = server.service.resume(sandbox.id)
    print("resumed:", resumed.state.value, "on", resumed.node)


if __name__ == "__main__":
    main()
