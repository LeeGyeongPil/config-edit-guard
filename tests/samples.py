"""Text fixtures shared by the tests.  All paths and names are neutral example values."""

# The probe document: single-quoted name at bytes 109-114, a merge key, a duplicate key.
PROBE_YAML = """# service config
defaults: &defaults
  timeout: 30   # seconds
  retries: 3
service:
  <<: *defaults
  name: 'api'
  enabled: no        # YAML 1.1 bool trap
  mode: 0755
  version: 1.10
  port: 8080
  port: 9090
  tags: [a, b]
"""

PROBE_NODUP = PROBE_YAML.replace("  port: 9090\n", "")

# Rich base document used by the verify tests: comments, anchor + merge, quotes, blank line, ordering.
BASE_YAML = """# service config
defaults: &defaults
  timeout: 30   # seconds
  retries: 3

service:
  <<: *defaults
  name: 'api'
  enabled: "no"
  mode: "0755"
  tags: [a, b]
other:
  host: example.com
  port: 8080
"""

DEPLOY_YAML = """# deployment for the api
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: api
          image: registry.example.com/api:1.4.1   # pinned
          ports:
            - containerPort: 8080
        - name: sidecar
          image: registry.example.com/sidecar:2.0.0
"""

FOUR_SPACE_YAML = """root:
    child:
        - x
        - y
    flow: {x: 1,   y: 2}   # keep spacing
    text: "a very long string"
"""

SETTINGS_JSONC = """{
  // editor basics
  "editor.fontSize": 14, /* keep */
  "http.proxy": "https://proxy.example.com:8080",
  "workbench.colorTheme": "Default Dark Modern",
  "files.exclude": {
    "**/.git": true,
  },
}
"""

PYPROJECT_TOML = """# tool config
[project]
name = "api"   # display name
version = "0.3.0"
dependencies = [ "a>=1",
  "b" ]

[tool.ruff]
line-length = 88

[[bin]]
name = 'first'

[[bin]]
name = 'second'
"""

APP_CSPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project Sdk="Microsoft.NET.Sdk">
  <!-- removed Foo.dll: breaks arm64 build -->
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable   enable='true'>enable</Nullable>
  </PropertyGroup>
</Project>
"""

SKILL_MD = """---
title: Release notes
tags: [a, b]   # keep
draft: false
---

# Release notes

---

Body text with a rule above.
"""
