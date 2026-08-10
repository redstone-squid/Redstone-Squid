"""Public provider-neutral account domain API."""

from squid.accounts.domain.models import (
    CONSENT_CUTOFF,
    CURRENT_CONSENT_VERSION,
    MERGE_PROOF_MAX_AGE_SECONDS,
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    IdentityProvider,
    RecentAccountProof,
    VerificationCode,
    normalize_ign,
)

__all__ = [
    "CONSENT_CUTOFF",
    "CURRENT_CONSENT_VERSION",
    "MERGE_PROOF_MAX_AGE_SECONDS",
    "Account",
    "AccountConsent",
    "AccountIdentity",
    "AccountMerge",
    "AliasClaim",
    "ClaimMethod",
    "ClaimStatus",
    "CreatorAlias",
    "CreatorProfile",
    "IdentityProvider",
    "RecentAccountProof",
    "VerificationCode",
    "normalize_ign",
]
