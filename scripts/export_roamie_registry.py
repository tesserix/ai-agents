from pathlib import Path

import yaml

from roamie_agents.definitions import DEFINITIONS, manager_definition

root = Path("registry/roamie")
root.mkdir(parents=True, exist_ok=True)
for definition in (*DEFINITIONS.values(), manager_definition()):
    agent = definition.agent
    manifest = {
        "apiVersion": "registry.agentic.dev/v1alpha1",
        "kind": "Agent",
        "metadata": {
            "name": f"{agent.name}-agent",
            "namespace": "roamie",
            "tenantId": "roamie",
            "tag": "1.0.5",
            "visibility": "public",
            "labels": {
                "app.kubernetes.io/part-of": "roamie",
                "ai.tesserix.dev/runtime": "tesserix-adk",
                "agent.tesserix.app/gateway-export": "false",
            },
            "annotations": {
                "roamie.tesserix.app/release-state": "candidate",
                "roamie.tesserix.app/definition-revision": definition.revision,
            },
        },
        "spec": {
            "title": agent.name.replace("-", " ").title(),
            "description": agent.instructions.split(".")[0] + ".",
            "model": {"provider": "solo-agentgateway", "name": "roamie-auto"},
            "a2a": {
                "url": f"http://agentgateway-mcp.agentgateway-system.svc.cluster.local:8082/a2a/v1/{agent.name}",
                "capabilities": {"streaming": False, "pushNotifications": False},
                "defaultInputModes": ["application/json"],
                "defaultOutputModes": ["application/json"],
            },
            "definitionVersion": "v1",
            "framework": "tesserix-adk",
            "runtime": {
                "type": "container",
                "protocol": "http",
                "image": (
                    "ghcr.io/tesserix/ai-agents-roamie-manager@sha256:"
                    "c2aad4bd3c8c59ad5eae1c70591ebb8bb4e9d1e83c0ab079d72b077c29025857"
                    if agent.name == "roamie-trip-manager"
                    else "ghcr.io/tesserix/ai-agents-roamie@sha256:"
                    "7deb690266a9a0e558db304966c35a56b52bc4638e010d7f2e25528a60271c6c"
                ),
                "port": 8080,
                "path": (
                    "/v1/trip-manager"
                    if agent.name == "roamie-trip-manager"
                    else f"/a2a/v1/{agent.name}"
                ),
                "healthPath": "/healthz",
            },
            "skills": [{"ref": agent.name, "version": "1.0.5"}],
            "tools": [{"ref": "roamie-travel-search", "version": "1.0.1"}],
            "mcpServers": [{"ref": "roamie-travel-mcp", "version": "1.0.1"}],
        },
    }
    if agent.name == "roamie-trip-manager":
        del manifest["spec"]["a2a"]
        manifest["metadata"]["annotations"]["roamie.tesserix.app/signed-http-path"] = (
            "/v1/trip-manager"
        )
    (root / f"{agent.name}.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))

    skill_dir = root / "skills"
    skill_dir.mkdir(exist_ok=True)
    skill = {
        "apiVersion": manifest["apiVersion"],
        "kind": "Skill",
        "metadata": {**manifest["metadata"], "name": agent.name},
        "spec": {
            "displayName": manifest["spec"]["title"],
            "description": manifest["spec"]["description"],
            "category": "travel",
            "instructions": agent.instructions,
            "tools": [] if agent.name == "roamie-trip-manager" else ["roamie-travel-search"],
            "metadata": {
                "owner": "roamie",
                "managerReviewRequired": True,
                "evaluationSuite": definition.evaluation_suite,
            },
        },
    }
    (skill_dir / f"{agent.name}.yaml").write_text(yaml.safe_dump(skill, sort_keys=False))
    skill_path = Path("skills/roamie") / agent.name
    skill_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text(
        "---\nname: "
        + agent.name
        + "\ndescription: "
        + manifest["spec"]["description"]
        + "\n---\n\n"
        + agent.instructions
        + "\n\nUse only verified MCP evidence. Return through the personal trip manager.\n"
    )


platform = root / "gateway"
platform.mkdir(exist_ok=True)


def resource(kind, name, spec):
    api = "gateway.networking.k8s.io/v1" if kind == "HTTPRoute" else "agentgateway.dev/v1alpha1"
    value = {
        "apiVersion": api,
        "kind": kind,
        "metadata": {"name": name, "namespace": "agentgateway-system"},
        "spec": spec,
    }
    (platform / f"{name}-{kind.lower()}.yaml").write_text(yaml.safe_dump(value, sort_keys=False))


resource(
    "AgentgatewayBackend",
    "roamie-agents",
    {"a2a": {"host": "roamie-agents.roamie.svc.cluster.local", "port": 8080}},
)
resource(
    "HTTPRoute",
    "roamie-agents",
    {
        "parentRefs": [{"name": "agentgateway-mcp", "sectionName": "runtime"}],
        "rules": [
            {
                "matches": [{"path": {"type": "Exact", "value": "/a2a/v1/" + d.agent.name}}],
                "backendRefs": [
                    {
                        "group": "agentgateway.dev",
                        "kind": "AgentgatewayBackend",
                        "name": "roamie-agents",
                    }
                ],
            }
            for d in DEFINITIONS.values()
        ],
    },
)
resource(
    "AgentgatewayPolicy",
    "roamie-agents",
    {
        "targetRefs": [
            {"group": "agentgateway.dev", "kind": "AgentgatewayBackend", "name": "roamie-agents"}
        ],
        "backend": {
            "auth": {
                "secretRef": {"name": "roamie-agent-upstream", "key": "token"},
                "location": {"header": {"name": "Authorization", "prefix": "Bearer "}},
            }
        },
    },
)
resource(
    "AgentgatewayBackend",
    "roamie-model",
    {
        "ai": {
            "groups": [
                {
                    "providers": [
                        {
                            "name": "vertex",
                            "vertexai": {
                                "model": "gemini-3.5-flash",
                                "projectId": "tesseracthub-480811",
                                "region": "global",
                            },
                            "policies": {"auth": {"gcp": {}}, "tls": {}},
                        }
                    ]
                }
            ]
        },
        "policies": {"ai": {"routes": {"/v1/chat/completions": "Completions"}}},
    },
)
resource(
    "HTTPRoute",
    "roamie-model",
    {
        "parentRefs": [{"name": "ai-gateway", "sectionName": "http"}],
        "rules": [
            {
                "matches": [{"path": {"type": "PathPrefix", "value": "/roamie"}}],
                "filters": [
                    {
                        "type": "URLRewrite",
                        "urlRewrite": {
                            "path": {"type": "ReplacePrefixMatch", "replacePrefixMatch": "/"}
                        },
                    }
                ],
                "backendRefs": [
                    {
                        "group": "agentgateway.dev",
                        "kind": "AgentgatewayBackend",
                        "name": "roamie-model",
                    }
                ],
            }
        ],
    },
)
for route, role in [("roamie-model", "roamie.models"), ("roamie-agents", "roamie.manager")]:
    resource(
        "AgentgatewayPolicy",
        route + "-access",
        {
            "targetRefs": [
                {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": route}
            ],
            "traffic": {
                "jwtAuthentication": {
                    "mode": "Strict",
                    "providers": [
                        {
                            "issuer": "https://auth.tesserix.app",
                            "audiences": ["387190457387450503"],
                            "jwks": {
                                "remote": {
                                    "backendRef": {
                                        "group": "agentgateway.dev",
                                        "kind": "AgentgatewayBackend",
                                        "name": "ai-zitadel-jwks",
                                        "port": 443,
                                    },
                                    "jwksPath": "/oauth/v2/keys",
                                    "cacheDuration": "15m",
                                }
                            },
                        }
                    ],
                },
                "authorization": {
                    "action": "Allow",
                    "policy": {
                        "matchExpressions": [
                            '"'
                            + role
                            + '" in jwt["urn:zitadel:iam:org:project:387190457387450503:roles"]'
                        ]
                    },
                },
                "timeouts": {"request": "55s"},
                "rateLimit": {"local": [{"requests": 120, "burst": 16, "unit": "Minutes"}]},
            },
        },
    )
