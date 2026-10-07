# Email assistant constraints

The user explicitly prohibits this assistant from sending email to anyone.

- Never implement or enable sending or forwarding through Microsoft Graph or other email-delivery mechanisms.
- Never request Microsoft Graph Mail.Send permission.
- Microsoft Graph may be used to create new-message drafts and reply drafts, and to update the body of a newly created reply draft. These drafts must remain unsent.
- Create a draft only when the user explicitly asks to create or save one. Reply suggestions otherwise remain text previews for the user to copy manually.
- Do not add other mailbox write actions. The mailbox interface is read and draft creation only.
