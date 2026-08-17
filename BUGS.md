# BUGS

The `admin records-lookup` unmatched-category report (`b03322f1d85e`) was fixed as part of the
systemic structured-error migration in `docs/plans/structured-errors.md`; the application/domain
architecture rule now rejects new bare builtin raises.

## `verification_codes.id` exhausts after 32,767 codes

`VerificationCode.id` is a `SmallInteger` autoincrement primary key
(`squid/accounts/infrastructure/models.py:227`), and no path ever deletes a row:
`replace_verification_code` (`squid/accounts/infrastructure/repository.py:629-652`) only flips
`valid = False` on prior codes and inserts a replacement. The identity sequence therefore climbs
monotonically with every in-game `/link` and stops at 32,767, after which issuing a code fails
outright and Minecraft accounts can no longer be linked at all. Nothing rewinds or reuses the range.

Found while auditing the table for
`docs/plans/pr-183-review/01-consent-verification-ux.md` §1; not a UX matter, so that plan records it
rather than fixing it. Unbounded growth of expired rows is the same root cause and wants the same
answer: widen the key and reap consumed codes.

## `verification_codes.code` has no index

The model declares no `__table_args__`, so every redemption's lookup on the peppered digest
(`repository.py:429-437`) is a sequential scan. Harmless at the table's current size and masked by
the ten-minute expiry, but it compounds with the unbounded growth above, and
`01-consent-verification-ux.md` §1 adds a second lookup per link.
