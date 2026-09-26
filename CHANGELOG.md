# Release notes

## Unreleased

- Mark messages as read or unread with `mailgate-api mark-read <message_id>`
  and `mailgate-api mark-unread <message_id>`.
- Set read status through authenticated `PATCH /v1/messages/{id}` requests
  with `{"is_read": true}` or `{"is_read": false}`. The field requires a
  boolean; missing messages return `404`.
- See read status in CLI list, search, and read output, and the `is_read`
  field in API list and message responses, including masked list entries.
- Listing, searching, and reading messages leave their read status unchanged.
  Use the new commands or endpoint to change it explicitly.
- Read-status changes follow the existing sensitive-message protections:
  security and OTP messages cannot be marked read or unread (`403`).
