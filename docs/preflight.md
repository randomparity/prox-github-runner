# GitHub Preflight

Run preflight before any Proxmox work:

```bash
ansible-playbook playbooks/preflight.yml
```

Hard failures:

- Target repository is public.
- GitHub rejects the PAT.
- PAT cannot request a repository runner registration token.
- PAT has 7 days or fewer remaining.
- PAT has more than 30 days remaining.
- Runner labels do not include `paper-archives`.
- Workflow jobs can route to the runner without `paper-archives`.
- Unsafe triggers can route to the runner.

Warnings:

- PAT has 14 days or fewer remaining.
- Branch protection or active branch rulesets are missing.
- Required review posture is weak.
- CODEOWNERS coverage is missing.

Warnings do not block solo-developer mode because the repository owner can
bypass review and CODEOWNERS controls.

The inventory default `github_pat_expires_on: "1970-01-01"` is a placeholder
that intentionally fails. Set it to the real fine-grained PAT expiration date
before running preflight against GitHub.
