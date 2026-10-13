# Optional - Public App, Private Workspace with Context-Based Ingress

Many organisations keep the workspace private: no workspace UI, notebooks or SQL editor from the internet. They still want the app reachable by external users. **Context-based ingress** (an account-level network policy) does exactly that.

## Two layers

1. **Cloud network layer (precondition).** A Databricks App is served from the workspace's own domain, so the workspace must accept public network traffic. On Azure that means public network access enabled. On its own this does not open the workspace, because the ingress policy below still decides every request.
2. **Context-based ingress policy.** From the public internet, allow only what the app needs:

| Access context | Why |
|---|---|
| **Databricks Apps runtime** | Serves the app itself (`*.databricksapps.com`) |
| **Workspace API `dashboard`** | Loads the embedded dashboard and its results |
| **Workspace API `settings`** | Lets the Databricks sign-in complete on the workspace host |
| Everything else | Private network only |

**The `settings` trap.** Allowing only Apps runtime plus `dashboard` is the most common mistake. The app loads, but sign-in runs on the workspace host (`.../oidc/oauth2/...`) and fails with *"context-based ingress block (CIB): Request does not meet the requirements of the workspace's ingress network policy"*.

## Steps

1. Deploy and test the app first, from inside the corporate network.
2. Enable public network access on the workspace at the cloud layer (portal, Terraform or ARM).
3. As an account admin, create a network policy whose public-access rule allows Apps runtime plus workspace API `dashboard` and `settings`. Leave private access as it is. Start in **dry-run** mode.
4. Attach the policy to the workspace:

```bash
databricks --profile <account-profile> account network-policies create-network-policy-rpc --json @policy.json
databricks --profile <account-profile> account workspace-network-configuration \
  update-workspace-network-option-rpc <workspace-id> \
  --json '{"workspace_id": <workspace-id>, "network_policy_id": "<policy-id>"}'
```

5. Review allowed and denied requests in `system.access.inbound_network`, then switch the policy to **enforced**. Changes take about 10 to 15 minutes to apply.
6. Test from a browser outside the corporate network: the app and dashboard load, and the workspace UI is blocked.

## References

- [Context-based ingress control](https://docs.databricks.com/aws/en/security/network/front-end/context-based-ingress)
