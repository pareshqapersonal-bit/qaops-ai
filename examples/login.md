# Feature: User Login

## User story
As a registered user, I want to log in with my email and password so that I can
access my account dashboard.

## Acceptance criteria
1. The login form has an email field, a password field, and a "Log in" button.
2. A registered user entering a correct email and password combination is
   redirected to their dashboard.
3. An incorrect email/password combination shows the message
   "Invalid email or password" without revealing which field was wrong.
4. After 5 consecutive failed attempts, the account is temporarily locked.
5. A "Forgot password" link sends a password reset email to the registered
   address.
6. Logged-in users remain logged in until they log out.

## Notes
- Login must be fast.
- Passwords are stored securely.
