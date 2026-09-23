# User password expiry

In User Manager, the Super Admin selects a user, chooses 1, 30, 60 or 90 days under Password Expiry, and saves the user. One day means 24 hours from the last password change. Leaving the password empty preserves its last-change date. Changing the interval recalculates expiry from that date, so shortening it can expire the password immediately. Other account managers cannot change the interval.

The user table shows the expiry date or an expired status. Change Password shows the signed-in user's expiry date. Expiry is enforced at the next sign-in: after verifying the old password, the user must choose a different password of at least 6 characters and confirm it. Cancellation or save failure leaves sign-in blocked. Existing sessions are not automatically ended.

Password changes and administrator password resets restart the selected lifetime. Permission-only edits do not. Existing accounts without password-age metadata receive a one-time 90-day lifetime when loaded by the updated application. Account metadata is saved with the user database and included in backups.

Validation: `python -m unittest test_password_policy.py test_storage_security.py` passed 30 tests, including expiry boundaries, invalid metadata, future timestamps and blocked sign-in after cancelling renewal. Interactive UI validation and MSI rebuilding remain separate steps.
