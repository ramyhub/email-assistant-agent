# Email assistant constraints

The user explicitly prohibits this assistant from sending email to anyone.

- Never implement or enable sending, forwarding, replying through Microsoft Graph, or other email-delivery mechanisms.
- Never request Microsoft Graph Mail.Send permission.
- Reply suggestions are text previews only, for the user to copy and send manually.
- Maintain the mailbox read-only interface. Any future change to this constraint requires an explicit user instruction revising it.
