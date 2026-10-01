# aws-fsx-cross-stack-connectivity

Jenkins pipeline that enables or disables FSx connectivity between AWS CloudFormation stacks using tagged security group rules.

## Background

When a customer's assets are migrated from an old stack to a new one in the same environment, the two stacks' processing servers need temporary access to each other's FSx file systems. Setting that up by hand meant adding security group rules on both sides, creating shared-folder links on both servers, and then remembering to tear it all down afterward, every time.

This pipeline turns that manual process into a single run with two actions: **ENABLE** to open connectivity for the migration window, and **DISABLE** to close it again.

> The code in this repo has been sanitized and generalized from production work. Names and internal references have been replaced, so it is meant to show the approach rather than run as a drop-in tool.

## Repository layout

```
aws-fsx-cross-stack-connectivity/
├── Jenkinsfile            # Pipeline parameters and AWS role assumption
└── fsx_connectivity.py    # Python (boto3) logic for lookups, rules, and symlinks
```

## How it works

The pipeline takes an environment (prod, qa, or uat), an **old** (source) stack number, a **new** (target) stack number, a ticket number, and an action. For each stack, it resolves:

- The **FSx file system**, by its `<environment>-<stack>-fsx` Name tag
- The **security group** attached to the FSx network interfaces
- The **processing server**, through the stack's CloudFormation resources

### ENABLE

1. Adds a bidirectional inbound rule: each stack's security group allows traffic from the other's. Both rules are tagged `TEMP <ticket number>`, so they're easy to trace and clearly marked as temporary.
2. Creates a desktop symlink on each processing server, run through **AWS SSM**, pointing at the other stack's FSx share. The symlinks are named `<environment>_<other stack number>_netshare`.

### DISABLE

Reverses the process in the opposite order: it removes both symlinks first, then both security group rules.

## Design decisions

- **Temporary by design.** The access exists only for the migration window. Every rule is tagged with the ticket number, and DISABLE removes exactly what ENABLE created.
- **Strict lookups.** If any stack number doesn't resolve to a real FSx file system, security group, or processing server, the script stops before making any changes.
- **Tightly scoped.** Only the security groups attached to the two FSx file systems are touched, and no other rules are added, removed, or edited.
- **Idempotent.** Re-running either action skips rules and symlinks that already exist (or are already gone) instead of erroring.
- **Easy to undo.** A mistaken run, such as the wrong stack number, is reversed by re-running with the same parameters and `ACTION=DISABLE`.

## Testing

- Verified ENABLE and DISABLE against both QA and production stack pairs.
- Confirmed that removing the rules actually blocks access to the linked share.
- Confirmed that rule and symlink naming matches the existing manual process.
- Verified idempotency: re-running ENABLE with existing rules skips them and continues to the symlinks.
- Verified that bad input (a nonexistent stack number or mismatched environment) fails before anything is written.

## Notes

- `internal-jenkins-lib` stands in for an internal Jenkins shared library. Its `env_parameters` step resolves the deployment IAM role and AWS account for the selected environment and region, used with `withAWS`.
- `REPO_BRANCH` is used by the Jenkins job's SCM configuration to run the pipeline from `main` or a feature branch.

## Tech

Jenkins (declarative pipeline) · Python 3 · boto3 · AWS FSx, EC2 security groups, SSM, CloudFormation · PowerShell
