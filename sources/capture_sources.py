"""Capture primary-source paths and hashes for the chapters; not needed to build the book.

The upstream checkout lives in sources/repos/OpenSandbox and is git-ignored.
Run: python3 sources/capture_sources.py
"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "sources/repos/OpenSandbox"
COMMIT = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
SUBJECT = subprocess.check_output(
    ["git", "-C", str(REPO), "log", "-1", "--format=%H %ci %s"], text=True
).strip()

MAPPING = {
    "00-preface": ["README.md", "CHARTER.md", "ROADMAP.md"],
    "01-requirements": [
        "docs/guides/secure-container.md",
        "docs/architecture/fast-sandbox/performance.md",
        "docs/architecture/control-plane/operator.md",
        "ROADMAP.md",
    ],
    "02-architecture": [
        "docs/architecture/index.md",
        "server/opensandbox_server/services/factory.py",
        "server/opensandbox_server/services/composite_service.py",
        "manifests/charts/opensandbox/Chart.yaml",
    ],
    "03-contracts": [
        "specs/sandbox-lifecycle.yml",
        "specs/execd-api.yaml",
        "specs/egress-api.yaml",
        "specs/diagnostic-api.yml",
        "docs/api/index.md",
        "docs/api/implementation-compatibility.md",
        "server/opensandbox_server/api/schema.py",
    ],
    "04-lifecycle-server": [
        "server/opensandbox_server/main.py",
        "server/opensandbox_server/startup_guard.py",
        "server/opensandbox_server/api/lifecycle.py",
        "server/opensandbox_server/api/proxy.py",
        "server/opensandbox_server/services/validators.py",
        "docs/architecture/control-plane/server.md",
    ],
    "05-runtime-routing": [
        "server/opensandbox_server/services/sandbox_service.py",
        "server/opensandbox_server/services/factory.py",
        "server/opensandbox_server/services/composite_service.py",
        "server/opensandbox_server/services/runtime_resolver.py",
        "server/opensandbox_server/services/snapshot_restore.py",
    ],
    "06-docker": [
        "server/opensandbox_server/services/docker/networking.py",
        "server/opensandbox_server/services/docker/metadata.py",
        "server/opensandbox_server/services/docker/port_allocator.py",
        "server/opensandbox_server/services/docker/snapshot_runtime.py",
        "docs/architecture/network/single-host-network.md",
    ],
    "07-kubernetes": [
        "kubernetes/apis/sandbox/v1alpha1/batchsandbox_types.go",
        "kubernetes/apis/sandbox/v1alpha1/pool_types.go",
        "kubernetes/apis/sandbox/v1alpha1/sandboxsnapshot_types.go",
        "docs/architecture/control-plane/operator.md",
        "docs/getting-started/configuration.md",
    ],
    "08-fast-sandbox": [
        "docs/architecture/fast-sandbox/index.md",
        "docs/architecture/fast-sandbox/scheduling.md",
        "docs/architecture/fast-sandbox/networking.md",
        "docs/architecture/fast-sandbox/checkpoints.md",
        "docs/architecture/fast-sandbox/storage.md",
        "docs/architecture/fast-sandbox/firecracker.md",
        "docs/architecture/fast-sandbox/performance.md",
    ],
    "09-execd": [
        "components/execd/pkg/web/controller/command.go",
        "components/execd/pkg/web/controller/pty_ws.go",
        "components/execd/pkg/web/model/codeinterpreting.go",
        "docs/architecture/data-plane/execd.md",
        "docs/guides/isolation-sessions.md",
    ],
    "10-ingress": [
        "components/ingress/pkg/proxy/header.go",
        "components/ingress/pkg/proxy/connectivity/tracker.go",
        "docs/architecture/network/ingress.md",
        "docs/guides/secure-access.md",
    ],
    "11-egress": [
        "components/egress/pkg/policy/policy.go",
        "components/egress/pkg/policy/always_rules.go",
        "docs/architecture/network/egress.md",
    ],
    "12-credential-vault": [
        "docs/guides/credential-vault.md",
        "oseps/0012-credential-vault.md",
        "oseps/0022-multi-sandbox-egress-control-plane.md",
        "server/opensandbox_server/api/credential_vault.py",
    ],
    "13-lifecycle-state": [
        "docs/guides/pause-resume.md",
        "docs/guides/client-pool.md",
        "server/opensandbox_server/integrations/idle_pause/decision.py",
        "server/opensandbox_server/integrations/renew_intent/consumer.py",
    ],
    "14-identity-tenancy": [
        "docs/guides/multi-tenancy.md",
        "server/opensandbox_server/middleware/auth.py",
        "server/opensandbox_server/tenants/file_provider.py",
    ],
    "15-isolation": [
        "docs/guides/secure-container.md",
        "docs/guides/network-isolation.md",
        "components/execd/pkg/isolation/",
        "oseps/0004-secure-container-runtime.md",
    ],
    "16-observability": [
        "docs/architecture/data-plane/node-agent.md",
        "docs/guides/telemetry.md",
        "components/nodeagent/pkg/marker/",
        "server/opensandbox_server/api/devops.py",
    ],
    "17-operations": [
        "docs/deployment/index.md",
        "manifests/charts/controller/values.yaml",
        "manifests/charts/server/values.yaml",
        "docs/community/release-verification.md",
    ],
    "18-tradeoffs": [
        "docs/api/implementation-compatibility.md",
        "docs/architecture/fast-sandbox/ha.md",
        "server/opensandbox_server/services/composite_service.py",
        "ROADMAP.md",
    ],
    "19-wrap-up": [
        "docs/architecture/index.md",
        "server/opensandbox_server/api/lifecycle.py",
        "docs/api/index.md",
    ],
    "appendix-map": ["AGENTS.md", "docs/README.md", "specs/README.md"],
}


def digest(path: Path) -> dict:
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
        data = b"".join(p.read_bytes() for p in files)
        return {
            "url": f"https://github.com/opensandbox-group/OpenSandbox/tree/{COMMIT}/{path.relative_to(REPO)}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "lines": sum(len(p.read_bytes().splitlines()) for p in files),
            "files": len(files),
        }
    data = path.read_bytes()
    return {
        "url": f"https://github.com/opensandbox-group/OpenSandbox/blob/{COMMIT}/{path.relative_to(REPO)}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "lines": len(data.splitlines()),
    }


def main() -> None:
    files = {}
    for chapter, paths in MAPPING.items():
        for relative in paths:
            path = REPO / relative
            if not path.exists():
                raise SystemExit(f"missing source for {chapter}: {relative}")
            files[relative] = digest(path)
    inventory = {
        "repository": "https://github.com/opensandbox-group/OpenSandbox",
        "commit": COMMIT,
        "head": SUBJECT,
        "captured": "2026-10-31",
        "files": files,
        "chapters": MAPPING,
        "method": (
            "Static reading of source, OpenAPI contracts, architecture docs, guides and OSEPs. "
            "OpenSandbox was not deployed or executed; no performance or security measurements were made."
        ),
    }
    (ROOT / "sources").mkdir(exist_ok=True)
    (ROOT / "sources/inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Captured {len(files)} source entries at {COMMIT}")


if __name__ == "__main__":
    main()
