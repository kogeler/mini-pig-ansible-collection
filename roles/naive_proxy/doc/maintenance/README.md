# naive_proxy maintenance runbooks

These runbooks are procedural instructions for maintainers and agents. They
explain how to change or diagnose the role; behavioural promises belong in
[`../contracts/`](../contracts/README.md).

| Runbook | Use it when |
|---|---|
| [Changing the role](change-workflow.md) | modifying variables, tasks, templates, units, handlers, or tests |
| [Testing](testing.md) | preparing the local environment or selecting/running Molecule coverage |
| [Updating versions](update-versions.md) | refreshing images, Naive, released-SFA sing-box, Go, cronet, or test helpers |
| [Debugging](debugging.md) | investigating local Molecule, CI, ACME, routing, H2, TUN, or production failures |
| [CI debugging](ci-debugging.md) | temporary runner-security experiments, bounded pcaps, and post-failure artifact collection |

Production capture and analysis helpers live in
[`../scripts/`](../scripts/README.md). They are documentation tooling and are
never copied to a target by the role.
