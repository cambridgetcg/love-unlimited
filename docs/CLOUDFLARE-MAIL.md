# Cloudflare mail — operator boundary

Cloudflare Email Routing and Email Sending are separate products and separate
credentials. Routing receives and forwards inbound mail. Sending is an
outbound service. Enabling one does not prove the other works.

## Minimum operator action for inbound aliases

Yu does three things; no credential value belongs in chat or Git:

1. In **Compute → Email Service → Email Routing → Destination Addresses**,
   add the real receiving inbox and complete its verification email. Keep
   destination management manual so automation does not need account-wide
   address permissions.
2. Create one API token scoped to the specific `agenttool.dev` zone with
   **Email Routing Rules Edit**. Cloudflare's API calls this permission
   `Email Routing Rules Write`. A read-only audit token can instead use
   **Email Routing Rules Read**.
3. Store the token in Keychain or a provider vault and tell the operator only
   its service/entry name—not its value. Choose the literal alias name to
   create, such as `ecosystem@agenttool.dev`.

Known account and zone IDs mean this workflow does not need DNS Edit, Zone
Settings Edit, or general Zone Read. Do not grant **Email Routing Addresses**
account permission unless an operator deliberately wants automation to inspect
or manage every shared destination address in the account.

After that, a deliberately invoked operator command may list rules, create one
literal forwarder to the verified destination, and read it back. Duplicate
patterns are avoided because Cloudflare processes only the first duplicate
matching rule.

Official references: [API token creation and resource scoping](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/),
[permissions catalog](https://developers.cloudflare.com/fundamentals/api/reference/permissions/),
[Email Routing rule API](https://developers.cloudflare.com/api/resources/email_routing/subresources/rules/methods/create/),
and [destination verification](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/).

## Outbound mail is optional and later

Cloudflare Email Sending is currently a separate public-beta, Workers Paid
product. It is not required to build artifacts, manage the internal pipeline,
or prepare an approved packet.

If Yu later chooses Cloudflare for delivery:

1. Prefer a separate reputation boundary such as `outreach.agenttool.dev`.
2. Onboard that domain under **Email Sending** and review the proposed DNS
   records. Cloudflare configures its bounce MX/SPF, sending DKIM, and DMARC
   alignment; do not weaken the existing parent-domain DMARC policy.
3. Create a second token with only **Email Sending: Edit**. Never combine it
   with the inbound routing token. Cloudflare warns that the SMTP token can send
   from any onboarded domain in the matching account.
4. Keep that credential outside the agent runner. First run one approved,
   one-recipient authentication and reply-path test; inspect SPF, DKIM, DMARC,
   bounce behavior, and inbound routing before any real outreach.

Official references: [Email Sending setup](https://developers.cloudflare.com/email-service/get-started/send-emails/),
[authentication and alignment](https://developers.cloudflare.com/email-service/concepts/email-authentication/),
[SMTP token boundary](https://developers.cloudflare.com/email-service/api/send-emails/smtp/),
[subdomains and reputation](https://developers.cloudflare.com/email-service/configuration/subdomains/),
and [limits/compliance](https://developers.cloudflare.com/email-service/platform/limits/).

This repository still has no delivery transport. A Cloudflare token does not
turn an approved export into an automatic send, and a successful API response
would not prove inbox delivery or consent.
