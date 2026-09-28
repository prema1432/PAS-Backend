"""Customer portal: the customers' own phone + OTP sign-in and profile view.

Separate from the `customers` module on purpose — that one is the **owner's**
management surface (CRUD, OTP regeneration, recharges) and answers to a Supabase
session. This one is the **customer's** read-only view of themselves, and the
phone + OTP pair is the credential. The two must never share authentication
logic beyond the OTP generator, or a fix to one would silently change the other.
"""
