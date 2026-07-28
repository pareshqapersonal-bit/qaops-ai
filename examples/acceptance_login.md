# Login Feature — Requirements

## REQ-001: User Login
A registered user can log in with their email and password. On success they are
taken to their dashboard. On failure they see an error and remain on the login
page.

## REQ-002: Failed Login Handling
After 5 consecutive failed attempts, the account is locked for 15 minutes. The
user is shown the remaining lockout time.

## REQ-003: Remember Me
A "Remember me" option keeps the user signed in for 30 days on the same device.

## REQ-004: Password Reset
A user who forgot their password can request a reset link sent to their
registered email. The link expires after 1 hour.
